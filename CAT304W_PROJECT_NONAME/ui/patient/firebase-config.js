import { app, auth, db, storage } from "../firebase-config.js";

const firebaseConfig = {
  apiKey: "AIzaSyCYt0RljpF3u840xlaUZG8YvKRtHUWJHKw",
  authDomain: "feiai-7c59e.firebaseapp.com",
  projectId: "feiai-7c59e",
  storageBucket: "feiai-7c59e.firebasestorage.app",
  messagingSenderId: "201087377012",
  appId: "1:201087377012:web:034c36f6998c65c2f77b21",
  measurementId: "G-2PLVVED844",
};

(function () {
  if (typeof window !== "undefined") {
    window.firebaseConfig = firebaseConfig;
  }
  if (typeof firebase !== "undefined") {
    if (!firebase.apps || !firebase.apps.length) {
      firebase.initializeApp(firebaseConfig);
    }
  }
})();

export { app, auth, db, storage };
