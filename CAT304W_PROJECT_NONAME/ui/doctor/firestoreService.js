import { auth, db } from "./firebase-config.js";
import {
  collection,
  doc,
  getDoc,
  getDocs,
  onSnapshot,
  query,
  where,
} from "https://www.gstatic.com/firebasejs/10.12.0/firebase-firestore.js";

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
    console.error("[doctorPatients] getCurrentUserProfile error", error);
    return null;
  }
}

export async function getDoctorPatientLinks(doctorId) {
  if (!doctorId || !db) return [];
  try {
    const q = query(collection(db, "doctorPatients"), where("doctorId", "==", doctorId));
    const snapshot = await getDocs(q);
    const links = [];
    snapshot.forEach((docSnap) => {
      const data = docSnap.data() || {};
      const status = (data.status || "active").toLowerCase();
      links.push({
        id: docSnap.id,
        doctorId: data.doctorId,
        patientId: data.patientId,
        status,
        assignedAt: data.assignedAt,
      });
    });
    return links.filter((link) => !link.status || link.status === "active");
  } catch (error) {
    console.error("[doctorPatients] getDoctorPatientLinks error", error);
    return [];
  }
}

export async function getPatientsByIds(patientIds = []) {
  if (!Array.isArray(patientIds) || patientIds.length === 0 || !db) return [];
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
    console.error("[doctorPatients] getPatientsByIds error", error);
    return [];
  }
}

export function subscribeDoctorPatients(doctorId, onUpdate) {
  if (!doctorId || !db || typeof onUpdate !== "function") return () => {};
  try {
    const q = query(collection(db, "doctorPatients"), where("doctorId", "==", doctorId));
    const unsub = onSnapshot(
      q,
      (snapshot) => {
        const links = [];
        snapshot.forEach((docSnap) => {
          const data = docSnap.data() || {};
          const status = (data.status || "active").toLowerCase();
          links.push({
            id: docSnap.id,
            doctorId: data.doctorId,
            patientId: data.patientId,
            status,
            assignedAt: data.assignedAt,
          });
        });
        const activeLinks = links.filter((link) => !link.status || link.status === "active");
        onUpdate(activeLinks);
      },
      (error) => {
        console.error("[doctorPatients] subscribeDoctorPatients error", error);
        onUpdate([]);
      }
    );
    return unsub;
  } catch (error) {
    console.error("[doctorPatients] subscribeDoctorPatients setup error", error);
    return () => {};
  }
}
