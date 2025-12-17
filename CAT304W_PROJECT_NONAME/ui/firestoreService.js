import { auth, db, storage } from "./firebase-config.js";
import {
  collection,
  doc,
  getDoc,
  getDocs,
  onSnapshot,
  orderBy,
  query,
  limit,
  where,
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
    const q = query(
      collection(db, "reports"),
      where("patientId", "==", patientId),
      orderBy("createdAt", "desc"),
      limit(50)
    );
    const snap = await getDocs(q);
    return snap.docs.map((docSnap) => ({
      id: docSnap.id,
      ...docSnap.data(),
    }));
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

// Backward compatibility for existing imports.
export { getDoctorPatients as getDoctorPatientLinks };
