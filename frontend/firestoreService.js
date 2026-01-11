import { auth, db, storage } from "./firebase-config.js";
import { fetchWithAuth } from "./common/fetchWithAuth.js";
import { getApp } from "https://www.gstatic.com/firebasejs/10.12.0/firebase-app.js";
import {
  collection,
  doc,
  getDoc,
  getDocs,
  addDoc,
  updateDoc,
  onSnapshot,
  orderBy,
  query,
  limit,
  where,
  serverTimestamp,
  Timestamp,
} from "https://www.gstatic.com/firebasejs/10.12.0/firebase-firestore.js";
import {
  getDownloadURL,
  ref as storageRef,
} from "https://www.gstatic.com/firebasejs/10.12.0/firebase-storage.js";

let reportsQueryDebugLogged = false;
let reportsIndexNeededLogged = false;
let reportsProjectLogged = false;

function logReportsProjectIdOnce() {
  if (reportsProjectLogged) return;
  let projectId = "(unknown)";
  try {
    projectId = getApp().options?.projectId || "(unknown)";
  } catch (error) {
    console.debug("[firestore] projectId lookup failed", error);
  }
  console.log("[firestore] projectId", projectId);
  reportsProjectLogged = true;
}

function extractCreateIndexUrl(message) {
  const match = String(message || "").match(/https?:\/\/[^\s]+create_composite=[^\s]+/i);
  if (!match) return "";
  return match[0].replace(/[),.;]+$/, "");
}

function chunkArray(arr, size) {
  const chunks = [];
  for (let i = 0; i < arr.length; i += size) {
    chunks.push(arr.slice(i, i + size));
  }
  return chunks;
}

function normalizeDoctorProfile(doctorId, doctorData = {}) {
  return {
    id: doctorId,
    name: doctorData.fullName || doctorData.name || "Doctor",
    hospital: doctorData.hospital || doctorData.hospital_name || doctorData.organization || "Hospital",
    specialty: doctorData.specialty || "",
    contactEmail: doctorData.contactEmail || doctorData.email || "",
    contactPhone: doctorData.contactPhone || doctorData.phone || "",
    raw: doctorData,
  };
}

function toTimestamp(input) {
  if (input instanceof Timestamp) return input;
  if (input instanceof Date) return Timestamp.fromDate(input);
  const ms = typeof input === "number" ? input : Date.parse(input);
  if (!Number.isFinite(ms)) throw new Error("Invalid date");
  return Timestamp.fromMillis(ms);
}

function toMillisValue(input) {
  if (!input) return null;
  if (typeof input === "number") return input;
  if (typeof input === "string") {
    const ms = Date.parse(input);
    return Number.isFinite(ms) ? ms : null;
  }
  if (typeof input.toMillis === "function") return input.toMillis();
  if (typeof input.toDate === "function") return input.toDate().getTime();
  if (typeof input.seconds === "number") return input.seconds * 1000;
  return null;
}

export async function getCurrentUserProfile() {
  try {
    const user = auth?.currentUser;
    if (!user || !db) return null;
    const snap = await getDoc(doc(db, "users", user.uid));
    if (!snap.exists()) return { uid: user.uid };
    return { uid: user.uid, ...snap.data() };
  } catch (error) {
    console.error("[firestore] getCurrentUserProfile error", error);
    return null;
  }
}

export async function getDoctorPatients(doctorId) {
  if (!doctorId || !db) return [];
  try {
    const q = query(
      collection(db, "doctorPatients"),
      where("doctorId", "==", doctorId),
      where("status", "==", "active")
    );
    const snapshot = await getDocs(q);
    return snapshot.docs.map((docSnap) => ({
      id: docSnap.id,
      ...docSnap.data(),
    }));
  } catch (error) {
    console.error("[firestore] getDoctorPatients error", error);
    return [];
  }
}

export async function getPatientsByIds(patientIds = []) {
  if (!Array.isArray(patientIds) || !patientIds.length || !db) return [];
  try {
    const chunks = chunkArray(patientIds, 10);
    const results = [];
    for (const chunk of chunks) {
      const q = query(collection(db, "users"), where("__name__", "in", chunk));
      const snap = await getDocs(q);
      snap.forEach((docSnap) => {
        results.push({ id: docSnap.id, ...docSnap.data() });
      });
    }
    return results;
  } catch (error) {
    console.error("[firestore] getPatientsByIds error", error);
    return [];
  }
}

