# Osler AI / MAQ-SCREEN Web UI | English & 中文

## Overview / 项目简介
Osler AI (MAQ-SCREEN) is a static web UI for a telehealth-style AI assistant that handles onboarding, screening questionnaires, risk feedback, and report viewing. The frontend is pure HTML/CSS/JS with Firebase Auth + Firestore for identity and profile storage.  
Osler AI（MAQ-SCREEN）是一个远程医疗风格的 AI 助手前端，涵盖登录注册、筛查问卷、风险反馈和报告查看。界面基于原生 HTML/CSS/JS，并通过 Firebase Auth + Firestore 管理身份与档案。

## Tech Stack / 技术栈
- HTML5 + CSS (`style.css`, `login.css`, `dashboard.css`) for layout and visuals.
- Vanilla JavaScript modules (`script.js`) plus Firebase CDN SDKs for auth, Firestore, and storage.
- Optional MySQL schema (`mysql_schema.sql`) for backend reference.
- Static assets live in `ui/` (HTML/CSS/JS plus `resource/`); npm metadata (`package.json`, `package-lock.json`) tracks dependencies.

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
2. Update Firebase config in `firebase-config.js` with your project keys (Console -> Project settings -> Web app).  
   将 `firebase-config.js` 中的配置替换为自己的 Firebase 项目参数。
3. Serve the UI from `ui/` (or run the Node proxy) / 在 `ui/` 目录中启动静态服务（或使用 Node 代理）  
   ```bash
   npx serve ui
   # or / 或
   cd ui && python -m http.server 5173
   # or start the Express API + static host / 或运行 Express API + 前端
   npm start
   ```
4. Visit `http://localhost:3000` (or your chosen port) and use the landing "Start" button to begin.  
   在浏览器打开本地域名，点击首页 "Start" 进入流程。

## Firebase Notes / Firebase 说明
- Auth: `login.html` and `register.html` call Firebase Auth; `register` writes a `users/{uid}` document with `{ uid, name, email, age, gender, riskLevel, createdAt }`.
- Firestore/Storage: `firebase-config.js` exports shared instances; extend usage in feature pages as needed.
- If Firebase is not configured, `script.js` will alert “Firebase 未配置”; add valid credentials before testing auth.

## Optional Data Layer / 可选数据层
`mysql_schema.sql` contains a starter schema for a Node.js/MySQL backend if you prefer relational storage alongside the Firebase demo.  
`mysql_schema.sql` 提供了 Node.js + MySQL 的基础表结构，可与 Firebase 示例并行参考。

## DashScope Chatbot API 示例 (Python)

```python
import os
from http import HTTPStatus
from dashscope import Application

def call_with_session():
    response = Application.call(
        # 若没有配置环境变量，可用百炼API Key将下行替换为：api_key="sk-xxx"。但不建议在生产环境中直接将API Key硬编码到代码中，以减少API Key泄露风险。
        api_key=os.getenv("sk-4963e772687a45aea82d05b62b206fc4"),
        app_id='cba59ba28eb848ada298737b2436d82d',  # 替换为实际的应用 ID
        prompt='你是谁？')

    if response.status_code != HTTPStatus.OK:
        print(f'request_id={response.request_id}')
        print(f'code={response.status_code}')
        print(f'message={response.message}')
        print('请参考文档：https://help.aliyun.com/zh/model-studio/developer-reference/error-code')
        return response

    responseNext = Application.call(
                # 若没有配置环境变量，可用百炼API Key将下行替换为：api_key="sk-xxx"。但不建议在生产环境中直接将API Key硬编码到代码中，以减少API Key泄露风险。
                api_key=os.getenv("sk-4963e772687a45aea82d05b62b206fc4"),
                app_id='cba59ba28eb848ada298737b2436d82d',  # 替换为实际的应用 ID
                prompt='你有什么技能?',
                session_id=response.output.session_id)  # 上一轮response的session_id

    if responseNext.status_code != HTTPStatus.OK:
        print(f'request_id={responseNext.request_id}')
        print(f'code={responseNext.status_code}')
        print(f'message={responseNext.message}')
        print('请参考文档：https://help.aliyun.com/zh/model-studio/developer-reference/error-code')
    else:
        print('%s\\n session_id=%s\\n' % (responseNext.output.text, responseNext.output.session_id))
        # print('%s\\n' % (response.usage))

if __name__ == '__main__':
    call_with_session()
```

## Deployment / 部署
Because assets are static, any static host (Firebase Hosting, Vercel, Netlify, Nginx, S3) works. Ensure HTTPS so Firebase SDKs load without mixed-content issues.  
前端为纯静态资源，可部署到任意静态托管；请使用 HTTPS 以保证 Firebase SDK 正常加载。
