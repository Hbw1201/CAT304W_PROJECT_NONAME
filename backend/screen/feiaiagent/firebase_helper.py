"""
Firebase Admin helper for screen backend.
Reuses logic from backend/firebase_admin_init.py for consistency.
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, Optional

import firebase_admin
from firebase_admin import credentials, firestore, storage

logger = logging.getLogger(__name__)

# Use the same service account path as main backend
BASE_DIR = Path(__file__).resolve().parent.parent.parent
DEFAULT_SERVICE_ACCOUNT_PATH = BASE_DIR / "secrets" / "serviceAccount.json"
EXPECTED_PROJECT_ID = "feiai-7c59e"

_app: Optional[firebase_admin.App] = None
_db: Optional[firestore.Client] = None
_bucket = None
_project_id: Optional[str] = None
_init_logged = False
_bucket_logged = False


def _read_service_account(path: Path) -> Dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"Firebase service account not found: {path}")
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _validate_project_id(project_id: Optional[str]) -> str:
    if not project_id:
        raise RuntimeError("Firebase service account is missing project_id.")
    if project_id != EXPECTED_PROJECT_ID:
        raise RuntimeError(
            "Firebase project_id mismatch. "
            f"service_account={project_id} expected={EXPECTED_PROJECT_ID}"
        )
    return project_id


def _load_credential() -> tuple[credentials.Base, str]:
    if not DEFAULT_SERVICE_ACCOUNT_PATH.exists():
        raise RuntimeError(
            "Firebase service account is not configured. "
            f"Expected {DEFAULT_SERVICE_ACCOUNT_PATH}"
        )
    data = _read_service_account(DEFAULT_SERVICE_ACCOUNT_PATH)
    project_id = _validate_project_id(data.get("project_id"))
    return credentials.Certificate(data), project_id


def _log_init(project_id: str, credential_type: str) -> None:
    global _init_logged
    if _init_logged:
        return
    logger.info("[firebase-admin] initialized project_id=%s credential=%s", project_id, credential_type)
    _init_logged = True


def _init_app() -> firebase_admin.App:
    global _app, _project_id
    if _app:
        return _app

    if firebase_admin._apps:
        _app = firebase_admin.get_app()
        _project_id = _validate_project_id(_app.project_id)
        _log_init(_project_id, "Certificate")
        return _app

    cred, project_id = _load_credential()
    _project_id = project_id
    options: Dict[str, Any] = {"projectId": EXPECTED_PROJECT_ID}
    _app = firebase_admin.initialize_app(cred, options)
    _log_init(project_id, "Certificate")
    return _app


def get_firestore_client() -> firebase_admin.firestore.Client:
    """Return a cached Firestore client, initializing Admin SDK on first use."""
    global _db
    if _db is not None:
        return _db
    app = _init_app()
    _db = firestore.client(app=app)
    return _db


def get_storage_bucket():
    """Return a cached default storage bucket."""
    global _bucket
    global _bucket_logged
    if _bucket is not None:
        return _bucket
    app = _init_app()
    bucket_name = (
        os.getenv("FIREBASE_STORAGE_BUCKET")
        or app.options.get("storageBucket")
        or (app.project_id and f"{app.project_id}.appspot.com")
    )
    if not bucket_name:
        raise RuntimeError("No storage bucket configured. Set FIREBASE_STORAGE_BUCKET or storageBucket in credentials.")
    _bucket = storage.bucket(bucket_name, app=app)
    if not _bucket_logged:
        logger.info("[firebase-admin] storage bucket=%s", bucket_name)
        _bucket_logged = True
    return _bucket


def upload_report_to_storage(
    local_path: str,
    patient_id: str,
    report_id: str,
    ext: str,
) -> str:
    """
    Upload report to Firebase Storage at reports/{patientId}/{reportId}.{ext}.
    
    Returns:
        Storage path (e.g., "reports/{patientId}/{reportId}.pdf")
    """
    try:
        safe_ext = ext.lstrip(".") or "pdf"
        file_path = Path(local_path)
        if not file_path.exists():
            raise FileNotFoundError(f"Report file not found: {file_path}")
        bucket = get_storage_bucket()
        path = f"reports/{patient_id}/{report_id}.{safe_ext}"
        blob = bucket.blob(path)
        content_type = "text/html; charset=utf-8" if safe_ext == "html" else "application/pdf"
        blob.upload_from_filename(str(file_path), content_type=content_type)
        try:
            blob.reload()
        except Exception:
            pass
        logger.info(
            "[firebase] report uploaded to %s size=%s content_type=%s",
            path,
            blob.size,
            blob.content_type,
        )
        return path
    except Exception as exc:
        logger.error(f"[firebase] Storage upload failed: {exc}")
        raise


def upload_pdf_to_storage(patient_id: str, report_id: str, local_path: str) -> str:
    """Backward-compatible PDF upload helper."""
    return upload_report_to_storage(local_path, patient_id, report_id, "pdf")


def write_report_doc(
    report_id: str,
    patient_id: str,
    screening_id: str,
    risk_level: str,
    storage_path: str,
    report_format: str,
    doctor_id: Optional[str] = None,
    source: str = "screening",
    file_name: Optional[str] = None,
    local_path: Optional[str] = None,
    download_url_local: Optional[str] = None,
    content_text: Optional[str] = None,
    answers_raw: Optional[Dict[str, Any]] = None,
    answers_normalized: Optional[Dict[str, Any]] = None,
) -> str:
    """
    Write report document to Firestore collection "reports".
    
    Document ID will be reportId (stable).
    
    Returns:
        Document ID
    """
    try:
        db = get_firestore_client()
        payload = {
            "createdAt": firestore.SERVER_TIMESTAMP,
            "updatedAt": firestore.SERVER_TIMESTAMP,
            "doctorId": doctor_id or "",
            "patientId": patient_id,
            "userId": patient_id,
            "reportId": report_id,
            "screeningId": screening_id,
            "sessionId": screening_id,
            "riskLevel": risk_level,
            "storagePath": storage_path,
            "fileName": file_name or Path(storage_path).name,
            "format": report_format,
            "title": "Lung Cancer Screening Report",
            "source": source,
            "status": "ready",
        }
        if local_path:
            payload["localPath"] = local_path
        if download_url_local:
            payload["downloadUrlLocal"] = download_url_local
        if report_format == "pdf":
            payload["pdfPath"] = storage_path
        if content_text:
            payload["contentText"] = content_text
        if answers_raw:
            payload["answersRaw"] = answers_raw
        if answers_normalized:
            payload["answersNormalized"] = answers_normalized
        
        doc_ref = db.collection("reports").document(report_id)
        doc_ref.set(payload, merge=True)
        doc_id = doc_ref.id
        logger.info(f"[firebase] Report doc written: reports/{doc_id}")
        return doc_id
    except Exception as exc:
        logger.error(f"[firebase] Firestore write failed: {exc}")
        raise