export async function getScreeningsByPatient(patientId) {
  if (!patientId || !db) return [];
  try {
    const q = query(
      collection(db, "screenings"),
      where("patientId", "==", patientId),
      orderBy("completedAt", "desc")
    );
    const snap = await getDocs(q);
    return snap.docs.map((docSnap) => ({
      id: docSnap.id,
      ...docSnap.data(),
    }));
  } catch (error) {
    console.error("[firestore] getScreeningsByPatient error", error);
    return [];
  }
}

/**
 * Get reports via API (with Firebase Auth).
 * Falls back to direct Firestore query if API fails.
 */
export async function getReportsByPatient(patientId) {
  if (!patientId) return [];
  
  // Try API first (with auth)
  try {
    // Dynamic import to avoid circular dependencies
    let fetchWithAuth;
    try {
      // Try common/fetchWithAuth.js first (for non-screening pages)
      const module = await import("./common/fetchWithAuth.js");
      fetchWithAuth = module.fetchWithAuth;
    } catch (importError) {
      // If common/fetchWithAuth.js doesn't exist, try patient/screening.auth.js
      try {
        const module = await import("./patient/screening.auth.js");
        fetchWithAuth = module.fetchWithAuth;
      } catch (importError2) {
        // If both fail, try to use window.fetchWithAuth if available
        if (typeof window !== "undefined" && window.fetchWithAuth) {
          fetchWithAuth = window.fetchWithAuth;
        } else {
          console.warn("[firestore] Could not import fetchWithAuth, using direct Firestore");
          fetchWithAuth = null;
        }
      }
    }
    
    if (fetchWithAuth) {
      const response = await fetchWithAuth("/api/reports");
      if (response.ok) {
        const data = await response.json();
        if (data.ok && Array.isArray(data.reports)) {
          console.log("[firestore] Loaded reports via API:", data.reports.length);
          return data.reports;
        }
      } else if (response.status === 401 || response.status === 403) {
        console.warn("[firestore] API returned auth error, falling back to direct Firestore");
      }
    }
  } catch (apiError) {
    console.warn("[firestore] API call failed, falling back to direct Firestore:", apiError);
  }
  
  // Fallback to direct Firestore query
  if (!db) return [];
  logReportsProjectIdOnce();

  // Reports schema (source of truth):
  // reports/{reportId}: patientId, doctorId, reportId, screeningId, riskLevel, createdAt.
  // Optional: pdfPath, contentText, updatedAt.
  const collectionName = "reports";
  const whereField = "patientId";
  const orderField = "createdAt";
  const orderDirection = "desc";
  const maxLimit = 50;
  const baseRef = collection(db, collectionName);

  function isIndexError(error) {
    const message = String(error?.message || "").toLowerCase();
    return error?.code === "failed-precondition" || message.includes("requires an index");
  }

  function logQueryConstraints() {
    console.log("[firestore] reports query", {
      collection: collectionName,
      where: [{ field: whereField, op: "==", value: patientId }],
      orderBy: { field: orderField, direction: orderDirection },
      limit: maxLimit,
    });
  }

  function debugQueryFailureOnce(error) {
    if (reportsQueryDebugLogged) return;
    reportsQueryDebugLogged = true;
    console.debug(
      "[firestore] reports query failed",
      {
        collection: collectionName,
        where: [{ field: whereField, op: "==", value: patientId }],
        orderBy: { field: orderField, direction: orderDirection },
        limit: maxLimit,
        code: error?.code,
        message: error?.message,
      },
      error
    );
  }

  function handleIndexError(error) {
    if (reportsIndexNeededLogged) return;
    const url = extractCreateIndexUrl(error?.message || "");
    const output = url || "(missing create index url)";
    console.error(`[INDEX_NEEDED] ${output}`);
    reportsIndexNeededLogged = true;
  }

  async function runQuery() {
    logQueryConstraints();
    // Firestore composite index required:
    // collection: reports, fields: patientId (ASC), createdAt (DESC).
    const q = query(
      baseRef,
      where(whereField, "==", patientId),
      orderBy(orderField, orderDirection),
      limit(maxLimit)
    );
    const snap = await getDocs(q);
    return snap.docs.map((docSnap) => ({
      id: docSnap.id,
      __collection: collectionName,
      __field: whereField,
      ...docSnap.data(),
    }));
  }

  try {
    return await runQuery();
  } catch (error) {
    if (isIndexError(error)) {
      handleIndexError(error);
      throw error;
    }
    debugQueryFailureOnce(error);
  }
  return [];
}

