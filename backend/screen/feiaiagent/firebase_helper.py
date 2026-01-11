"""
Firebase Admin helper for screen backend.
Reuses logic from backend/firebase_admin_init.py for consistency.
"""
from __future__ import annotations

import json
import logging
import os
import socket
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional, Union

import firebase_admin
from firebase_admin import credentials, firestore, storage

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DEFAULT_SERVICE_ACCOUNT_PATH = BASE_DIR / "secrets" / "serviceAccount.json"
EXPECTED_PROJECT_ID = "feiai-7c59e"
DEFAULT_STORAGE_BUCKET = "feiai-7c59e.appspot.com"

_app: Optional[firebase_admin.App] = None
_db: Optional[firestore.Client] = None
_bucket = None
_project_id: Optional[str] = None
_bucket_name: Optional[str] = None
_init_logged = False
_bucket_logged = False


class FirebaseUploadError(Exception):
    def __init__(self, stage: str, bucket: str, storage_path: str, cause: Optional[Exception] = None):
        self.stage = stage
        self.bucket = bucket
        self.storage_path = storage_path
        self.cause = cause
        detail = f"{type(cause).__name__}: {cause}" if cause else ""
        message = f"{stage} failed {detail}".strip()
        super().__init__(message)


def _credential_path() -> Path:
    raw = os.getenv("GOOGLE_APPLICATION_CREDENTIALS", "").strip()
    if not raw:
        raise RuntimeError("GOOGLE_APPLICATION_CREDENTIALS is not set")
    path = Path(raw)
    if path.exists():
        return path
    if DEFAULT_SERVICE_ACCOUNT_PATH.exists():
        logger.warning(
            "[firebase-admin] GOOGLE_APPLICATION_CREDENTIALS not found at %s; falling back to %s",
            path,
            DEFAULT_SERVICE_ACCOUNT_PATH,
        )
        return DEFAULT_SERVICE_ACCOUNT_PATH
    raise FileNotFoundError(f"Firebase service account not found: {path}")


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
    path = _credential_path()
    data = _read_service_account(path)
    project_id = _validate_project_id(data.get("project_id"))
    return credentials.Certificate(data), project_id


def _log_init(project_id: str, credential_type: str) -> None:
    global _init_logged
    if _init_logged:
        return
    logger.info("[firebase-admin] initialized project_id=%s credential=%s", project_id, credential_type)
    _init_logged = True


def _init_app() -> firebase_admin.App:
    global _app, _project_id, _bucket_name
    if _app:
        return _app

    if firebase_admin._apps:
        _app = firebase_admin.get_app()
        _project_id = _validate_project_id(_app.project_id)
        _log_init(_project_id, "Certificate")
        return _app

    cred, project_id = _load_credential()
    _project_id = project_id
    bucket_name = os.getenv("FIREBASE_STORAGE_BUCKET", DEFAULT_STORAGE_BUCKET).strip()
    if not bucket_name:
        raise RuntimeError("FIREBASE_STORAGE_BUCKET is not set")
    _bucket_name = bucket_name
    options: Dict[str, Any] = {"projectId": project_id, "storageBucket": bucket_name}
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
    global _bucket, _bucket_name
    global _bucket_logged
    if _bucket is not None:
        return _bucket
    app = _init_app()
    bucket_name = _bucket_name or app.options.get("storageBucket") or ""
    if not bucket_name:
        raise RuntimeError("No storage bucket configured. Set FIREBASE_STORAGE_BUCKET.")
    _bucket = storage.bucket(bucket_name, app=app)
    if not _bucket_logged:
        logger.info("[firebase-admin] storage bucket=%s", bucket_name)
        _bucket_logged = True
    return _bucket


def _classify_exception(exc: Exception) -> str:
    try:
        from google.api_core import exceptions as gexc  # type: ignore
    except Exception:
        gexc = None

    if isinstance(exc, FileNotFoundError):
        return "missing credentials file"
    if isinstance(exc, json.JSONDecodeError):
        return "invalid json"
    if gexc:
        if isinstance(exc, (gexc.PermissionDenied, gexc.Forbidden)):
            return "permission denied / insufficient IAM"
        if isinstance(exc, gexc.NotFound):
            return "bucket not found"
        if isinstance(exc, (gexc.ServiceUnavailable, gexc.DeadlineExceeded, gexc.InternalServerError)):
            return "network/DNS error"
    if isinstance(exc, (ConnectionError, TimeoutError, socket.gaierror)):
        return "network/DNS error"
    message = str(exc).lower()
    if "permission" in message or "unauthorized" in message or "forbidden" in message:
        return "permission denied / insufficient IAM"
    if "not found" in message and "bucket" in message:
        return "bucket not found"
    if "dns" in message or "name resolution" in message or "timed out" in message:
        return "network/DNS error"
    return "unknown"


