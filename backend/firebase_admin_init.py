"""
Firebase Admin bootstrap and helpers.

Fail-fast initialization using GOOGLE_APPLICATION_CREDENTIALS and FIREBASE_STORAGE_BUCKET.
"""
from __future__ import annotations

import json
import logging
import os
import socket
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

import firebase_admin
from firebase_admin import auth as admin_auth
from firebase_admin import credentials, firestore, storage

BASE_DIR = Path(__file__).resolve().parent
DEFAULT_SERVICE_ACCOUNT_PATH = BASE_DIR.parent / "secrets" / "serviceAccount.json"
EXPECTED_PROJECT_ID = "feiai-7c59e"

logger = logging.getLogger(__name__)

_app: Optional[firebase_admin.App] = None
_db: Optional[firestore.Client] = None
_bucket = None
_project_id: Optional[str] = None
_bucket_name: Optional[str] = None
_init_logged = False


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
    env_bucket = os.getenv("FIREBASE_STORAGE_BUCKET", "")
    env_credentials = os.getenv("GOOGLE_APPLICATION_CREDENTIALS", "")
    logger.info("[firebase-admin] env FIREBASE_STORAGE_BUCKET=%s", env_bucket or "(empty)")
    logger.info("[firebase-admin] env GOOGLE_APPLICATION_CREDENTIALS=%s", env_credentials or "(empty)")
    bucket_name = env_bucket.strip()
    if not bucket_name:
        raise RuntimeError(
            "FIREBASE_STORAGE_BUCKET is not set. "
            "Set it to the bucket name shown in Firebase Console -> Storage "
            "(the part after gs://)."
        )
    _bucket_name = bucket_name
    logger.info("[firebase-admin] service account project_id=%s", project_id)
    logger.info("[firebase-admin] storage bucket=%s", bucket_name)
    options: Dict[str, Any] = {"projectId": project_id, "storageBucket": bucket_name}
    _app = firebase_admin.initialize_app(cred, options)
    _log_init(project_id, "Certificate")
    return _app


def get_firestore_client() -> firestore.Client:
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
    if _bucket is not None:
        return _bucket
    app = _init_app()
    bucket_name = _bucket_name or app.options.get("storageBucket") or ""
    if not bucket_name:
        raise RuntimeError("No storage bucket configured. Set FIREBASE_STORAGE_BUCKET.")
    _bucket = storage.bucket(bucket_name, app=app)
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
    try:
        blob.upload_from_string(payload, content_type="text/plain")
        blob.reload()
    except Exception as exc:
        try:
            from google.api_core import exceptions as gexc  # type: ignore
        except Exception:
            gexc = None
        if gexc and isinstance(exc, gexc.NotFound):
            message = (
                f"Storage bucket not found: {bucket.name}. "
                "Your bucket may not exist or project mismatch. "
                "Check Firebase Console -> Storage for the bucket URL, "
                "or run `gcloud storage buckets list`."
            )
            raise RuntimeError(message) from exc
        raise
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


def verify_id_token(id_token: str) -> Dict[str, Any]:
    """Verify a Firebase ID token and return the decoded claims."""
    app = _init_app()
    return admin_auth.verify_id_token(id_token, app=app)


def get_project_id() -> str:
    """Return the Firebase project_id resolved from credentials."""
    app = _init_app()
    return _project_id or app.project_id or ""


def get_user_role(uid: str) -> str:
    """
    Read role from Firestore users/{uid}. Defaults to 'patient' if missing or on read error.
    """
    try:
        db = get_firestore_client()
        snap = db.collection("users").document(uid).get()
        if snap.exists:
            role_val = snap.to_dict().get("role") or ""
            if isinstance(role_val, str) and role_val.strip():
                return role_val.strip().lower()
    except Exception:
        # Swallow errors to keep auth flow resilient; caller may log details.
        pass
    return "patient"
