# AEGIS System Implementation Notes

## Overall System Flow
1) Login and routing (auto)
- UI signs users in with Firebase Auth, reads `users/{uid}` to determine role, stores `userRole` in localStorage, and redirects to `/doctor/dashboard.html` or `/patient/dashboard.html`. (evidence: `ui/script.js:268`, `ui/script.js:284`, `ui/script.js:295`, `ui/script.js:297`)

2) Patient screening intake (user + auto)
- `/patient/screening.html` embeds the screening backend in an iframe at `http://127.0.0.1:5100/` and shows a fallback banner if it is not reachable. (evidence: `ui/patient/screening.html:147`, `ui/patient/screening.html:150`)
- Flask also exposes `/api/screen/voice/*` and `/api/screen/metagpt/*` proxies (with Firebase auth) to reach the screening backend. (evidence: `main.py:937`, `main.py:952`)

3) Questionnaire engine and report creation (auto)
- Screening backend MetaGPT flow starts with `/metagpt/start` (session_id + question) and finishes via `/metagpt/next`; on completion, it saves report text/JSON/PDF via `ReportManager`. (evidence: `screen/feiaiagent/app.py:1073`, `screen/feiaiagent/app.py:1112`, `screen/feiaiagent/report_manager.py:93`)

4) Report ingestion into Firebase (auto if called)
- Flask `/api/reports` accepts a PDF + metadata, normalizes answers, and writes a `reports/{reportId}` document plus a PDF file to Storage `reports/{reportId}.pdf`. (evidence: `main.py:546`, `main.py:569`, `main.py:622`, `main.py:629`)

5) Patient report viewing (auto)
- `patient/report.html` queries Firestore reports and downloads PDFs from Storage for preview/download. (evidence: `ui/firestoreService.js:133`, `ui/patient/report.html:822`, `ui/patient/report.html:639`)

6) Doctor review and imaging (doctor action + auto)
- Doctor dashboard computes metrics from `users` (status steps), `reports` (pending_review), and `chats` (unreadForDoctor). (evidence: `ui/doctor/dashboard.html:1700`, `ui/doctor/dashboard.html:1752`, `ui/doctor/dashboard.html:1776`)
- Doctor CT page loads `ctStudies` by doctorId, displays DICOMs from Storage, and allows the doctor to trigger AI analysis. (evidence: `ui/doctor/ct.js:803`, `ui/doctor/ct.js:527`, `ui/doctor/ct.js:708`)
- Flask `/api/ct/analyze` downloads DICOM files, runs PyTorch inference, and writes analysis back to Firestore. (evidence: `main.py:727`, `main.py:775`, `main.py:816`)

7) Follow-up and consultation (user action + auto)
- Appointments are created in Firestore `appointments` with status and timestamps; doctor dashboard reads appointment records into calendar metrics. (evidence: `ui/firestoreService.js:335`, `ui/doctor/dashboard.html:1466`)
- Patient AI chat uses `/api/chat` (Flask proxy to Node). Doctor-patient chat page uses client-only messages. (evidence: `ui/patient/chatbot.html:331`, `main.py:680`, `ui/doctor/script.js:180`)

## User-side Workflow
- Login and role routing
  - Firebase Auth sign-in, role read from `users/{uid}`, stored in localStorage for gating and redirect. (evidence: `ui/script.js:268`, `ui/script.js:284`, `ui/script.js:295`)

- Screening start
  - Main UI uses an iframe to the screening backend at `http://127.0.0.1:5100/`. (evidence: `ui/patient/screening.html:147`)
  - Optional in-app mode exists: `ui/patient/screening.js` and `ui/patient/screening.metagpt.js` call `/api/screen/voice/*` and `/api/screen/metagpt/*` with Firebase ID tokens. (evidence: `ui/patient/screening.js:271`, `ui/patient/screening.metagpt.js:108`, `ui/patient/screening.auth.js:36`)

- Questionnaire generation and progress
  - Voice mode: `/voice/start` issues a `session_id`, `/voice/stop` transcribes audio and returns text. (evidence: `screen/feiaiagent/app.py:679`, `screen/feiaiagent/app.py:700`)
  - MetaGPT mode: `/metagpt/start` returns a question; `/metagpt/next` consumes answers, yields the next question, and on completion saves reports. (evidence: `screen/feiaiagent/app.py:1073`, `screen/feiaiagent/app.py:1090`, `screen/feiaiagent/app.py:1112`)

- Risk summary and report access
  - Screening backend generates report text using `generate_assessment_report` on structured questionnaire items. (evidence: `screen/feiaiagent/local_questionnaire.py:211`, `screen/feiaiagent/local_questionnaire.py:216`)
  - Patient report page renders `riskLevel` and pulls PDFs from Storage. (evidence: `ui/patient/report.html:544`, `ui/patient/report.html:639`)

- Risk level display and updates
  - Patient dashboard reads `users/{uid}.riskLevel` and shows a risk ring. (evidence: `ui/patient/dashboard.html:801`)
  - Patient profile page can update `riskLevel` and `lastScreening` in Firestore. (evidence: `ui/patient/profile.html:355`, `ui/patient/profile.html:362`)

- Follow-up
  - Appointment booking writes `appointments` records with status tracking; cancellations update status. (evidence: `ui/firestoreService.js:335`, `ui/firestoreService.js:372`)

