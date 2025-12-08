# Osler AI / MAQ-SCREEN Web UI | English & 中文

## Overview / 项目简介
Osler AI (MAQ-SCREEN) is a static web UI for a telehealth-style AI assistant that handles onboarding, screening questionnaires, risk feedback, and report viewing. The frontend is pure HTML/CSS/JS with Firebase Auth + Firestore for identity and profile storage.  
Osler AI（MAQ-SCREEN）是一个远程医疗风格的 AI 助手前端，涵盖登录注册、筛查问卷、风险反馈和报告查看。界面基于原生 HTML/CSS/JS，并通过 Firebase Auth + Firestore 管理身份与档案。

## Tech Stack / 技术栈
- HTML5 + CSS (`style.css`, `login.css`, `dashboard.css`) for layout and visuals.
- Vanilla JavaScript modules (`script.js`) plus Firebase CDN SDKs for auth, Firestore, and storage.
- Optional MySQL schema (`mysql_schema.sql`) for backend reference.
- Static assets in `resource/` and npm metadata (`package.json`, `package-lock.json`) for dependency tracking.

## Screens & Files / 页面与文件
- `index.html`: Landing page with tabbed hero and privacy/legal modals; “Start” routes to `login.html`.
- `login.html`, `register.html`, `forgot.html`: Auth flows hooked to Firebase; registration writes user profile to Firestore.
- `dashboard.html`: Post-login hub styled by `dashboard.css`.
- Feature pages: `appointment.html`, `chatbot.html`, `screening.html`, `question.html`, `report.html`, `profile.html`, `services.html`, `about.html`, `contact.html` for scheduling, Q&A, screening, reports, profile, and marketing content.
- `firebase-config.js`: Firebase project credentials; exports `auth`, `db`, `storage`.

## Quick Start / 快速开始
1. Install deps (optional but keeps npm metadata consistent) / 可选：安装依赖  
   ```bash
   npm install
   ```
2. Update Firebase config in `firebase-config.js` with your project keys (Console → Project settings → Web app).  
   将 `firebase-config.js` 中的配置替换为自己的 Firebase 项目参数。
3. Run a static server from the project root / 在项目根目录启动静态服务  
   ```bash
   npx serve .
   # or / 或
   python -m http.server 5173
   ```
4. Visit `http://localhost:3000` (or your chosen port) and use the landing “Start” button to begin.  
   在浏览器打开本地域名，点击首页 “Start” 进入流程。

## Firebase Notes / Firebase 说明
- Auth: `login.html` and `register.html` call Firebase Auth; `register` writes a `users/{uid}` document with `{ uid, name, email, age, gender, riskLevel, createdAt }`.
- Firestore/Storage: `firebase-config.js` exports shared instances; extend usage in feature pages as needed.
- If Firebase is not configured, `script.js` will alert “Firebase 未配置”; add valid credentials before testing auth.

## Optional Data Layer / 可选数据层
`mysql_schema.sql` contains a starter schema for a Node.js/MySQL backend if you prefer relational storage alongside the Firebase demo.  
`mysql_schema.sql` 提供了 Node.js + MySQL 的基础表结构，可与 Firebase 示例并行参考。

## Deployment / 部署
Because assets are static, any static host (Firebase Hosting, Vercel, Netlify, Nginx, S3) works. Ensure HTTPS so Firebase SDKs load without mixed-content issues.  
前端为纯静态资源，可部署到任意静态托管；请使用 HTTPS 以保证 Firebase SDK 正常加载。
