import { initializeApp, getApps } from "https://www.gstatic.com/firebasejs/10.12.0/firebase-app.js";
import { getAuth } from "https://www.gstatic.com/firebasejs/10.12.0/firebase-auth.js";
import { getFirestore } from "https://www.gstatic.com/firebasejs/10.12.0/firebase-firestore.js";
import { getStorage } from "https://www.gstatic.com/firebasejs/10.12.0/firebase-storage.js";

// Firebase project configuration copied from the console.
const firebaseConfig = {
  apiKey: "AIzaSyCYt0RljpF3u840xlaUZG8YvKRtHUWJHKw",
  authDomain: "feiai-7c59e.firebaseapp.com",
  projectId: "feiai-7c59e",
  storageBucket: "feiai-7c59e.firebasestorage.app",
  messagingSenderId: "201087377012",
  appId: "1:201087377012:web:034c36f6998c65c2f77b21",
  measurementId: "G-2PLVVED844",
};

export const app = getApps().length === 0 ? initializeApp(firebaseConfig) : getApps()[0];
export const auth = getAuth(app);
export const db = getFirestore(app);
export const storage = getStorage(app);

const firestoreSettings = db && db._settings ? db._settings : {};
const firestoreHost = firestoreSettings.host || "(default)";
const firestoreEmulator = /^(localhost|127\.0\.0\.1)/.test(firestoreHost);
console.log("[firebase] Firestore host:", firestoreHost, "emulator:", firestoreEmulator);