export async function getReportPdfUrl(reportId, pdfPath) {
  if (!pdfPath || !storage) return null;
  try {
    const pdfRef = storageRef(storage, pdfPath);
    return await getDownloadURL(pdfRef);
  } catch (error) {
    console.error("[storage] getReportPdfUrl error", error);
    return null;
  }
}

export async function getAppointmentsByDoctor(doctorId) {
  if (!doctorId || !db) return [];
  try {
    const q = query(
      collection(db, "appointments"),
      where("doctorId", "==", doctorId),
      where("status", "==", "scheduled"),
      orderBy("scheduledAt", "asc")
    );
    const snap = await getDocs(q);
    return snap.docs.map((docSnap) => ({
      id: docSnap.id,
      ...docSnap.data(),
    }));
  } catch (error) {
    console.error("[firestore] getAppointmentsByDoctor error", error);
    return [];
  }
}

export async function getChatsByDoctor(doctorId) {
  if (!doctorId || !db) return [];
  try {
    const q = query(
      collection(db, "chats"),
      where("doctorId", "==", doctorId),
      where("status", "==", "active"),
      orderBy("createdAt", "desc")
    );
    const snap = await getDocs(q);
    return snap.docs.map((docSnap) => ({
      id: docSnap.id,
      ...docSnap.data(),
    }));
  } catch (error) {
    console.error("[firestore] getChatsByDoctor error", error);
    return [];
  }
}

export function subscribeDoctorPatients(doctorId, onUpdate) {
  if (!doctorId || !db || typeof onUpdate !== "function") return () => {};
  try {
    const q = query(
      collection(db, "doctorPatients"),
      where("doctorId", "==", doctorId),
      where("status", "==", "active")
    );
    return onSnapshot(
      q,
      (snapshot) => {
        const items = snapshot.docs.map((docSnap) => ({
          id: docSnap.id,
          ...docSnap.data(),
        }));
        onUpdate(items);
      },
      (error) => {
        console.error("[firestore] subscribeDoctorPatients error", error);
        onUpdate([]);
      }
    );
  } catch (error) {
    console.error("[firestore] subscribeDoctorPatients setup error", error);
    return () => {};
  }
}

export async function getUserProfile(uid) {
  if (!uid || !db) return null;
  try {
    const snap = await getDoc(doc(db, "users", uid));
    if (!snap.exists()) return null;
    return { uid, ...snap.data() };
  } catch (error) {
    console.error("[firestore] getUserProfile error", error);
    throw error;
  }
}

export async function getDoctorById(doctorId) {
  if (!doctorId || !db) return null;
  try {
    const snap = await getDoc(doc(db, "users", doctorId));
    if (!snap.exists()) return null;
    return normalizeDoctorProfile(doctorId, snap.data());
  } catch (error) {
    console.error("[firestore] getDoctorById error", error);
    throw error;
  }
}

export async function getAssignedDoctorForCurrentUser() {
  const user = auth?.currentUser;
  if (!user || !db) throw new Error("Not authenticated");

  try {
    const patientSnap = await getDoc(doc(db, "users", user.uid));
    const patientData = patientSnap.exists() ? patientSnap.data() : {};
    const doctorId = patientData.assignedDoctorId || patientData.doctorId || "";

    if (!doctorId) {
      return {
        patientId: user.uid,
        doctorId: "",
        patientProfile: { uid: user.uid, ...patientData },
        doctorProfile: null,
      };
    }

    const doctorSnap = await getDoc(doc(db, "users", doctorId));
    const doctorData = doctorSnap.exists() ? doctorSnap.data() : {};
    const doctorProfile = normalizeDoctorProfile(doctorId, doctorData);

    return {
      patientId: user.uid,
      doctorId,
      patientProfile: { uid: user.uid, ...patientData },
      doctorProfile,
    };
  } catch (error) {
    console.error("[firestore] getAssignedDoctorForCurrentUser error", error);
    throw error;
  }
}

