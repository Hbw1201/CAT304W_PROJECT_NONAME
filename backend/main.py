"""
main.py
---------
System entry point for the MAQ-SCREEN web UI. Bootstraps a Flask server that
serves the existing static frontend and exposes placeholder APIs, ready for
future multi-agent and CT inference modules.
"""
from __future__ import annotations

import os
import atexit
import socket
import subprocess
import sys
import time
import traceback
import logging
import base64
import json
import shutil
import tempfile
import uuid
from contextlib import contextmanager
from datetime import datetime
from functools import wraps
from pathlib import Path
from typing import Any, Dict, Optional, Callable
from urllib.parse import urlparse

try:
    import fcntl  # Linux only
except Exception:
    fcntl = None

from flask import Flask, abort, jsonify, request, send_from_directory, Response, g
from flask_cors import CORS
import requests
import firebase_admin
import torch
from firebase_admin import credentials as admin_credentials
from firebase_admin import firestore as admin_firestore
from firebase_admin import storage as admin_storage

from firebase_admin_init import (
    get_firestore_client,
    get_project_id,
    get_user_role,
    verify_id_token as verify_firebase_id_token,
    get_storage_bucket,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


BASE_DIR = Path(__file__).resolve().parent
UI_DIR = BASE_DIR.parent / "frontend"
CT_DIR = BASE_DIR / "ct"
SCREEN_BACKEND_URL = os.environ.get("SCREEN_BACKEND_URL", "http://127.0.0.1:5100").rstrip("/")
_parsed_screen_url = urlparse(SCREEN_BACKEND_URL if "://" in SCREEN_BACKEND_URL else f"http://{SCREEN_BACKEND_URL}")
SCREEN_HOST = os.environ.get("SCREEN_HOST", _parsed_screen_url.hostname or "127.0.0.1")
SCREEN_PORT = int(os.environ.get("SCREEN_PORT", _parsed_screen_url.port or 5100))
START_SCREEN = os.environ.get("START_SCREEN", "1").lower() not in ("0", "false", "no")
CHAT_HOST = os.environ.get("CHAT_HOST", "127.0.0.1")
CHAT_PORT = int(os.environ.get("NODE_CHAT_PORT", 3000))
DEFAULT_NODE_CHAT_URL = f"http://{CHAT_HOST}:{CHAT_PORT}"
AUTO_START_CHAT = os.environ.get("AUTO_START_CHAT", "1").lower() not in ("0", "false", "no")
_screen_process: Optional[subprocess.Popen] = None
_chat_process: Optional[subprocess.Popen] = None
_chat_log_fp: Optional[Any] = None
MAX_PDF_BYTES = 10 * 1024 * 1024  # 10MB limit

# Allowlist of root-level UI assets that can be served directly from /ui.
ROOT_UI_ALLOWLIST = {
    "index.html",
    "login.html",
    "register.html",
    "forgot.html",
    "script.js",
    "firebase-config.js",
    "firestoreService.js",
    "forgot.js",
    "dashboard.css",
    "login.css",
    "shared-layout.css",
}

# Legacy /patient/login.* assets that live at the UI root.
PATIENT_ROOT_FALLBACKS = {"login.css", "script.js"}

CT_BUCKET_NAME = "feiai-7c59e.firebasestorage.app"
CT_MODEL = None
CT_MODEL_PATH = os.environ.get("CT_MODEL_PATH", "").strip()

if str(CT_DIR) not in sys.path:
    sys.path.insert(0, str(CT_DIR))
from infer_dicom_series import infer_dicom_dir, load_model

logger.info("[CT] torch version=%s", getattr(torch, "__version__", "unknown"))
logger.info("[CT] cuda available=%s", torch.cuda.is_available())
if torch.cuda.is_available():
    try:
        logger.info("[CT] cuda device=%s", torch.cuda.get_device_name(0))
    except Exception as exc:  # noqa: BLE001
        logger.warning("[CT] cuda device name unavailable: %s", exc)

CT_DEVICE = "cuda" if torch.cuda.is_available() else "cpu"
_CT_FIRESTORE_CLIENT: Optional[Any] = None


def _load_ct_model():
    global CT_MODEL
    path = CT_MODEL_PATH
    if not path:
        logger.warning("[CT] CT_MODEL_PATH not set; CT model will be unavailable.")
        return None
    if not os.path.exists(path):
        logger.error("[CT] CT model file not found: %s", path)
        return None
    try:
        logger.info("[CT] loading CT model from %s", CT_MODEL_PATH)
        from ct.infer_dicom_series import load_model
        model = load_model(weights_path=CT_MODEL_PATH)
        model.eval()
        return model
    except Exception:
        logger.exception("[CT] Failed to load CT model")
        return None


CT_MODEL = _load_ct_model()


def _ensure_ct_admin_app() -> firebase_admin.App:
    if firebase_admin._apps:
        return firebase_admin.get_app()

    cred_path = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")
    if not cred_path:
        raise RuntimeError("GOOGLE_APPLICATION_CREDENTIALS is required for CT Firestore.")

    cred_file = Path(cred_path)
    if not cred_file.exists():
        raise RuntimeError(f"Firebase service account not found: {cred_file}")

    logger.info("[CT] firebase credentials path=%s", cred_file)

    service_data = json.loads(cred_file.read_text(encoding="utf-8"))
    project_id = service_data.get("project_id") or "feiai-7c59e"
    logger.info("[CT] firestore project_id=%s", project_id)
    cred = admin_credentials.Certificate(service_data)
    return firebase_admin.initialize_app(
        cred,
        {
            "projectId": project_id,
            "storageBucket": CT_BUCKET_NAME,
        },
    )


def _get_ct_firestore():
    global _CT_FIRESTORE_CLIENT
    if _CT_FIRESTORE_CLIENT is not None:
        return _CT_FIRESTORE_CLIENT
    app = _ensure_ct_admin_app()
    _CT_FIRESTORE_CLIENT = admin_firestore.client(app=app)
    return _CT_FIRESTORE_CLIENT


def _get_ct_bucket():
    app = _ensure_ct_admin_app()
    return admin_storage.bucket(CT_BUCKET_NAME, app=app)


def _download_all_dicoms(storage_prefix: str, out_dir: Path) -> int:
    bucket = _get_ct_bucket()
    count = 0
    for blob in bucket.list_blobs(prefix=storage_prefix):
        name = blob.name or ""
        if not name.lower().endswith(".dcm"):
            continue
        filename = os.path.basename(name) or f"slice_{count:04d}.dcm"
        dest = out_dir / filename
        dest.parent.mkdir(parents=True, exist_ok=True)
        try:
            blob.download_to_filename(str(dest))
        except FileNotFoundError:
            # In rare cases, concurrent cleanup or race may remove the file right before utime().
            # Recreate parent dir and retry once.
            dest.parent.mkdir(parents=True, exist_ok=True)
            blob.download_to_filename(str(dest))
        count += 1
    return count


@contextmanager
def _study_lock(study_id: str):
    """
    Prevent concurrent /api/ct/analyze for the same studyId on the same host.
    Uses a file lock under /tmp. Works on Linux.
    """
    lock_path = Path("/tmp") / f"aegis_ct_{study_id}.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fp = open(lock_path, "w")
    try:
        if fcntl:
            fcntl.flock(fp.fileno(), fcntl.LOCK_EX)
        yield
    finally:
        try:
            if fcntl:
                fcntl.flock(fp.fileno(), fcntl.LOCK_UN)
        finally:
            fp.close()


def _extract_bearer_token() -> Optional[str]:
    header = request.headers.get("Authorization", "")
    if not header:
        return None
    parts = header.split()
    if len(parts) != 2 or parts[0].lower() != "bearer":
        return None
    return parts[1].strip() or None


AUTH_BYPASS_ALLOWLIST = {
    "/api/screen/health",
    "/api/screen/voice/status",
    "/api/metagpt/agents_status",
}


def _is_auth_bypass_path(path: str) -> bool:
    return path in AUTH_BYPASS_ALLOWLIST


def _get_auth_mode() -> str:
    mode = os.environ.get("AUTH_MODE", "firebase").strip().lower()
    return "dev" if mode == "dev" else "firebase"


def _is_dev_fallback_allowed() -> bool:
    debug_flag = os.environ.get("DEBUG", "").strip().lower() in {"1", "true", "yes"}
    flask_env = os.environ.get("FLASK_ENV", "").strip().lower()
    return debug_flag or flask_env == "development"


def _service_account_path() -> Path:
    return BASE_DIR.parent / "secrets" / "serviceAccount.json"


def _service_account_present() -> bool:
    return _service_account_path().exists()


def _set_request_user(payload: Dict[str, Any]) -> None:
    try:
        setattr(request, "user", payload)
    except Exception:
        pass


def _set_dev_identity() -> None:
    g.firebase_uid = "dev"
    g.firebase_email = "dev@local"
    g.firebase_role = "patient"
    g.firebase_token = ""
    g.firebase_aud = ""
    g.firebase_iss = ""
    _set_request_user({"uid": "dev", "role": "patient", "mode": "dev"})


def _set_anonymous_identity() -> None:
    g.firebase_uid = ""
    g.firebase_email = ""
    g.firebase_role = "anonymous"
    g.firebase_token = ""
    g.firebase_aud = ""
    g.firebase_iss = ""


def _decode_jwt_part(part: str) -> Dict[str, Any]:
    padding = "=" * (-len(part) % 4)
    try:
        decoded = base64.urlsafe_b64decode(part + padding)
        return json.loads(decoded)
    except Exception:  # noqa: BLE001
        return {}


def _decode_jwt_unverified(token: str) -> tuple[Dict[str, Any], Dict[str, Any]]:
    parts = token.split(".")
    if len(parts) < 2:
        return {}, {}
    return _decode_jwt_part(parts[0]), _decode_jwt_part(parts[1])


def _set_identity(decoded: Dict[str, Any], token: str) -> None:
    uid = decoded.get("uid") or decoded.get("sub") or ""
    email = decoded.get("email", "") or ""
    g.firebase_aud = decoded.get("aud", "") or ""
    g.firebase_iss = decoded.get("iss", "") or ""
    role = "patient"
    if uid:
        try:
            role = get_user_role(uid) or "patient"
        except Exception as exc:  # noqa: BLE001
            logger.warning("[auth] failed to read role for %s: %s", uid, exc)
            role = "patient"
    g.firebase_uid = uid
    g.firebase_email = email
    g.firebase_role = role
    g.firebase_token = token


def require_firebase_auth(allowed_roles: Optional[set[str]] = None) -> Callable:
    """Decorator enforcing Firebase ID token auth; optional role allowlist."""

    def decorator(func: Callable) -> Callable:
        @wraps(func)
        def wrapper(*args, **kwargs):
            request_path = request.path
            auth_mode = _get_auth_mode()
            if auth_mode == "dev":
                _set_dev_identity()
                logger.info("[auth] AUTH_MODE=dev bypass for %s %s", request.method, request_path)
                return func(*args, **kwargs)

            if _is_auth_bypass_path(request_path):
                _set_anonymous_identity()
                logger.info("[auth] bypass allowlist for %s %s", request.method, request_path)
                return func(*args, **kwargs)

            if not _service_account_present():
                if _is_dev_fallback_allowed():
                    logger.warning(
                        "[auth] Firebase service account missing; dev fallback active for %s %s",
                        request.method,
                        request_path,
                    )
                    _set_dev_identity()
                    return func(*args, **kwargs)
                return jsonify({"error": "Firebase service account not configured"}), 401

            token = _extract_bearer_token()
            if not token:
                return jsonify({"error": "Missing Authorization Bearer token"}), 401
            try:
                decoded = verify_firebase_id_token(token)
            except Exception as exc:  # noqa: BLE001
                token_prefix = token[:20] if token else ""
                header_claims, payload_claims = _decode_jwt_unverified(token) if token else ({}, {})
                logger.warning(
                    "[auth] token verification failed: %s | token_prefix=%s header_aud=%s header_iss=%s payload_aud=%s payload_iss=%s",
                    exc,
                    token_prefix,
                    header_claims.get("aud"),
                    header_claims.get("iss"),
                    payload_claims.get("aud"),
                    payload_claims.get("iss"),
                )
                return jsonify({"error": "Invalid Firebase ID token", "detail": str(exc)}), 401

            _set_identity(decoded, token)
            if not getattr(g, "firebase_uid", None):
                return jsonify({"error": "Token missing uid"}), 401

            if allowed_roles:
                role = (getattr(g, "firebase_role", "") or "").lower()
                if role not in allowed_roles:
                    return jsonify({"error": "Forbidden", "role": role}), 403
            return func(*args, **kwargs)

        return wrapper

    return decorator


def _identity_headers() -> Dict[str, str]:
    return {
        "X-Firebase-UID": getattr(g, "firebase_uid", "") or "",
        "X-Firebase-Role": getattr(g, "firebase_role", "") or "",
        "X-Firebase-Email": getattr(g, "firebase_email", "") or "",
    }


def _build_report_id(uid: str) -> str:
    now = time.localtime()
    ymd = f"{now.tm_year}{now.tm_mon:02d}{now.tm_mday:02d}"
    safe_uid = "".join(ch for ch in uid if ch.isalnum())[:12] or "patient"
    return f"noname_{ymd}_{safe_uid}_{int(time.time() * 1000)}"


def _build_screening_id() -> str:
    return f"demo_screening_{int(time.time() * 1000)}"


def _decode_pdf_bytes() -> tuple[Optional[bytes], Dict[str, Any]]:
    """
    Return (pdf_bytes, metadata) where metadata contains optional fields from form/json.
    """
    meta: Dict[str, Any] = {
        "reportId": None,
        "screeningId": None,
        "doctorId": None,
        "riskLevel": None,
        "answers": None,
        "mode": None,
        "sessionId": None,
        "reportText": None,
    }
    pdf_bytes: Optional[bytes] = None

    content_type = request.content_type or ""
    if "multipart/form-data" in content_type:
        pdf_file = request.files.get("pdf")
        if pdf_file:
            pdf_bytes = pdf_file.read()
        meta["reportId"] = request.form.get("reportId")
        meta["screeningId"] = request.form.get("screeningId")
        meta["doctorId"] = request.form.get("doctorId")
        meta["riskLevel"] = request.form.get("riskLevel")
        meta["answers"] = request.form.get("answers")
        meta["mode"] = request.form.get("mode")
        meta["sessionId"] = request.form.get("session_id") or request.form.get("sessionId")
        meta["reportText"] = request.form.get("reportText")
    else:
        data = request.get_json(silent=True, force=True) or {}
        meta["reportId"] = data.get("reportId")
        meta["screeningId"] = data.get("screeningId")
        meta["doctorId"] = data.get("doctorId")
        meta["riskLevel"] = data.get("riskLevel")
        meta["answers"] = data.get("answers")
        meta["mode"] = data.get("mode")
        meta["sessionId"] = data.get("session_id") or data.get("sessionId")
        meta["reportText"] = data.get("reportText")
        pdf_b64 = data.get("pdfBase64")
        if pdf_b64:
            try:
                payload = pdf_b64.split(",", 1)[-1]
                pdf_bytes = base64.b64decode(payload)
            except Exception as exc:  # noqa: BLE001
                logger.warning("[reports] failed to decode pdfBase64: %s", exc)
                raise

    return pdf_bytes, meta


def _parse_answers_payload(raw: Any) -> list[Dict[str, Any]]:
    if not raw:
        return []
    if isinstance(raw, list):
        return [item for item in raw if isinstance(item, dict)]
    if isinstance(raw, dict):
        return [{"question": key, "answer": value} for key, value in raw.items()]
    if isinstance(raw, str):
        try:
            data = json.loads(raw)
            if isinstance(data, list):
                return [item for item in data if isinstance(item, dict)]
            if isinstance(data, dict):
                return [{"question": key, "answer": value} for key, value in data.items()]
        except Exception:  # noqa: BLE001
            return []
    return []


def _answers_list_to_map(answers_list: list[Dict[str, Any]]) -> Dict[str, str]:
    answers_map: Dict[str, str] = {}
    for item in answers_list:
        question = str(item.get("question") or "").strip()
        answer = str(item.get("answer") or "").strip()
        if not question or not answer:
            continue
        answers_map[question] = answer
    return answers_map


_ANSWER_KEY_MAP = {
    "full name": "fullName",
    "full_name": "fullName",
    "name": "fullName",
    "姓名": "fullName",
    "gender": "gender",
    "sex": "gender",
    "性别": "gender",
    "性别(1男2女)": "gender",
    "year of birth": "yearOfBirth",
    "birth year": "yearOfBirth",
    "birth_year": "yearOfBirth",
    "dob": "yearOfBirth",
    "date of birth": "yearOfBirth",
    "出生年份": "yearOfBirth",
    "height (cm)": "height",
    "height": "height",
    "weight (kg)": "weight",
    "weight": "weight",
    "smoking history": "smokingHistory",
    "smoking_history": "smokingHistory",
    "passive smoking exposure": "passiveSmoking",
    "passive_smoking": "passiveSmoking",
    "long-term exposure to kitchen fumes": "kitchenFumes",
    "kitchen_fumes": "kitchenFumes",
    "exposure to occupational carcinogens": "occupationExposure",
    "occupation_exposure": "occupationExposure",
    "lung cancer in first-degree relatives": "familyCancerHistory",
    "family_cancer_history": "familyCancerHistory",
    "personal cancer history": "personalCancerHistory",
    "personal_tumor_history": "personalCancerHistory",
    "chest ct scan in the past year": "chestCtLastYear",
    "chest_ct_last_year": "chestCtLastYear",
    "chronic lung disease history": "chronicLungDisease",
    "chronic_lung_disease": "chronicLungDisease",
    "unexplained weight loss (last 6 months)": "recentWeightLoss",
    "recent_weight_loss": "recentWeightLoss",
    "symptoms such as persistent cough, blood in sputum, hoarseness": "recentSymptoms",
    "recent_symptoms": "recentSymptoms",
    "general health self-rating (1 good, 2 average, 3 poor)": "selfFeeling",
    "self_feeling": "selfFeeling",
    "occupation": "occupation",
}


def _normalize_key(raw_key: Any) -> str:
    return str(raw_key or "").strip()


def normalize_answers(answers_raw: Dict[str, str]) -> Dict[str, Any]:
    normalized: Dict[str, Any] = {}
    for key, value in (answers_raw or {}).items():
        cleaned = _normalize_key(key)
        lowered = cleaned.lower()
        mapped = _ANSWER_KEY_MAP.get(cleaned) or _ANSWER_KEY_MAP.get(lowered)
        if not mapped:
            continue
        normalized[mapped] = value
    return normalized


def normalize_gender(value: Any) -> str:
    raw = str(value or "").strip()
    lowered = raw.lower()
    if lowered in {"1", "male", "man", "m", "男"}:
        return "male"
    if lowered in {"2", "female", "woman", "f", "女"}:
        return "female"
    return raw or "unknown"


def map_option(question_key: str, raw_value: Any) -> Dict[str, Any]:
    text = str(raw_value or "").strip()
    lowered = text.lower()
    numeric_map = {
        "smokingHistory": {"1": True, "2": False},
        "passiveSmoking": {"1": True, "2": False},
        "kitchenFumes": {"1": True, "2": False},
        "occupationExposure": {"1": True, "2": False},
        "familyCancerHistory": {"1": True, "2": False},
        "recentSymptoms": {"1": True, "2": False},
    }
    if question_key in numeric_map and lowered in numeric_map[question_key]:
        bool_val = numeric_map[question_key][lowered]
        return {
            "raw": text,
            "normalized": "yes" if bool_val else "no",
            "bool": bool_val,
            "note": "numeric_map",
        }
    yes_values = {
        "1",
        "yes",
        "y",
        "true",
        "smoke",
        "smoked",
        "i smoke",
        "former smoker",
        "used to smoke",
        "exposed",
        "是",
        "有",
        "有的",
        "存在",
        "接触",
    }
    no_values = {
        "2",
        "no",
        "n",
        "false",
        "never",
        "none",
        "non-smoker",
        "non smoker",
        "否",
        "无",
        "没有",
        "不",
        "不吸烟",
        "不抽烟",
    }
    if lowered in yes_values:
        return {"raw": text, "normalized": "yes", "bool": True}
    if lowered in no_values:
        return {"raw": text, "normalized": "no", "bool": False}
    if lowered in {"1", "2"}:
        assumed = "yes" if lowered == "1" else "no"
        return {
            "raw": text,
            "normalized": assumed,
            "bool": lowered == "1",
            "note": "numeric_assumed",
        }
    if not text:
        return {"raw": text, "normalized": "unknown", "bool": None, "note": "empty"}
    return {"raw": text, "normalized": text, "bool": None, "note": "unparsed"}


def build_content_text(answers_list: list[Dict[str, Any]]) -> str:
    if not answers_list:
        return ""
    lines = ["Screening Report", ""]
    for idx, item in enumerate(answers_list, start=1):
        question = str(item.get("question") or "").strip()
        answer = str(item.get("answer") or "").strip()
        if not question and not answer:
            continue
        lines.append(f"{idx}. {question or 'Question'}")
        lines.append(f"   Answer: {answer or 'N/A'}")
        lines.append("")
    return "\n".join(lines).strip()


def system_init() -> Flask:
    """
    Initialize and configure the Flask application.

    This is the single bootstrap point for wiring in:
    - multi-agent modules
    - CT inference modules
    - additional API routes or services
    """
    app = Flask(
        __name__,
        static_folder=str(UI_DIR),
        static_url_path="/ui",  # Serve /ui/* assets directly
    )

    # Enable CORS for all routes to simplify frontend integration.
    CORS(app)

    # 启动时验证静态文件目录和关键文件
    logger.info(f"[static] UI_DIR = {UI_DIR}, exists: {UI_DIR.exists()}")
    common_dir = UI_DIR / "common"
    marked_file = common_dir / "marked.min.js"
    logger.info(f"[static] common marked = {marked_file}, exists: {marked_file.exists()}")
    if not marked_file.exists():
        logger.warning(f"[static] marked.min.js not found at {marked_file}, CDN fallback will be used")

    @app.after_request
    def add_permissions_policy(response: Response) -> Response:
        response.headers["Permissions-Policy"] = "microphone=(self), camera=(), geolocation=()"
        return response

    @app.route("/health", methods=["GET"])
    def health() -> Any:
        status: Dict[str, Any] = {
            "status": "ok",
            "message": "system online",
            "components": {
                "frontend": "ready",
                "backend": "ready",
                "multi_agent": "placeholder",
                "ct_inference": "placeholder",
            },
        }
        return jsonify(status)

    @app.route("/api/whoami", methods=["GET"])
    @require_firebase_auth()
    def whoami() -> Any:
        return jsonify(
            {
                "uid": getattr(g, "firebase_uid", "") or "",
                "projectId_used_by_admin": get_project_id(),
                "token_aud": getattr(g, "firebase_aud", "") or "",
                "token_iss": getattr(g, "firebase_iss", "") or "",
            }
        )

    @app.route("/api/questionnaire_status", methods=["GET"])
    def questionnaire_status() -> Any:
        headers = {"Accept": "application/json", **_identity_headers()}
        auth_header = request.headers.get("Authorization")
        if auth_header:
            headers["Authorization"] = auth_header

        if _is_port_open(SCREEN_HOST, SCREEN_PORT):
            try:
                proxied = requests.get(
                    f"{SCREEN_BACKEND_URL}/api/questionnaire_status",
                    params=request.args,
                    timeout=6,
                    headers=headers,
                )
                if proxied.status_code < 400:
                    try:
                        data = proxied.json()
                    except ValueError:
                        data = {}
                    status = data.get("status") or data.get("current_system") or "idle"
                    return jsonify({"ok": True, "status": status}), 200
                logger.warning(
                    "[questionnaire_status] screen backend returned status=%s",
                    proxied.status_code,
                )
            except requests.RequestException as exc:
                logger.warning("[questionnaire_status] screen backend unreachable: %s", exc)

        return jsonify({"ok": True, "status": "idle"}), 200

    @app.route("/api/reports", methods=["GET"])
    @require_firebase_auth()
    def list_reports() -> Any:
        """
        List reports for the current patient from Firestore.
        Returns reports where patientId == current user's uid.
        """
        uid = getattr(g, "firebase_uid", None)
        if not uid:
            return jsonify({"error": "Unauthorized"}), 401
        
        try:
            db = get_firestore_client()
            # Query reports collection where patientId == uid
            reports_ref = db.collection("reports")
            query = reports_ref.where("patientId", "==", uid).order_by("createdAt", direction=admin_firestore.Query.DESCENDING).limit(50)
            
            docs = query.stream()
            reports = []
            for doc in docs:
                data = doc.to_dict()
                if data:
                    # Convert Firestore timestamps to ISO strings
                    report_item = {
                        "reportId": data.get("reportId", doc.id),
                        "patientId": data.get("patientId", ""),
                        "doctorId": data.get("doctorId", ""),
                        "screeningId": data.get("screeningId", ""),
                        "riskLevel": data.get("riskLevel", "low"),
                        "pdfPath": data.get("pdfPath", ""),
                        "status": data.get("status", "ready"),
                    }
                    # Handle timestamps
                    created_at = data.get("createdAt")
                    if created_at:
                        if hasattr(created_at, "isoformat"):
                            report_item["createdAt"] = created_at.isoformat()
                        else:
                            report_item["createdAt"] = str(created_at)
                    else:
                        report_item["createdAt"] = None
                    
                    updated_at = data.get("updatedAt")
                    if updated_at:
                        if hasattr(updated_at, "isoformat"):
                            report_item["updatedAt"] = updated_at.isoformat()
                        else:
                            report_item["updatedAt"] = str(updated_at)
                    else:
                        report_item["updatedAt"] = None
                    
                    # Optional fields
                    if "contentText" in data:
                        report_item["contentText"] = data["contentText"]
                    if "answersRaw" in data:
                        report_item["answersRaw"] = data["answersRaw"]
                    if "answersNormalized" in data:
                        report_item["answersNormalized"] = data["answersNormalized"]
                    
                    # Generate signed URL for PDF if pdfPath exists
                    pdf_path = report_item.get("pdfPath")
                    if pdf_path:
                        try:
                            bucket = get_storage_bucket()
                            blob = bucket.blob(pdf_path)
                            if blob.exists():
                                # Generate signed URL valid for 1 hour
                                from datetime import timedelta
                                url = blob.generate_signed_url(
                                    expiration=timedelta(hours=1),
                                    method="GET"
                                )
                                report_item["pdfUrl"] = url
                        except Exception as exc:
                            logger.warning(f"[reports] Failed to generate signed URL for {pdf_path}: {exc}")
                            report_item["pdfUrl"] = None
                    else:
                        report_item["pdfUrl"] = None
                    
                    reports.append(report_item)
            
            return jsonify({
                "ok": True,
                "reports": reports,
                "stats": {
                    "total_reports": len(reports),
                    "total_size_mb": 0,  # Could calculate from Storage if needed
                    "reports_dir": "",
                },
            }), 200
            
        except Exception as exc:  # noqa: BLE001
            logger.error(f"[reports] Firestore query failed: {exc}", exc_info=True)
            # Fallback: try screen backend if available
            if _is_port_open(SCREEN_HOST, SCREEN_PORT):
                try:
                    headers = {"Accept": "application/json", **_identity_headers()}
                    auth_header = request.headers.get("Authorization")
                    if auth_header:
                        headers["Authorization"] = auth_header
                    proxied = requests.get(
                        f"{SCREEN_BACKEND_URL}/api/reports",
                        params=request.args,
                        timeout=12,
                        headers=headers,
                    )
                    if proxied.status_code < 400:
                        try:
                            data = proxied.json()
                        except ValueError:
                            data = None
                        if isinstance(data, dict) and "reports" in data:
                            return jsonify(data), 200
                        if isinstance(data, list):
                            return jsonify(
                                {
                                    "reports": data,
                                    "stats": {
                                        "total_reports": len(data),
                                        "total_size_mb": 0,
                                        "reports_dir": "",
                                    },
                                }
                            ), 200
                except requests.RequestException:
                    pass
            
            return jsonify(
                {
                    "ok": False,
                    "error": "Failed to query reports",
                    "reports": [],
                    "stats": {
                        "total_reports": 0,
                        "total_size_mb": 0,
                        "reports_dir": "",
                    },
                }
            ), 500

    @app.route("/api/reports/content/<path:filename>", methods=["GET"])
    def report_content_proxy(filename: str) -> Any:
        return proxy_screen(f"/api/reports/content/{filename}", "GET")

    @app.route("/api/reports/download/<path:filename>", methods=["GET"])
    def report_download_proxy(filename: str) -> Any:
        return proxy_screen(f"/api/reports/download/{filename}", "GET")

    @app.route("/api/reports/export_pdf/<path:filename>", methods=["GET"])
    def report_export_pdf_proxy(filename: str) -> Any:
        return proxy_screen(f"/api/reports/export_pdf/{filename}", "GET")

    @app.route("/api/reports", methods=["POST"])
    @require_firebase_auth()
    def create_report() -> Any:
        uid = getattr(g, "firebase_uid", None)
        if not uid:
            return jsonify({"error": "Unauthorized"}), 401

        try:
            pdf_bytes, meta = _decode_pdf_bytes()
        except Exception as exc:  # noqa: BLE001
            return jsonify({"error": "Invalid payload", "detail": str(exc)}), 400

        if not pdf_bytes:
            return jsonify({"error": "PDF is required"}), 400
        if len(pdf_bytes) > MAX_PDF_BYTES:
            return jsonify({"error": "PDF too large", "limit": MAX_PDF_BYTES}), 413
        content_type = request.headers.get("Content-Type", "")
        if "multipart/form-data" in (content_type or "").lower():
            if "pdf" not in request.files:
                return jsonify({"error": "PDF file field 'pdf' is required"}), 400
        if content_type and "pdf" not in content_type and "multipart/form-data" not in content_type:
            return jsonify({"error": "Unsupported Content-Type, expected application/pdf or multipart/form-data"}), 415

        answers_list = _parse_answers_payload(meta.get("answers"))
        answers_raw = _answers_list_to_map(answers_list)
        normalized = normalize_answers(answers_raw)
        answers_normalized: Dict[str, Any] = dict(normalized)
        for key in ("smokingHistory", "passiveSmoking", "kitchenFumes", "occupationExposure", "familyCancerHistory", "recentSymptoms"):
            if key in normalized:
                answers_normalized[key] = map_option(key, normalized.get(key))
        if "gender" in normalized:
            answers_normalized["gender"] = normalize_gender(normalized.get("gender"))

        mode = (meta.get("mode") or "voice").strip().lower()
        session_id = (meta.get("sessionId") or "").strip() or None

        report_id = meta.get("reportId") or session_id or _build_report_id(uid)
        if session_id and report_id != session_id:
            logger.info("[reports] overriding reportId to session_id reportId=%s session_id=%s", report_id, session_id)
            report_id = session_id
        screening_id = meta.get("screeningId") or _build_screening_id()
        doctor_id = (meta.get("doctorId") or "").strip() or None
        if doctor_id == "unassigned":
            doctor_id = None
        risk_level = (meta.get("riskLevel") or "low").strip() or "low"
        content_text = meta.get("reportText") or build_content_text(answers_list)

        summary_user_info = {
            "name": normalized.get("fullName") or "未知",
            "gender": normalize_gender(normalized.get("gender")) if normalized.get("gender") else "未知",
            "yearOfBirth": normalized.get("yearOfBirth") or "未知",
        }

        try:
            bucket = get_storage_bucket()
            path = f"reports/{uid}/{report_id}.pdf"
            blob = bucket.blob(path)
            blob.upload_from_string(pdf_bytes, content_type="application/pdf")
        except Exception as exc:  # noqa: BLE001
            logger.error("[reports] storage upload failed uid=%s reportId=%s bytes=%s error=%s", uid, report_id, len(pdf_bytes), exc)
            return jsonify({"error": "Storage upload failed", "detail": str(exc)}), 500

        try:
            db = get_firestore_client()
            # Reports schema (source of truth):
            # reports/{reportId}: patientId, doctorId, reportId, screeningId, riskLevel, createdAt.
            # Optional: pdfPath, contentText, updatedAt.
            payload = {
                "createdAt": admin_firestore.SERVER_TIMESTAMP,
                "updatedAt": admin_firestore.SERVER_TIMESTAMP,
                "doctorId": doctor_id,
                "patientId": uid,
                "reportId": report_id,
                "screeningId": screening_id,
                "riskLevel": risk_level,
                "pdfPath": path,
                "source": mode,
                "status": "ready",
                "answersRaw": answers_raw,
                "answersNormalized": answers_normalized,
                "summaryUserInfo": summary_user_info,
                "contentText": content_text,
            }
            doc_ref = db.collection("reports").document(report_id)
            doc_ref.set(payload, merge=True)
            doc_id = doc_ref.id
        except Exception as exc:  # noqa: BLE001
            logger.error("[reports] firestore write failed uid=%s reportId=%s error=%s", uid, report_id, exc)
            return jsonify({"error": "Firestore write failed", "detail": str(exc)}), 500

        logger.info(
            "[reports] created uid=%s reportId=%s docId=%s bytes=%s mode=%s answers=%s",
            uid,
            report_id,
            doc_id,
            len(pdf_bytes),
            mode,
            len(answers_raw),
        )
        return jsonify(
            {
                "ok": True,
                "docId": doc_id,
                "reportId": report_id,
                "screeningId": screening_id,
                "pdfPath": path,
                "patientId": uid,
                "createdAt": admin_firestore.SERVER_TIMESTAMP,
            }
        )

    @app.route("/api/health/firebase", methods=["GET"])
    def health_firebase() -> Any:
        try:
            db = get_firestore_client()
        except Exception as exc:  # noqa: BLE001
            return jsonify({"ok": False, "error": str(exc)}), 503

        try:
            doc_ref = db.collection("_health").document("ping")
            doc_ref.set({"ok": True, "ts": admin_firestore.SERVER_TIMESTAMP}, merge=True)
            snap = doc_ref.get()
            return jsonify({
                "ok": True,
                "exists": snap.exists,
                "data": snap.to_dict() if snap.exists else {},
            })
        except Exception as exc:  # noqa: BLE001
            return jsonify({"ok": False, "error": str(exc)}), 500

    @app.route("/api/chat", methods=["GET", "HEAD"])
    def chat_probe() -> Any:
        if request.method == "HEAD":
            return ("", 200)

        downstream = "unavailable"
        target_url = f"{_get_chat_base()}/api/chat"
        try:
            requests.get(target_url, timeout=2)
            downstream = "ok"
        except requests.RequestException:
            downstream = "unavailable"

        payload = {
            "ok": True,
            "service": "maq_api",
            "message": "chat endpoint ready",
            "downstream": downstream,
        }
        return jsonify(payload), 200

    @app.route("/api/chat", methods=["POST"])
    @require_firebase_auth()
    def chat() -> Any:
        target_base = _get_chat_base()
        parsed_target = urlparse(target_base)
        target_host = parsed_target.hostname or CHAT_HOST
        target_port = parsed_target.port or CHAT_PORT

        target_url = f"{target_base}/api/chat"
        if not is_chat_running():
            logger.warning("[chat] service unavailable at %s", target_base)
            return (
                jsonify(
                    {
                        "error": "chat_unavailable",
                        "message": f"Chat service is not running on {target_host}:{target_port}",
                    }
                ),
                503,
            )

        content_type = request.content_type or "application/json"
        raw_body = request.get_data(cache=True, as_text=False) or b""
        auth_header = request.headers.get("Authorization")

        headers = {
            "Content-Type": content_type,
            "Accept": "application/json",
            **_identity_headers(),
        }
        if auth_header:
            headers["Authorization"] = auth_header

        try:
            proxied = requests.post(
                target_url,
                data=raw_body,
                headers=headers,
                timeout=15,
            )
            print(f"[/api/chat proxy] -> {target_url} status={proxied.status_code}")
        except requests.RequestException as exc:
            print(f"[/api/chat proxy] -> {target_url} error={exc}")
            return jsonify({"error": f"backend chat service unreachable: {exc}"}), 502

        resp_content = proxied.content
        resp_headers = {"Content-Type": proxied.headers.get("Content-Type", "application/json")}
        return Response(resp_content, status=proxied.status_code, headers=resp_headers)

    def add_cors(resp: Response) -> Response:
        resp.headers["Access-Control-Allow-Origin"] = "*"
        resp.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization"
        resp.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
        return resp

    @app.route("/api/ct/analyze", methods=["GET", "POST", "OPTIONS"])
    def ct_analyze() -> Any:
        global CT_MODEL
        logger.info("[CT] /api/ct/analyze %s hit (main.py)", request.method)
        if request.method == "OPTIONS":
            resp = jsonify({"ok": True})
            return add_cors(resp), 204

        if request.method == "GET":
            resp = jsonify({"ok": False, "message": "Use POST with JSON {studyId}"})
            return add_cors(resp), 200

        payload: Dict[str, Any] = {}
        doc_ref = None
        study_id = ""
        try:
            if not request.is_json:
                resp = jsonify({"ok": False, "error": "Content-Type must be application/json"})
                return add_cors(resp), 400
            payload = request.get_json(silent=True)
            if not isinstance(payload, dict):
                resp = jsonify({"ok": False, "error": "Invalid JSON body"})
                return add_cors(resp), 400
            t0 = time.time()
            logger.info("[CT] analyze start payload=%s", payload)
            study_id = payload.get("studyId")
            if not isinstance(study_id, str) or not study_id.strip():
                resp = jsonify({"ok": False, "error": "studyId is required"})
                return add_cors(resp), 400
            study_id = study_id.strip()

            with _study_lock(study_id):
                patient_id = payload.get("patientId")
                if patient_id is not None and not isinstance(patient_id, str):
                    patient_id = str(patient_id)
                storage_prefix = payload.get("storagePrefix")
                if storage_prefix is not None and not isinstance(storage_prefix, str):
                    storage_prefix = str(storage_prefix)

                if CT_MODEL is None:
                    resp = jsonify({"ok": False, "error": "CT model not loaded at startup"})
                    return add_cors(resp), 500

                db = _get_ct_firestore()

                doc_ref = db.collection("ctStudies").document(str(study_id))
                logger.info("[CT] firestore doc path=ctStudies/%s", study_id)
                doc_snap = doc_ref.get()
                study_data = doc_snap.to_dict() or {}
                if not storage_prefix:
                    storage_prefix = study_data.get("storagePrefix")
                if not patient_id:
                    patient_id = study_data.get("patientId")

                if not storage_prefix:
                    error_payload = {
                        "analysisStatus": "error",
                        "analysisUpdatedAt": admin_firestore.SERVER_TIMESTAMP,
                        "analysisCompletedAt": admin_firestore.SERVER_TIMESTAMP,
                        "analysisError": {
                            "message": "storagePrefix is required",
                            "stack": "",
                        },
                        "updatedAt": admin_firestore.SERVER_TIMESTAMP,
                    }
                    doc_ref.set(error_payload, merge=True)
                    resp = jsonify({"ok": False, "error": "storagePrefix is required"})
                    return add_cors(resp), 400

                running_update = {
                    "analysisStatus": "running",
                    "analysisStartedAt": admin_firestore.SERVER_TIMESTAMP,
                    "analysisCompletedAt": admin_firestore.DELETE_FIELD,
                    "analysisUpdatedAt": admin_firestore.SERVER_TIMESTAMP,
                    "analysisError": None,
                    "updatedAt": admin_firestore.SERVER_TIMESTAMP,
                }
                if patient_id:
                    running_update["patientId"] = patient_id
                if storage_prefix:
                    running_update["storagePrefix"] = storage_prefix
                doc_ref.set(running_update, merge=True)

                req_id = uuid.uuid4().hex[:8]
                tmp_dir = Path("/tmp") / "aegis_ct" / f"{study_id}_{req_id}"
                tmp_dir.mkdir(parents=True, exist_ok=True)

                file_count = _download_all_dicoms(storage_prefix, tmp_dir)
                if file_count == 0:
                    raise RuntimeError("No DICOM files downloaded from storage.")

                device = "cuda" if torch.cuda.is_available() else "cpu"
                model_device = next(CT_MODEL.parameters()).device
                logger.info("[CT] inference start device=%s model=%s pid=%s", device, model_device, os.getpid())
                if torch.cuda.is_available():
                    CT_MODEL.to("cuda")
                    logger.info("[CT] model moved to cuda device=%s", next(CT_MODEL.parameters()).device)
                    try:
                        _ = torch.ones(1, device="cuda") * 2
                        logger.info("[CT] cuda smoke ok")
                    except Exception as exc:  # noqa: BLE001
                        logger.exception("[CT] cuda smoke failed: %s", exc)
                else:
                    logger.info("[CT] cuda not available; using cpu")

                start_ts = time.perf_counter()
                inference = infer_dicom_dir(
                    str(tmp_dir),
                    device=device,
                    model=CT_MODEL,
                    weights_path=str(CT_MODEL_PATH),
                )
                elapsed_ms = int((time.perf_counter() - start_ts) * 1000)
                logger.info("[CT] inference end elapsedMs=%s", elapsed_ms)

                risk_level = "unknown"
                if isinstance(inference, dict):
                    risk_level = inference.get("label") or inference.get("riskLevel") or "unknown"
                aggregate = inference.get("aggregate", {}) if isinstance(inference, dict) else {}
                max_prob = aggregate.get("max_prob_malignant")
                mean_prob = aggregate.get("mean_prob_malignant")
                num_slices = aggregate.get("num_slices_scored")
                summary_parts = []
                if num_slices is not None:
                    summary_parts.append(f"{num_slices} slices scored")
                if isinstance(max_prob, (int, float)):
                    summary_parts.append(f"max prob {max_prob:.3f}")
                if isinstance(mean_prob, (int, float)):
                    summary_parts.append(f"mean prob {mean_prob:.3f}")
                summary = " | ".join(summary_parts) if summary_parts else "CT inference completed."

                nodules = []
                if isinstance(inference, dict) and isinstance(inference.get("nodules"), list):
                    nodules = inference.get("nodules") or []
                elif isinstance(inference, dict) and isinstance(inference.get("slice_scores"), list):
                    nodules = [
                        {
                            "location": item.get("file") or item.get("index"),
                            "confidence": item.get("prob_malignant"),
                            "index": item.get("index"),
                        }
                        for item in inference.get("slice_scores")
                        if isinstance(item, dict)
                    ]

                model_device_str = str(model_device)
                analysis_result = {
                    "summary": summary,
                    "riskLevel": risk_level,
                    "nodules": nodules,
                    "noduleCount": len(nodules),
                    "elapsedMs": elapsed_ms,
                    "modelDevice": model_device_str,
                }

                analysis = {
                    "status": "done",
                    "updatedAt": admin_firestore.SERVER_TIMESTAMP,
                    "model": Path(CT_MODEL_PATH).name if CT_MODEL_PATH else "",
                    "result": inference,
                    "fileCount": file_count,
                    "device": device,
                    "elapsedMs": elapsed_ms,
                }
                doc_ref.set(
                    {
                        "analysis": analysis,
                        "analysisStatus": "done",
                        "analysisUpdatedAt": admin_firestore.SERVER_TIMESTAMP,
                        "analysisCompletedAt": admin_firestore.SERVER_TIMESTAMP,
                        "analysisResult": analysis_result,
                        "analysisError": None,
                        "status": "analyzed",
                        "updatedAt": admin_firestore.SERVER_TIMESTAMP,
                        "fileCount": file_count,
                    },
                    merge=True,
                )
                try:
                    readback = doc_ref.get()
                    data = {}
                    has_result = False
                    if readback.exists:
                        data = readback.to_dict() or {}
                        has_result = "analysisResult" in data and data.get("analysisResult") not in (None, "")
                    logger.info(
                        "[CT] firestore readback exists=%s keys=%s analysisResult=%s",
                        readback.exists,
                        sorted(list(data.keys())),
                        has_result,
                    )
                except Exception:  # noqa: BLE001
                    logger.exception("[CT] firestore readback failed")

                result = {
                    "ok": True,
                    "studyId": study_id,
                    "patientId": patient_id,
                    "storagePrefix": storage_prefix,
                    "analysisResult": analysis_result,
                    "analysis": inference,
                    "result": inference,
                    "device": device,
                    "elapsedMs": elapsed_ms,
                }
                logger.info("[CT] analyze ok elapsed=%.2fs", time.time() - t0)
                return add_cors(jsonify(result)), 200
        except Exception as exc:  # noqa: BLE001
            tb = traceback.format_exc()
            logger.exception("[CT] analyze failed")
            if doc_ref is not None:
                try:
                    doc_ref.set(
                        {
                            "analysis": {
                                "status": "error",
                                "updatedAt": admin_firestore.SERVER_TIMESTAMP,
                                "model": Path(CT_MODEL_PATH).name if CT_MODEL_PATH else "",
                                "error": str(exc),
                            },
                            "analysisStatus": "error",
                            "analysisUpdatedAt": admin_firestore.SERVER_TIMESTAMP,
                            "analysisCompletedAt": admin_firestore.SERVER_TIMESTAMP,
                            "analysisError": {
                                "message": str(exc),
                                "stack": tb,
                            },
                            "updatedAt": admin_firestore.SERVER_TIMESTAMP,
                        },
                        merge=True,
                    )
                except Exception:  # noqa: BLE001
                    logger.exception("[CT] analyze failed to persist error state")
            err = {
                "ok": False,
                "error": str(exc),
                "studyId": study_id if isinstance(payload, dict) else "",
            }
            resp = jsonify(err)
            resp.status_code = 500
            return add_cors(resp)

    def proxy_screen(path: str, method: str = "GET"):
        target_url = f"{SCREEN_BACKEND_URL}{path}"
        auth_header = request.headers.get("Authorization")
        common_headers = {
            "Accept": "application/json",
            **_identity_headers(),
        }
        if auth_header:
            common_headers["Authorization"] = auth_header
        # default timeout and extended timeout for MetaGPT endpoints which may take longer
        timeout = 20
        if "/metagpt" in path or "/api/metagpt" in path:
            timeout = int(os.environ.get("SCREEN_METAGPT_TIMEOUT", "120"))
        try:
            if method.upper() == "GET":
                proxied = requests.get(
                    target_url,
                    params=request.args,
                    timeout=timeout,
                    headers=common_headers,
                )
            else:
                raw_body = request.get_data(cache=True, as_text=False) or b""
                proxied = requests.request(
                    method.upper(),
                    target_url,
                    params=request.args if method.upper() == "DELETE" else None,
                    data=raw_body,
                    headers={
                        "Content-Type": request.headers.get("Content-Type", "application/json"),
                        **common_headers,
                    },
                    timeout=timeout,
                )
        except requests.RequestException as exc:
            print(f"[screen proxy] -> {target_url} error={exc}")
            return jsonify({"error": "screen backend unreachable", "detail": str(exc)}), 502
        content_type = proxied.headers.get("Content-Type", "application/json")
        resp_content: Any = proxied.content
        if "text/html" in content_type.lower():
            # Rewrite HTML asset URLs so screen CSS/JS load through the proxy.
            import re

            html = proxied.text or ""

            def rewrite_asset(match):
                attr = match.group(1)
                quote = match.group(2) or ""
                url = match.group(3) or ""
                url_stripped = url.strip()
                if url_stripped.startswith("/screen_embedded/"):
                    return match.group(0)
                if url_stripped.startswith("http://") or url_stripped.startswith("https://") or url_stripped.startswith("data:") or url_stripped.startswith("mailto:") or url_stripped.startswith("//"):
                    return match.group(0)
                if url_stripped.startswith("/"):
                    new_url = f"/screen_embedded{url_stripped}"
                else:
                    new_url = f"/screen_embedded/{url_stripped}"
                return f'{attr}={quote}{new_url}{quote}'

            html = re.sub(r'(?i)(src|href)=([\'"]?)([^\'"\s>]+)\2', rewrite_asset, html)
            if "<base" not in html.lower():
                head_close = html.lower().find("</head>")
                base_tag = '<base href="/screen_embedded/">'
                if head_close != -1:
                    html = html[:head_close] + base_tag + html[head_close:]
                else:
                    html = base_tag + html
            resp_content = html
        resp_headers = {"Content-Type": content_type}
        for header_name in ("Content-Disposition", "Content-Length", "Cache-Control"):
            header_value = proxied.headers.get(header_name)
            if header_value:
                resp_headers[header_name] = header_value
        return Response(resp_content, status=proxied.status_code, headers=resp_headers)

    @app.route("/api/screen/voice/start", methods=["POST"])
    @require_firebase_auth()
    def screen_voice_start():
        return proxy_screen("/voice/start", "POST")

    @app.route("/api/screen/voice/stop", methods=["POST"])
    @require_firebase_auth()
    def screen_voice_stop():
        return proxy_screen("/voice/stop", "POST")

    @app.route("/api/screen/voice/status", methods=["GET"])
    @require_firebase_auth()
    def screen_voice_status():
        return proxy_screen("/voice/status", "GET")

    @app.route("/api/screen/metagpt/start", methods=["POST"])
    @require_firebase_auth()
    def screen_metagpt_start():
        return proxy_screen("/metagpt/start", "POST")

    @app.route("/api/screen/metagpt/next", methods=["POST"])
    @require_firebase_auth()
    def screen_metagpt_next():
        return proxy_screen("/metagpt/next", "POST")

    @app.route("/api/screen/health", methods=["GET"])
    @require_firebase_auth()
    def screen_health():
        target_url = f"{SCREEN_BACKEND_URL}/health"
        auth_header = request.headers.get("Authorization")
        headers = {"Accept": "application/json", **_identity_headers()}
        if auth_header:
            headers["Authorization"] = auth_header
        try:
            proxied = requests.get(
                target_url,
                timeout=5,
                headers=headers,
                params=request.args,
            )
        except requests.RequestException as exc:
            logger.warning("[screen health] backend unreachable: %s", exc)
            return (
                jsonify(
                    {
                        "ok": True,
                        "screen_backend": "down",
                        "error": str(exc),
                    }
                ),
                200,
            )

        if proxied.status_code >= 400:
            logger.warning("[screen health] backend returned status=%s", proxied.status_code)
            return (
                jsonify(
                    {
                        "ok": True,
                        "screen_backend": "down",
                        "error": f"screen backend returned status {proxied.status_code}",
                    }
                ),
                200,
            )

        return (
            jsonify(
                {
                    "ok": True,
                    "screen_backend": "up",
                    "screen_url": SCREEN_BACKEND_URL,
                }
            ),
            200,
        )

    @app.route("/api/metagpt/agents_status", methods=["GET"])
    @require_firebase_auth()
    def proxy_metagpt_agents_status():
        """Proxy to screen's /api/metagpt/agents_status for diagnostics."""
        return proxy_screen("/api/metagpt/agents_status", "GET")

    # Static routes
    @app.route("/", methods=["GET"])
    def serve_root() -> Any:
        return send_from_directory(UI_DIR, "index.html")

    @app.route("/doctor", methods=["GET"])
    def serve_doctor() -> Any:
        doctor_dir = UI_DIR / "doctor"
        index_path = doctor_dir / "index.html"
        if index_path.exists():
            return send_from_directory(doctor_dir, "index.html")
        # Fallback to dashboard if a dedicated index is absent.
        return send_from_directory(doctor_dir, "dashboard.html")

    @app.route("/doctor/index.html", methods=["GET"])
    def serve_doctor_index() -> Any:
        doctor_dir = UI_DIR / "doctor"
        index_path = doctor_dir / "index.html"
        if index_path.exists():
            return send_from_directory(doctor_dir, "index.html")
        # Fallback to dashboard if a dedicated index is absent.
        return send_from_directory(doctor_dir, "dashboard.html")

    @app.route("/doctor/dashboard.html", methods=["GET"])
    @app.route("/doctor/dashboard", methods=["GET"])
    def serve_doctor_dashboard() -> Any:
        return send_from_directory(UI_DIR / "doctor", "dashboard.html")

    @app.route("/patient", methods=["GET"])
    def serve_patient() -> Any:
        return send_from_directory(UI_DIR / "patient", "index.html")

    @app.route("/patient/index.html", methods=["GET"])
    def serve_patient_index() -> Any:
        # Compatibility route for direct index paths.
        return send_from_directory(UI_DIR / "patient", "index.html")

    @app.route("/patient/screening.html", methods=["GET"])
    @app.route("/patient/screening", methods=["GET"])
    def serve_patient_screening() -> Any:
        """
        Serve the patient screening page.
        If the screen backend is running, proxy to its root (so the UI served by screen/app.py is shown).
        Otherwise, fall back to the local static patient/screening.html.
        """
        # By default serve the local integrated UI so the main site's chrome and styles are preserved.
        # For development/testing you can force using the remote screen backend by adding ?use_remote=1
        use_remote = str(request.args.get("use_remote") or "").strip().lower() in ("1", "true", "yes")
        if use_remote and _is_port_open(SCREEN_HOST, SCREEN_PORT):
            try:
                return proxy_screen("/", "GET")
            except Exception:
                # fall through to static fallback
                pass
        # Serve the local static screening page (preferred for integrated UI)
        return send_from_directory(UI_DIR / "patient", "screening.html")
        # Embedded proxy routes for iframe content
    @app.route("/screen_embedded", defaults={"path": ""}, methods=["GET"])
    @app.route("/screen_embedded/<path:path>", methods=["GET"])
    def screen_embedded(path: str) -> Any:
        """Proxy arbitrary paths to the screen backend for embedding (iframe)."""
        target = f"/{path}" if path else "/"
        return proxy_screen(target, "GET")

    @app.route("/screen_fragment", methods=["GET"])
    def screen_fragment() -> Any:
        """
        Fetch the screen backend root HTML, extract the <body> inner HTML,
        perform lightweight cleanup (remove common sidebar/header blocks),
        rewrite relative asset URLs to go through `/screen_embedded/*` proxy,
        and return the fragment so the parent page can insert it directly.

        This keeps the main UI chrome (sidebar/header) in the parent and
        embeds only the screen application's content.
        """
        if not _is_port_open(SCREEN_HOST, SCREEN_PORT):
            return jsonify({"ok": False, "error": "screen backend unreachable"}), 502

        try:
            proxied = requests.get(f"{SCREEN_BACKEND_URL}/", timeout=10, headers={"Accept": "text/html"})
        except requests.RequestException as exc:
            return jsonify({"ok": False, "error": "failed to fetch screen backend", "detail": str(exc)}), 502

        if proxied.status_code >= 400:
            return jsonify({"ok": False, "error": "screen backend returned error", "status": proxied.status_code}), 502

        html = proxied.text or ""

        # Lightweight extraction of <body>...</body>
        lower = html.lower()
        bstart = lower.find("<body")
        if bstart == -1:
            body_inner = html
        else:
            # find the start of the body content after the opening tag
            btag_end = html.find(">", bstart)
            if btag_end == -1:
                body_inner = html
            else:
                bend = lower.rfind("</body>")
                if bend == -1:
                    body_inner = html[btag_end + 1 :]
                else:
                    body_inner = html[btag_end + 1 : bend]

        # Remove common sidebar/header blocks to avoid duplicate chrome.
        import re

        # remove <aside>...</aside>
        body_inner = re.sub(r"(?is)<aside\b.*?</aside>", "", body_inner)
        # remove header-like blocks
        body_inner = re.sub(r"(?is)<header\b.*?</header>", "", body_inner)
        # remove elements with class names likely to be sidebars/hero areas
        body_inner = re.sub(r'(?is)<div\b[^>]*class=["\'][^"\']*(sidebar|hero|page-header|topbar)[^"\']*["\'][^>]*>.*?</div>', "", body_inner)

        # Rewrite relative asset URLs (src/href) to go through /screen_embedded proxy.
        # e.g. <script src="static/app.js"> -> src="/screen_embedded/static/app.js"
        def rewrite_asset(match):
            attr = match.group(1)
            quote = match.group(2) or ""
            url = match.group(3) or ""
            url_stripped = url.strip()
            # If already absolute (http/https) or data:, leave as-is
            if url_stripped.startswith("http://") or url_stripped.startswith("https://") or url_stripped.startswith("data:") or url_stripped.startswith("mailto:") or url_stripped.startswith("//"):
                return match.group(0)
            # If it's an absolute path starting with '/', proxy it
            if url_stripped.startswith("/"):
                new_url = f"/screen_embedded{url_stripped}"
            else:
                # relative path -> make it under /screen_embedded/
                new_url = f"/screen_embedded/{url_stripped}"
            return f'{attr}={quote}{new_url}{quote}'

        body_inner = re.sub(r'(?i)(src|href)=([\'"]?)([^\'"\s>]+)\2', rewrite_asset, body_inner)

        # Add a base to ensure relative links in fragment resolve to proxied root.
        base_tag = '<base href="/screen_embedded/">'
        fragment = base_tag + "\n" + body_inner
        return Response(fragment, status=200, headers={"Content-Type": "text/html"})

    @app.route("/patient/login", methods=["GET"])
    @app.route("/patient/login.html", methods=["GET"])
    def serve_patient_login() -> Any:
        # Serve the canonical login page for legacy /patient/login* URLs.
        return send_from_directory(UI_DIR, "login.html")

    @app.route("/script.js", methods=["GET"])
    def serve_root_script() -> Any:
        # Compatibility route for root-level script reference.
        return send_from_directory(UI_DIR, "script.js")

    @app.route("/static/style.css", methods=["GET"])
    def serve_static_style_direct() -> Any:
        """Direct route for legacy requests to /static/style.css (handles query params in browser)."""
        candidates = [
            UI_DIR / "static" / "style.css",
            UI_DIR / "style.css",
            UI_DIR / "patient" / "style.css",
        ]
        for p in candidates:
            if p.exists() and p.is_file():
                logger.info("[static/direct] serving style from %s", p)
                return send_from_directory(str(p.parent), p.name)
        logger.warning("[static/direct] style.css not found in candidates")
        abort(404)

    @app.route("/static/script.js", methods=["GET"])
    def serve_static_script_direct() -> Any:
        """Direct route for legacy requests to /static/script.js (handles query params in browser)."""
        candidates = [
            UI_DIR / "static" / "script.js",
            UI_DIR / "script.js",
            UI_DIR / "patient" / "script.js",
        ]
        for p in candidates:
            if p.exists() and p.is_file():
                logger.info("[static/direct] serving script from %s", p)
                return send_from_directory(str(p.parent), p.name)
        logger.warning("[static/direct] script.js not found in candidates")
        abort(404)

    @app.route("/style.css", methods=["GET"])
    def serve_root_style() -> Any:
        """
        Serve a root-level style.css for legacy pages that reference /style.css.
        Prefer root UI/style.css if present, otherwise fallback to patient/style.css.
        """
        root_style = UI_DIR / "style.css"
        patient_style = UI_DIR / "patient" / "style.css"
        if root_style.exists():
            return send_from_directory(UI_DIR, "style.css")
        if patient_style.exists():
            return send_from_directory(UI_DIR / "patient", "style.css")
        abort(404)

    @app.route("/patient/script.js", methods=["GET"])
    def serve_patient_script() -> Any:
        """
        Some pages reference /patient/script.js; ensure it resolves to the root script.js.
        """
        script_path = UI_DIR / "script.js"
        if script_path.exists():
            return send_from_directory(UI_DIR, "script.js")
        abort(404)

    @app.route("/static/<path:asset_path>", methods=["GET"])
    def serve_any_static(asset_path: str) -> Any:
        """
        Compatibility: serve files requested under /static/* by resolving common locations:
        - ui/static/{asset}
        - ui/{asset}
        - ui/patient/{asset}
        - ui/doctor/{asset}
        This prevents 404s when pages reference /static/style.css or /static/script.js.
        """
        # sanitize asset_path in case route captured query-like suffixes
        asset_path = asset_path.split("?", 1)[0].split("#", 1)[0]

        candidates = [
            UI_DIR / "static" / asset_path,
            UI_DIR / asset_path,
            UI_DIR / "patient" / asset_path,
            UI_DIR / "doctor" / asset_path,
        ]
        logger.info("[static] request for %s, candidates=%s", asset_path, [str(p) for p in candidates])
        for p in candidates:
            try:
                exists = p.exists() and p.is_file()
            except Exception:
                exists = False
            logger.info("[static] checking %s exists=%s", p, exists)
            if exists:
                logger.info("[static] serving %s", p)
                return send_from_directory(str(p.parent), p.name)
        logger.warning("[static] not found for %s", asset_path)
        abort(404)

    @app.route("/firebase-config.js", methods=["GET"])
    def serve_firebase_config() -> Any:
        # Compatibility route for Firebase config used by the frontend.
        return send_from_directory(UI_DIR, "firebase-config.js")

    @app.route("/resource/<path:resource_path>", methods=["GET"])
    def serve_resource(resource_path: str) -> Any:
        # Static assets under /ui/resource/*
        return send_from_directory(UI_DIR / "resource", resource_path)

    @app.route("/common/<path:filename>", methods=["GET"])
    def serve_common(filename: str) -> Any:
        """
        Serve common assets like marked.min.js from frontend/common/*
        This route must be defined BEFORE /patient/<path:patient_path> to avoid route conflicts.
        Route order is critical: more specific routes (like /common) must come before generic ones (like /patient).
        """
        common_dir = UI_DIR / "common"
        file_path = common_dir / filename
        # 安全检查：确保请求的文件在 common_dir 内（防止路径遍历攻击）
        try:
            file_path.resolve().relative_to(common_dir.resolve())
        except ValueError:
            logger.warning(f"[static/common] Path traversal attempt blocked: {filename}")
            abort(404)
        if not file_path.exists() or not file_path.is_file():
            logger.warning(f"[static/common] File not found: {filename} in {common_dir}")
            abort(404)
        return send_from_directory(str(common_dir), filename)

    @app.route("/patient/<path:patient_path>", methods=["GET"])
    def serve_patient_assets(patient_path: str) -> Any:
        # Serve patient-specific static assets and nested pages.
        if patient_path in PATIENT_ROOT_FALLBACKS:
            # Older login template references /patient/login.* resources even though the
            # canonical copies live at the UI root. Serve them from the root folder.
            return send_from_directory(UI_DIR, patient_path)
        return send_from_directory(UI_DIR / "patient", patient_path)

    @app.route("/doctor/<path:filename>", methods=["GET"])
    def doctor_static(filename: str) -> Any:
        # Serve doctor-specific static assets and nested pages.
        return send_from_directory(os.path.join(UI_DIR, "doctor"), filename)

    @app.route("/favicon.ico", methods=["GET"])
    def serve_favicon() -> Any:
        favicon_path = UI_DIR / "favicon.ico"
        if favicon_path.exists():
            return send_from_directory(UI_DIR, "favicon.ico")
        # Avoid noisy 404s when no favicon is present.
        return ("", 204)

    @app.route("/<path:filename>", methods=["GET"])
    def serve_root_files(filename: str) -> Any:
        # Serve only allowed root-level UI files from /ui to avoid 404s.
        if "/" in filename or filename not in ROOT_UI_ALLOWLIST:
            abort(404)
        return send_from_directory(UI_DIR, filename)

    # Serve any asset under /ui/*
    @app.route("/ui/<path:asset_path>", methods=["GET"])
    def serve_ui_assets(asset_path: str) -> Any:
        return send_from_directory(UI_DIR, asset_path)

    return app


def _is_port_open(host: str, port: int, timeout: float = 0.5) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(timeout)
        try:
            sock.connect((host, port))
            return True
        except OSError:
            return False


def _get_chat_base() -> str:
    target_base = (os.environ.get("NODE_CHAT_URL") or DEFAULT_NODE_CHAT_URL).rstrip("/")
    if "://" not in target_base:
        target_base = f"http://{target_base}"
    return target_base


def is_chat_running(timeout: float = 0.5) -> bool:
    target_base = _get_chat_base()
    try:
        resp = requests.get(f"{target_base}/health", timeout=timeout)
    except requests.RequestException:
        return False
    if resp.status_code != 200:
        return False
    try:
        payload = resp.json()
    except ValueError:
        return False
    # Support both {"ok": true} and {"status": "ok"} formats
    return payload.get("ok") is True or payload.get("status") == "ok"


def start_chatbot_node() -> Optional[subprocess.Popen]:
    global _chat_log_fp
    node_dir = BASE_DIR.parent / "node"
    script_path = node_dir / "chatbot.js"
    if not script_path.exists():
        logger.error("[chat] chatbot.js not found at %s", script_path)
        return None

    log_dir = BASE_DIR / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / "chatbot-node.log"
    try:
        _chat_log_fp = open(log_path, "ab")
    except OSError as exc:
        logger.warning("[chat] failed to open log file %s: %s", log_path, exc)
        _chat_log_fp = None

    stdout_target = _chat_log_fp or subprocess.DEVNULL
    stderr_target = _chat_log_fp or subprocess.DEVNULL
    creationflags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)

    env = os.environ.copy()
    try:
        proc = subprocess.Popen(
            ["node", "chatbot.js"],
            cwd=str(node_dir),
            env=env,
            stdout=stdout_target,
            stderr=stderr_target,
            creationflags=creationflags,
        )
    except Exception as exc:  # noqa: BLE001
        logger.error("[chat] failed to start chatbot.js: %s", exc)
        return None
    return proc


