"""
Lazy Firebase Admin bootstrap and helpers.

Uses secrets/serviceAccount.json and enforces project_id = feiai-7c59e.
"""
from __future__ import annotations

import json
import logging
import os
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
_init_logged = False


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
            "Expected secrets/serviceAccount.json."
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
    global _bucket
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
    return _bucket


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