def storage_healthcheck() -> None:
    bucket = get_storage_bucket()
    payload = datetime.utcnow().isoformat()
    blob = bucket.blob("healthcheck/ping.txt")
    blob.upload_from_string(payload, content_type="text/plain")
    blob.reload()
    size = blob.size or 0
    if size <= 0:
        raise RuntimeError("storage healthcheck failed: size=0")
    try:
        blob.delete()
    except Exception:
        pass
    logger.info("[firebase-admin] storage healthcheck ok bucket=%s size=%s", bucket.name, size)


def init_firebase_or_die() -> None:
    try:
        _init_app()
        get_firestore_client()
        storage_healthcheck()
    except Exception as exc:
        category = _classify_exception(exc)
        message = f"Firebase init failed ({category}): {exc}"
        logger.error(message)
        raise RuntimeError(message) from exc


def get_project_id() -> str:
    """Return the Firebase project_id resolved from credentials."""
    app = _init_app()
    return _project_id or app.project_id or ""


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


def _coerce_pdf_path(pdf_bytes_or_path: Union[bytes, str, Path]) -> tuple[Path, Optional[Path]]:
    if isinstance(pdf_bytes_or_path, (bytes, bytearray)):
        temp = tempfile.NamedTemporaryFile(delete=False, suffix=".pdf")
        temp.write(pdf_bytes_or_path)
        temp.flush()
        temp.close()
        temp_path = Path(temp.name)
        return temp_path, temp_path
    return Path(str(pdf_bytes_or_path)).expanduser(), None


def _describe_pdf_source(pdf_path: Union[str, Path]) -> tuple[str, int, Path]:
    file_path = Path(str(pdf_path)).expanduser()
    abs_path = str(file_path.resolve())
    size = -1
    try:
        size = file_path.stat().st_size
    except FileNotFoundError:
        size = -1
    return abs_path, size, file_path


def _mark_pdf_failed(doc_ref: Optional[Any], report_id: str, error_message: str) -> None:
    payload = {
        "pdfStatus": "failed",
        "status": "failed",
        "pdfError": str(error_message)[:500],
        "pdfUpdatedAt": firestore.SERVER_TIMESTAMP,
        "updatedAt": firestore.SERVER_TIMESTAMP,
    }
    try:
        if doc_ref is None:
            db = get_firestore_client()
            doc_ref = db.collection("reports").document(report_id)
        doc_ref.set(payload, merge=True)
    except Exception:
        logger.warning("[firebase] Failed to mark pdfStatus=failed for reportId=%s", report_id)


def debug_check_object(storage_path: str) -> None:
    if not storage_path:
        return
    try:
        bucket = get_storage_bucket()
        blob = bucket.blob(storage_path)
        exists = blob.exists()
        try:
            blob.reload()
        except Exception:
            pass
        size = blob.size or 0
        logger.info(
            "[firebase] debug_check_object exists=%s size=%s bucket=%s path=%s",
            exists,
            size,
            bucket.name,
            storage_path,
        )
    except Exception as exc:
        logger.warning("[firebase] debug_check_object failed path=%s error=%s", storage_path, exc)