export async function createAppointment({ patientId, doctorId, scheduledAt }) {
  if (!patientId || !doctorId || !db) throw new Error("Missing patientId or doctorId");
  let scheduledAtMs;
  if (scheduledAt instanceof Date) {
    scheduledAtMs = scheduledAt.getTime();
  } else if (typeof scheduledAt === "number") {
    scheduledAtMs = scheduledAt;
  } else if (typeof scheduledAt === "string") {
    scheduledAtMs = new Date(scheduledAt).getTime();
  }
  if (!Number.isFinite(scheduledAtMs)) throw new Error("Invalid scheduledAt");

  let doctorName = "Doctor";
  let hospitalName = "Hospital";
  try {
    const doctorSnap = await getDoc(doc(db, "users", doctorId));
    if (doctorSnap.exists()) {
      const doctorData = doctorSnap.data() || {};
      doctorName = doctorData.fullName || doctorData.name || doctorName;
      hospitalName = doctorData.hospital || doctorData.hospital_name || doctorData.organization || hospitalName;
    }
  } catch {
    /* ignore fetch failure */
  }

  const scheduledAtIso = new Date(scheduledAtMs).toISOString();
  const payload = {
    patientId,
    doctorId,
    doctorName,
    hospitalName,
    scheduledAt: scheduledAtIso,
  };

  try {
    const response = await fetchWithAuth("/api/appointments", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify(payload),
    });
    let data = null;
    try {
      data = await response.json();
    } catch {
      data = null;
    }
    if (!response.ok || !data?.ok) {
      const err = new Error(data?.message || "Failed to book appointment");
      err.code = data?.error || `HTTP_${response.status}`;
      throw err;
    }
    return {
      id: data.appointmentId,
      appointmentId: data.appointmentId,
      scheduledAt: scheduledAtIso,
      slotStartAt: data.slotStartAt || scheduledAtIso,
      status: "scheduled",
      doctorId,
      doctorName,
      hospitalName,
    };
  } catch (error) {
    console.error("[firestore] createAppointment error", error);
    throw error;
  }
}

export async function cancelAppointment(appointmentId, reason = "") {
  if (!appointmentId || !db) throw new Error("Missing appointmentId");
  const trimmedReason = String(reason || "").trim();
  if (!trimmedReason) throw new Error("Cancellation reason is required");

  try {
    console.log("[api] cancel appointmentId =", appointmentId);
    const url = `/api/appointments/${appointmentId}/cancel`;
    console.log("[api] cancel url =", url);
    const response = await fetchWithAuth(url, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ reason: trimmedReason }),
    });
    if (!response.ok) {
      const text = await response.text();
      console.error("[api] cancel failed", response.status, text);
      throw new Error(text || `HTTP ${response.status}`);
    }
    let data = null;
    try {
      data = await response.json();
    } catch {
      data = null;
    }
    if (!data?.ok) {
      const err = new Error(data?.message || "Failed to cancel appointment");
      err.code = data?.error || data?.code || `HTTP_${response.status}`;
      throw err;
    }
    return { id: appointmentId };
  } catch (error) {
    console.error("[firestore] cancelAppointment error", error);
    throw error;
  }
}

export async function expirePastScheduledAppointments(patientId) {
  if (!patientId || !db) return { updated: 0 };
  try {
    const q = query(
      collection(db, "appointments"),
      where("patientId", "==", patientId),
      where("status", "==", "scheduled")
    );
    const snap = await getDocs(q);
    const nowMs = Date.now();
    let updated = 0;

    for (const docSnap of snap.docs) {
      const data = docSnap.data() || {};
      const scheduledMs = toMillisValue(data.scheduledAt);
      if (!Number.isFinite(scheduledMs)) continue;
      if (scheduledMs < nowMs) {
        await updateDoc(doc(db, "appointments", docSnap.id), {
          status: "expired",
          expiredAt: serverTimestamp(),
          updatedAt: serverTimestamp(),
          expireReason: "scheduled time passed",
        });
        updated += 1;
      }
    }

    return { updated };
  } catch (error) {
    console.error("[firestore] expirePastScheduledAppointments error", error);
    throw error;
  }
}

