"""
Lazy Firebase Admin bootstrap and helpers.

Env vars:
- FIREBASE_SERVICE_ACCOUNT: path to service account JSON file (preferred).
- FIREBASE_SERVICE_ACCOUNT_JSON: raw JSON string (fallback).
- FIREBASE_PROJECT_ID: optional project override.
"""
from __future__ import annotations

import json
import os
from typing import Any, Dict, Optional

import firebase_admin
from firebase_admin import auth as admin_auth
from firebase_admin import credentials, firestore, storage

_app: Optional[firebase_admin.App] = None
_db: Optional[firestore.Client] = None
_bucket = None


def _load_credential() -> credentials.Base:
    path = os.getenv("FIREBASE_SERVICE_ACCOUNT")
    raw_json = os.getenv("FIREBASE_SERVICE_ACCOUNT_JSON")

    if path:
        if not os.path.exists(path):
            raise FileNotFoundError(f"FIREBASE_SERVICE_ACCOUNT path not found: {path}")
        return credentials.Certificate(path)

    if raw_json:
        try:
            data = json.loads(raw_json)
        except json.JSONDecodeError as exc:
            raise ValueError("FIREBASE_SERVICE_ACCOUNT_JSON is not valid JSON") from exc
        return credentials.Certificate(data)

    raise RuntimeError("Firebase service account is not configured. Set FIREBASE_SERVICE_ACCOUNT or FIREBASE_SERVICE_ACCOUNT_JSON.")


def _init_app() -> firebase_admin.App:
    global _app
    if _app:
        return _app

    if firebase_admin._apps:
        _app = firebase_admin.get_app()
        return _app

    cred = _load_credential()
    options: Dict[str, Any] = {}
    project_id = os.getenv("FIREBASE_PROJECT_ID")
    if project_id:
        options["projectId"] = project_id

    _app = firebase_admin.initialize_app(cred, options or None)
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