def upload_report_pdf(
    report_doc: Dict[str, Any],
    pdf_bytes_or_path: Union[bytes, str, Path],
) -> str:
    auth_uid = str(report_doc.get("auth_uid") or report_doc.get("authUid") or "").strip()
    report_id = str(report_doc.get("reportId") or report_doc.get("id") or "").strip()
    doc_ref = report_doc.get("_doc_ref")
    if not auth_uid or not report_id:
        raise ValueError("upload_report_pdf requires auth_uid and reportId")

    storage_path = f"reports/{auth_uid}/{report_id}.pdf"
    temp_path = None
    pdf_path, temp_path = _coerce_pdf_path(pdf_bytes_or_path)
    local_path, local_size, file_path = _describe_pdf_source(pdf_path)
    project_id = get_project_id()
    bucket_name = os.getenv("FIREBASE_STORAGE_BUCKET", "").strip()
    try:
        bucket = get_storage_bucket()
        bucket_name = bucket.name
    except Exception as exc:
        logger.error(
            "[firebase] bucket init failed auth_uid=%s reportId=%s error=%s",
            auth_uid,
            report_id,
            exc,
        )
        _mark_pdf_failed(doc_ref, report_id, f"bucket init failed: {exc}")
        raise FirebaseUploadError("upload_pdf", bucket_name, storage_path, exc) from exc
    logger.info(
        "[firebase] upload context project=%s bucket=%s auth_uid=%s reportId=%s local=%s size=%s dest=%s",
        project_id,
        bucket.name,
        auth_uid,
        report_id,
        local_path,
        local_size,
        storage_path,
    )
    if not file_path.exists():
        _mark_pdf_failed(doc_ref, report_id, f"local file not found: {local_path}")
        raise FileNotFoundError(f"Report file not found: {local_path}")
    try:
        blob = bucket.blob(storage_path)
        blob.upload_from_filename(str(file_path), content_type="application/pdf")
        exists = blob.exists()
        try:
            blob.reload()
        except Exception:
            pass
        size = blob.size or 0
        logger.info(
            "[firebase] upload verify exists=%s size=%s storagePath=%s",
            exists,
            size,
            storage_path,
        )
        if not exists or size <= 0:
            message = f"upload verification failed exists={exists} size={size}"
            _mark_pdf_failed(doc_ref, report_id, message)
            raise FirebaseUploadError("upload_pdf", bucket_name, storage_path, RuntimeError(message))
        logger.info(
            "[firebase] report uploaded auth_uid=%s reportId=%s storagePath=%s size=%s",
            auth_uid,
            report_id,
            storage_path,
            size,
        )
    except Exception as exc:
        logger.error(
            "[firebase] Storage upload failed auth_uid=%s reportId=%s storagePath=%s error=%s",
            auth_uid,
            report_id,
            storage_path,
            exc,
        )
        _mark_pdf_failed(doc_ref, report_id, f"upload failed: {exc}")
        raise FirebaseUploadError("upload_pdf", bucket_name, storage_path, exc) from exc
    finally:
        if temp_path:
            try:
                temp_path.unlink()
            except Exception:
                pass

    try:
        db = get_firestore_client()
        doc_ref = doc_ref or db.collection("reports").document(report_id)
        doc_ref.set(
            {
                "patientUid": auth_uid,
                "storagePath": storage_path,
                "pdfPath": storage_path,
                "pdfStatus": "ready",
                "status": "ready",
                "pdfError": "",
                "pdfUpdatedAt": firestore.SERVER_TIMESTAMP,
                "updatedAt": firestore.SERVER_TIMESTAMP,
            },
            merge=True,
        )
    except Exception as exc:
        logger.error(
            "[firebase] Firestore update failed auth_uid=%s reportId=%s storagePath=%s error=%s",
            auth_uid,
            report_id,
            storage_path,
            exc,
        )
        raise FirebaseUploadError("update_firestore", bucket_name, storage_path, exc) from exc

    return storage_path


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
    pdf_status: Optional[str] = None,
    status: Optional[str] = None,
) -> str:
    """
    Write report document to Firestore collection "reports".
    
    Document ID will be reportId (stable).
    
    Returns:
        Document ID
    """
    try:
        db = get_firestore_client()
        resolved_status = status or ("ready" if storage_path else "pending")
        resolved_pdf_status = pdf_status or ("ready" if storage_path else "")
        payload = {
            "createdAt": firestore.SERVER_TIMESTAMP,
            "updatedAt": firestore.SERVER_TIMESTAMP,
            "doctorId": doctor_id or "",
            "patientId": patient_id,
            "patientUid": patient_id,
            "userId": patient_id,
            "userUid": patient_id,
            "reportId": report_id,
            "screeningId": screening_id,
            "sessionId": screening_id,
            "riskLevel": risk_level,
            "storagePath": storage_path,
            "fileName": file_name or (Path(storage_path).name if storage_path else ""),
            "format": report_format,
            "title": "Lung Cancer Screening Report",
            "source": source,
            "status": resolved_status,
        }
        if resolved_pdf_status:
            payload["pdfStatus"] = resolved_pdf_status
            if resolved_pdf_status in ("ready", "error"):
                payload["pdfUpdatedAt"] = firestore.SERVER_TIMESTAMP
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