export async function getUpcomingAppointments(patientId, doctorId) {
  if (!patientId || !db) return [];
  try {
    const response = await fetchWithAuth("/api/appointments");
    const data = await response.json();
    const isIndexWarning = data?.code === "MISSING_INDEX" && (Array.isArray(data.upcoming) || Array.isArray(data.all));
    if (!response.ok && !isIndexWarning) {
      const err = new Error(data?.message || "Failed to load appointments");
      err.code = data?.error || data?.code || `HTTP_${response.status}`;
      throw err;
    }
    const upcoming = Array.isArray(data?.upcoming) ? data.upcoming : Array.isArray(data?.all) ? data.all : [];
    const nowMs = Date.now();
    return upcoming
      .map((appt) => ({ ...appt, id: appt.id || "" }))
      .filter((appt) => {
        const status = String(appt.status || "").toLowerCase();
        if (!["scheduled", "pending", "upcoming", "active"].includes(status)) return false;
        const scheduledMs = toMillisValue(appt.scheduledAt);
        return Number.isFinite(scheduledMs) && scheduledMs >= nowMs;
      })
      .sort((a, b) => {
        const aMs = toMillisValue(a.scheduledAt) || 0;
        const bMs = toMillisValue(b.scheduledAt) || 0;
        return aMs - bMs;
      })
      .slice(0, 20);
  } catch (error) {
    console.error("[firestore] getUpcomingAppointments error", error);
    throw error;
  }
}

export async function getAppointmentHistory(patientId, doctorId) {
  if (!patientId || !db) return [];
  try {
    const response = await fetchWithAuth("/api/appointments");
    const data = await response.json();
    const isIndexWarning = data?.code === "MISSING_INDEX" && (Array.isArray(data.history) || Array.isArray(data.all));
    if (!response.ok && !isIndexWarning) {
      const err = new Error(data?.message || "Failed to load appointments");
      err.code = data?.error || data?.code || `HTTP_${response.status}`;
      throw err;
    }
    const history = Array.isArray(data?.history) ? data.history : Array.isArray(data?.all) ? data.all : [];
    return history
      .map((appt) => ({ ...appt, id: appt.id || "" }))
      .filter((appt) => {
        const status = String(appt.status || "").toLowerCase();
        return ["completed", "cancelled", "expired"].includes(status);
      })
      .sort((a, b) => {
        const aMs = toMillisValue(a.scheduledAt) || 0;
        const bMs = toMillisValue(b.scheduledAt) || 0;
        return bMs - aMs;
      })
      .slice(0, 50);
  } catch (error) {
    console.error("[firestore] getAppointmentHistory error", error);
    throw error;
  }
}

export async function getLastMedicalAdvice(patientId, doctorId) {
  if (!patientId || !db) return null;
  const baseConstraints = [where("patientId", "==", patientId), where("status", "==", "completed")];

  async function runQuery(orderField) {
    const q = query(collection(db, "appointments"), ...baseConstraints, orderBy(orderField, "desc"), limit(1));
    const snap = await getDocs(q);
    if (!snap.size) return null;
    const docSnap = snap.docs[0];
    return { id: docSnap.id, ...docSnap.data() };
  }

  try {
    const byCompleted = await runQuery("completedAt");
    if (byCompleted) return byCompleted;
    return await runQuery("scheduledAt");
  } catch (error) {
    console.error("[firestore] getLastMedicalAdvice error", error);
    throw error;
  }
}

export async function createAppointmentFixedDoctor({ doctorId, scheduledAtMillis }) {
  const user = auth?.currentUser;
  if (!user) throw new Error("Not authenticated");
  return createAppointment({ patientId: user.uid, doctorId, scheduledAt: scheduledAtMillis });
}

// Backward compatibility for existing imports.
export { getDoctorPatients as getDoctorPatientLinks };