def _find_node_command() -> str:
    """Find the appropriate node command (node or node3)."""
    import shutil
    if shutil.which("node"):
        return "node"
    if shutil.which("node3"):
        return "node3"
    return "node"  # Default fallback


def _find_chat_script() -> Optional[Path]:
    """Find the chat service script (chatbot.js, server.js, or package.json)."""
    node_dir = BASE_DIR.parent / "node"
    candidates = [
        node_dir / "chatbot.js",
        node_dir / "server.js",
        node_dir / "package.json",
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None


def ensure_chat_service() -> None:
    """Launch chat service on port 3000."""
    global _chat_process, _chat_log_fp
    if not AUTO_START_CHAT:
        logger.info("[launcher] AUTO_START_CHAT=0, skipping auto-launch")
        return
    
    logger.info(f"[launcher] checking port {CHAT_PORT}...")
    target_base = _get_chat_base()
    parsed = urlparse(target_base)
    target_host = parsed.hostname or CHAT_HOST
    target_port = parsed.port or CHAT_PORT
    health_url = f"http://{target_host}:{target_port}/health"
    
    # Check if service is already running
    if _check_service_health(health_url, timeout=0.5):
        logger.info(f"[launcher] chat service already running on {target_host}:{target_port}")
        return
    
    if _is_port_open(target_host, target_port):
        logger.warning(f"[launcher] port {target_host}:{target_port} is open but health check failed")
        return

    chat_script = _find_chat_script()
    if not chat_script:
        logger.error("[launcher][ERROR] chat service script not found, cannot auto-start")
        return

    logger.info("[launcher] starting chat service...")
    node_cmd = _find_node_command()
    node_dir = BASE_DIR.parent / "node"
    
    log_dir = BASE_DIR / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / "chatbot-node.log"
    try:
        _chat_log_fp = open(log_path, "ab")
    except OSError as exc:
        logger.warning("[launcher] failed to open log file %s: %s", log_path, exc)
        _chat_log_fp = None

    stdout_target = _chat_log_fp or subprocess.DEVNULL
    stderr_target = _chat_log_fp or subprocess.DEVNULL
    creationflags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)

    env = os.environ.copy()
    try:
        if chat_script.name == "package.json":
            # Use npm start if package.json exists
            proc = subprocess.Popen(
                ["npm", "start"],
                cwd=str(node_dir),
                env=env,
                stdout=stdout_target,
                stderr=stderr_target,
                creationflags=creationflags,
            )
        else:
            # Use node directly for .js files
            proc = subprocess.Popen(
                [node_cmd, chat_script.name],
                cwd=str(node_dir),
                env=env,
                stdout=stdout_target,
                stderr=stderr_target,
                creationflags=creationflags,
            )
        _chat_process = proc
        logger.info(f"[launcher] chat service started pid={proc.pid}")
    except Exception as exc:
        logger.error(f"[launcher][ERROR] failed to start chat service: {exc}")
        return

    def _cleanup_chat_process() -> None:
        global _chat_process, _chat_log_fp
        if _chat_process and _chat_process.poll() is None:
            try:
                _chat_process.terminate()
                _chat_process.wait(2)
            except subprocess.TimeoutExpired:
                _chat_process.kill()
            except Exception:
                pass
        _chat_process = None
        if _chat_log_fp:
            try:
                _chat_log_fp.close()
            except Exception:
                pass
            _chat_log_fp = None

    atexit.register(_cleanup_chat_process)

    # Wait for health check (up to 10 seconds)
    deadline = time.time() + 10
    while time.time() < deadline:
        if _check_service_health(health_url, timeout=0.5):
            logger.info(f"[launcher] chat service ready on {target_host}:{target_port}")
            return
        # Fallback: check if port is open (if health endpoint doesn't exist)
        if _is_port_open(target_host, target_port):
            logger.info(f"[launcher] chat service ready on {target_host}:{target_port} (port check)")
            return
        time.sleep(0.5)
    
    # Enhanced error diagnostics
    logger.error(f"[launcher][ERROR] chat service failed to start within 10s")
    logger.error(f"[launcher][ERROR] Please check the log file for details: {log_path}")
    logger.error(f"[launcher][ERROR] Diagnostic info:")
    logger.error(f"[launcher][ERROR]   - node_dir: {node_dir}")
    logger.error(f"[launcher][ERROR]   - chat_script: {chat_script}")
    logger.error(f"[launcher][ERROR]   - node_cmd: {node_cmd}")
    logger.error(f"[launcher][ERROR]   - health_url: {health_url}")
    if _chat_process:
        exit_code = _chat_process.poll()
        if exit_code is not None:
            logger.error(f"[launcher][ERROR]   - process exited with code: {exit_code}")
        else:
            logger.error(f"[launcher][ERROR]   - process is still running (pid: {_chat_process.pid})")