## Doctor-side Workflow
- Login and role gate
  - Login checks role from `users/{uid}.role` and routes to doctor dashboard when role is `doctor`. (evidence: `ui/script.js:284`, `ui/script.js:297`)

- Patient list and consultation chat
  - Doctor view loads `doctorPatients` links and fetches patient profiles from `users`. (evidence: `ui/doctor/script.js:285`)
  - Doctor chat UI is local and does not persist messages to Firestore. (evidence: `ui/doctor/script.js:180`)

- Dashboard metrics
  - Metrics use `users.status.steps` to count CT uploads and AI queue, plus `reports` and `chats` collections to count pending reviews and unread messages. (evidence: `ui/doctor/dashboard.html:1700`, `ui/doctor/dashboard.html:1752`, `ui/doctor/dashboard.html:1776`)

- CT Imaging workflow
  - CT studies list is filtered by `doctorId` and ordered by `updatedAt` when the index exists. (evidence: `ui/doctor/ct.js:803`)
  - DICOMs are loaded from Storage using `storagePrefix`, and the analysis button sends a POST to `/api/ct/analyze`. (evidence: `ui/doctor/ct.js:527`, `ui/doctor/ct.js:708`)

## Data Flow & Key Data Objects
- User profile (`users/{uid}`)
  - Created at registration with `role="patient"` and `riskLevel="low"`. (evidence: `ui/script.js:191`, `ui/script.js:192`)
  - Updated from profile page (riskLevel, lastScreening, etc.). (evidence: `ui/patient/profile.html:355`, `ui/patient/profile.html:362`)

- Screening session
  - Voice sessions are tracked by `session_id` and state stored in memory. (evidence: `screen/feiaiagent/app.py:671`)
  - MetaGPT sessions use `session_id` and are stored in `app.metagpt_sessions`. (evidence: `screen/feiaiagent/app.py:1090`)

- Questionnaire answers
  - `/api/reports` parses and normalizes answers and stores them as `answersRaw` and `answersNormalized`. (evidence: `main.py:569`, `main.py:622`, `main.py:625`)

- Risk assessment result
  - `riskLevel` is stored in report documents and rendered in patient report UI. (evidence: `main.py:588`, `ui/patient/report.html:544`)
  - `users/{uid}.riskLevel` drives the patient dashboard risk ring. (evidence: `ui/patient/dashboard.html:801`)

- Report object
  - Firestore `reports/{reportId}` includes `storagePath`, `downloadUrl`, `contentText`, and metadata for the screening. (evidence: `main.py:622`, `main.py:629`)
  - PDF stored in Firebase Storage under `reports/{reportId}.pdf`. (evidence: `main.py:597`)

- Follow-up / consultation record
  - `appointments` collection stores `patientId`, `doctorId`, `scheduledAt`, and `status`. (evidence: `ui/firestoreService.js:335`)

- CT study + analysis
  - `ctStudies` documents include `doctorId`, `patientId`, `storagePrefix`, and analysis fields updated by `/api/ct/analyze`. (evidence: `ui/doctor/ct.js:803`, `main.py:816`)
  - Inference uses ResNet18, center-crops to 224x224, and outputs slice-level probabilities with aggregate mean/max. (evidence: `ct/infer_dicom_series.py:12`, `ct/infer_dicom_series.py:54`, `ct/infer_dicom_series.py:113`)

## Important Design Decisions
- Firebase-first identity and data storage
  - Frontend uses Firebase Auth; backend uses Firebase Admin for report/CT writes. (evidence: `ui/script.js:268`, `firebase_admin_init.py:61`, `main.py:546`)

- Screening backend isolation
  - Screening runs in a separate service (`screen/feiaiagent`) with voice + MetaGPT endpoints; Flask proxies provide a unified origin with auth. (evidence: `ui/patient/screening.html:147`, `main.py:937`, `screen/feiaiagent/app.py:679`)

- Separate system assessment vs clinician review
  - System writes `riskLevel` into report docs; clinician review is represented by dashboard metrics and CT tools rather than a signed-off report workflow. (evidence: `main.py:588`, `ui/doctor/dashboard.html:1752`, `ui/doctor/ct.js:708`)

- CT AI runs server-side
  - `/api/ct/analyze` executes inference in Flask and writes results back to Firestore for UI display. (evidence: `main.py:727`, `main.py:794`)

## Known Limitations / Current Constraints
- Screening reports are stored locally by the screening backend and are not automatically pushed to Firestore; `/api/reports` exists but is not called by the embedded screening page in this repo. (evidence: `screen/feiaiagent/report_manager.py:93`, `main.py:546`, `ui/patient/screening.html:147`)
- `ui/patient/screening.js` and `ui/patient/screening.metagpt.js` are not referenced by `patient/screening.html` (iframe path is used instead). (evidence: `ui/patient/screening.html:147`)
- Risk level can be edited in the patient profile and is not auto-updated from screening results. (evidence: `ui/patient/profile.html:355`)
- Doctor report page is static (no Firestore binding) and doctor chat is mock-only with no persistence. (evidence: `ui/doctor/report.html:150`, `ui/doctor/script.js:180`)
- CT inference is a prototype slice-level classifier (center-crop) and not a full detection/segmentation pipeline. (evidence: `ct/infer_dicom_series.py:54`, `ct/infer_dicom_series.py:96`)
