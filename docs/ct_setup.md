# CT Imaging Setup (Backend)

This guide covers required environment variables and common errors for
`/api/ct/analyze`.

## Required env

Set the Firebase Admin service account path:

PowerShell (session only):
```
$env:CT_FIREBASE_SERVICE_ACCOUNT="E:\path\service-account.json"
```

PowerShell (persist for new terminals):
```
setx CT_FIREBASE_SERVICE_ACCOUNT "E:\path\service-account.json"
```

Restart the backend after changing env vars.

## Optional env

- `CT_MODEL_PATH`: path to model weights (defaults to `backend/ct/best_resnet_nodule_precise.pt`)
- `CT_DEVICE`: `cpu` (default), `cuda`, or `auto`
- `CT_ANALYSIS_TIMEOUT`: seconds before aborting analysis (default 120)

## Notes

- Do not commit service account JSON to git.
- The backend logs the resolved credential path at startup.

## Common errors (HTTP)

- `503 firebase_admin_unavailable`: CT_FIREBASE_SERVICE_ACCOUNT missing/invalid
- `400 no_ct_slices`: no slices under storagePrefix
- `503 ct_model_unavailable`: model missing or failed to load
- `500 internal_error`: unexpected failure (see server logs)
