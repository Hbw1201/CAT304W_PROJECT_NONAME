import { auth, db, storage } from "./firebase-config.js";
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

export async function getReportsByPatient(patientId) {
  if (!patientId || !db) return [];
  try {
    // Compatibility layer for legacy collections/fields and missing orderBy indexes.
    const collections = ["reports", "riskReports", "screeningReports"];
    const fields = ["patientId", "uid", "userId", "ownerUid"];
    // Try orderBy when available; fall back to unordered to avoid index errors.
    const orderFields = ["createdAt", "created_at", null];
    let lastError = null;

    async function runQuery(collectionName, fieldName, orderField) {
      const baseRef = collection(db, collectionName);
      const constraints = [where(fieldName, "==", patientId)];
      if (orderField) constraints.push(orderBy(orderField, "desc"));
      constraints.push(limit(50));
      const q = query(baseRef, ...constraints);
      const snap = await getDocs(q);
      return snap.docs.map((docSnap) => ({
        id: docSnap.id,
        __collection: collectionName,
        __field: fieldName,
        ...docSnap.data(),
      }));
    }

    for (const collectionName of collections) {
      for (const fieldName of fields) {
        for (const orderField of orderFields) {
          try {
            console.info("[firestore] reports query", {
              collection: collectionName,
              field: fieldName,
              orderBy: orderField || "(none)",
            });
            const rows = await runQuery(collectionName, fieldName, orderField);
            if (rows.length) {
              console.info("[firestore] reports loaded", {
                count: rows.length,
                collection: collectionName,
                field: fieldName,
                orderBy: orderField || "(none)",
              });
              return rows;
            }
          } catch (error) {
            lastError = error;
            console.warn("[firestore] reports query failed", {
              collection: collectionName,
              field: fieldName,
              orderBy: orderField || "(none)",
              code: error?.code,
              message: error?.message,
            });
          }
        }
      }
    }

    if (lastError) throw lastError;
    return [];
  } catch (error) {
    throw error;
  }
}

export async function getReportPdfUrl(reportId) {
  if (!reportId || !storage) return null;
  try {
    const pdfRef = storageRef(storage, `reports/${reportId}.pdf`);
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
  const ts = toTimestamp(scheduledAt);

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

  const payload = {
    patientId,
    doctorId,
    doctorName,
    hospitalName,
    scheduledAt: ts,
    status: "scheduled",
    createdAt: serverTimestamp(),
    updatedAt: serverTimestamp(),
  };

  try {
    const ref = await addDoc(collection(db, "appointments"), payload);
    const snap = await getDoc(ref);
    return { id: ref.id, ...(snap.exists() ? snap.data() : payload) };
  } catch (error) {
    console.error("[firestore] createAppointment error", error);
    throw error;
  }
}

export async function cancelAppointment(appointmentId, reason = "") {
  const userId = auth?.currentUser?.uid || null;
  if (!appointmentId || !db) throw new Error("Missing appointmentId");

  const ref = doc(db, "appointments", appointmentId);
  const snap = await getDoc(ref);
  if (!snap.exists()) throw new Error("Appointment not found");

  const data = snap.data() || {};
  if (userId && data.patientId && data.patientId !== userId) {
    throw new Error("Forbidden");
  }
  if (data.status !== "scheduled") throw new Error("Only scheduled appointments can be cancelled");

  try {
    await updateDoc(ref, {
      status: "cancelled",
      cancelReason: reason || "",
      cancelledAt: serverTimestamp(),
      updatedAt: serverTimestamp(),
    });
    const updated = await getDoc(ref);
    return { id: ref.id, ...(updated.exists() ? updated.data() : {}) };
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
    const now = new Date();
    const constraints = [
      where("patientId", "==", patientId),
      where("status", "==", "scheduled"),
      where("scheduledAt", ">=", now),
      orderBy("scheduledAt", "asc"),
      limit(20),
    ];
    const q = query(collection(db, "appointments"), ...constraints);
    const snap = await getDocs(q);
    return snap.docs.map((docSnap) => ({ id: docSnap.id, ...docSnap.data() }));
  } catch (error) {
    console.error("[firestore] getUpcomingAppointments error", error);
    throw error;
  }
}

export async function getAppointmentHistory(patientId, doctorId) {
  if (!patientId || !db) return [];
  try {
    const constraints = [
      where("patientId", "==", patientId),
      where("status", "in", ["completed", "cancelled", "expired"]),
      orderBy("scheduledAt", "desc"),
      limit(50),
    ];
    const q = query(collection(db, "appointments"), ...constraints);
    const snap = await getDocs(q);
    return snap.docs.map((docSnap) => ({ id: docSnap.id, ...docSnap.data() }));
  } catch (error) {
    const msg = String(error?.message || "").toLowerCase();
    if (error?.code === "failed-precondition" || msg.includes("requires an index")) {
      console.error("[firestore] missing index: appointments: patientId+status+scheduledAt(desc)");
    }
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