def _is_chat_service_reachable(target_base: str, timeout: float = 1.5) -> bool:
    probe_targets = [
        ("GET", f"{target_base}/health"),
        ("GET", f"{target_base}/"),
        ("HEAD", f"{target_base}/api/chat"),
    ]
    for idx, (method, url) in enumerate(probe_targets):
        try:
            resp = requests.request(method, url, timeout=timeout)
        except requests.RequestException:
            continue
        if idx == 0 and resp.status_code in {404, 405}:
            continue
        return True
    return False


def _check_service_health(url: str, timeout: float = 1.0) -> bool:
    """Check if a service health endpoint is responding."""
    try:
        resp = requests.get(url, timeout=timeout)
        if resp.status_code == 200:
            try:
                data = resp.json()
                return data.get("ok") is True or data.get("status") == "ok"
            except (ValueError, AttributeError):
                return True  # If JSON parse fails but status is 200, consider it healthy
        return False
    except requests.RequestException:
        return False


def ensure_screen_backend():
    """Launch screening backend service on port 5100."""
    global _screen_process
    if not START_SCREEN:
        logger.info("[launcher] START_SCREEN=0, skipping auto-launch")
        return
    
    logger.info(f"[launcher] checking port {SCREEN_PORT}...")
    # Try /health first, fallback to /api/health
    health_url = f"http://{SCREEN_HOST}:{SCREEN_PORT}/health"
    
    # Check if service is already running
    if _check_service_health(health_url, timeout=0.5):
        logger.info(f"[launcher] screening backend already running on {SCREEN_HOST}:{SCREEN_PORT}")
        return
    
    if _is_port_open(SCREEN_HOST, SCREEN_PORT):
        logger.warning(f"[launcher] port {SCREEN_HOST}:{SCREEN_PORT} is open but health check failed")
        return
    
    script_path = BASE_DIR / "screen" / "feiaiagent" / "app.py"
    if not script_path.exists():
        logger.error(f"[launcher][ERROR] screen app not found at {script_path}, cannot auto-start")
        return

    logger.info("[launcher] starting screening backend...")
    env = os.environ.copy()
    env.setdefault("SCREEN_PORT", str(SCREEN_PORT))
    env.setdefault("SCREEN_HOST", SCREEN_HOST)

    try:
        proc = subprocess.Popen(
            [sys.executable, str(script_path)],
            cwd=str(script_path.parent),
            env=env,
            stdout=None,
            stderr=None,
        )
        _screen_process = proc
        logger.info(f"[launcher] screening backend started pid={proc.pid}")
    except Exception as exc:
        logger.error(f"[launcher][ERROR] failed to start screening backend: {exc}")
        return

    def _cleanup():
        global _screen_process
        if _screen_process and _screen_process.poll() is None:
            try:
                _screen_process.terminate()
                _screen_process.wait(5)
            except subprocess.TimeoutExpired:
                _screen_process.kill()
            except Exception:
                pass
        _screen_process = None

    atexit.register(_cleanup)

    # Wait for health check (up to 10 seconds)
    deadline = time.time() + 10
    while time.time() < deadline:
        if _check_service_health(health_url, timeout=0.5):
            logger.info(f"[launcher] screening backend ready on {SCREEN_HOST}:{SCREEN_PORT}")
            return
        time.sleep(0.5)
    
    logger.error(f"[launcher][ERROR] screening backend failed to start within 10s")


# Expose the app instance for WSGI servers (e.g., gunicorn main:app).
app = system_init()
_chat_base = _get_chat_base()
logger.info(f"[launcher] expecting chat service at {_chat_base}")

logger.info("\n=== ROUTES (runtime) ===")
for r in app.url_map.iter_rules():
    logger.info(f"{r.rule} {r.methods}")
logger.info("=== END ===\n")


if __name__ == "__main__":
    logger.info("[launcher] starting all services...")
    # Start services in order: 1) Screening backend, 2) Chat service, 3) Main backend
    ensure_screen_backend()
    ensure_chat_service()
    port = int(os.environ.get("PORT", 8001))
    logger.info(f"[launcher] starting main backend on {port}...")
    app.run(host="0.0.0.0", port=port, debug=False)
