// firebase-config.js

// 1. 从 CDN 引入 Firebase 模块
import { initializeApp } from "https://www.gstatic.com/firebasejs/10.12.0/firebase-app.js";
import { getAuth } from "https://www.gstatic.com/firebasejs/10.12.0/firebase-auth.js";
import { getFirestore } from "https://www.gstatic.com/firebasejs/10.12.0/firebase-firestore.js";
import { getStorage } from "https://www.gstatic.com/firebasejs/10.12.0/firebase-storage.js";

// 2. 你的项目的真实配置（从控制台 Web 应用复制的这一段）
const firebaseConfig = {
  apiKey: "AIzaSyCYt0RljpF3u840xlaUZG8YvKRtHUWJHKw",
  authDomain: "feiai-7c59e.firebaseapp.com",
  projectId: "feiai-7c59e",
  storageBucket: "feiai-7c59e.firebasestorage.app",
  messagingSenderId: "201087377012",
  appId: "1:201087377012:web:034c36f6998c65c2f77b21",
  measurementId: "G-2PLVVED844"
};

// 3. 初始化 Firebase
const app = initializeApp(firebaseConfig);

// 4. 导出实例，给其他文件用
export const auth = getAuth(app);
export const db = getFirestore(app);
export const storage = getStorage(app);
