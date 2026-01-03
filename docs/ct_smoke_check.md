# CT Imaging Smoke Check

Manual validation checklist for `/api/ct/analyze`.

## A) Missing CT_FIREBASE_SERVICE_ACCOUNT

- Unset `CT_FIREBASE_SERVICE_ACCOUNT`.
- Click "Run AI analysis".
- Expect HTTP 503 with `firebase_admin_unavailable`.
- UI shows a readable error message.

## B) No slices under storagePrefix

- Set `CT_FIREBASE_SERVICE_ACCOUNT` correctly.
- Use a study whose `storagePrefix` has no CT files.
- Expect HTTP 400 with `no_ct_slices`.
- UI shows "No CT slices found under storagePrefix".

## C) Model unavailable

- Ensure storagePrefix has slices.
- Remove/rename model file or set a bad `CT_MODEL_PATH`.
- Click "Run AI analysis".
- Expect HTTP 503 with `ct_model_unavailable`.
- UI shows "CT model is not available on server".

## D) Happy path

- Set `CT_FIREBASE_SERVICE_ACCOUNT` correctly.
- Provide a valid storagePrefix with slices.
- Ensure model file exists and loads.
- Expect HTTP 200 `ok:true`.
- Firestore `ctStudies/{studyId}`:
  - `analysisStatus=analyzed`
  - `resultSummary` and `slices` populated
- UI shows summary values (prob_malignant, high_risk_slices).
