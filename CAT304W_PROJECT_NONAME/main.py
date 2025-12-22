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
import logging
import base64
import json
from datetime import timedelta
from functools import wraps
from pathlib import Path
from typing import Any, Dict, Optional, Callable
from urllib.parse import urlparse

from flask import Flask, abort, jsonify, request, send_from_directory, Response, g
from flask_cors import CORS
import requests
from firebase_admin import firestore as admin_firestore

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
UI_DIR = BASE_DIR / "ui"
SCREEN_BACKEND_URL = os.environ.get("SCREEN_BACKEND_URL", "http://127.0.0.1:5100").rstrip("/")
_parsed_screen_url = urlparse(SCREEN_BACKEND_URL if "://" in SCREEN_BACKEND_URL else f"http://{SCREEN_BACKEND_URL}")
SCREEN_HOST = os.environ.get("SCREEN_HOST", _parsed_screen_url.hostname or "127.0.0.1")
SCREEN_PORT = int(os.environ.get("SCREEN_PORT", _parsed_screen_url.port or 5100))
START_SCREEN = os.environ.get("START_SCREEN", "1").lower() not in ("0", "false", "no")
CHAT_HOST = os.environ.get("CHAT_HOST", "127.0.0.1")
CHAT_PORT = int(os.environ.get("NODE_CHAT_PORT", 3000))
DEFAULT_NODE_CHAT_URL = f"http://{CHAT_HOST}:{CHAT_PORT}"
_screen_process: Optional[subprocess.Popen] = None
_chat_process: Optional[subprocess.Popen] = None
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



def _extract_bearer_token() -> Optional[str]:
    header = request.headers.get("Authorization", "")
    if not header:
        return None
    parts = header.split()
    if len(parts) != 2 or parts[0].lower() != "bearer":
        return None
    return parts[1].strip() or None


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
        doctor_id = meta.get("doctorId") or "unassigned"
        risk_level = (meta.get("riskLevel") or "low").strip() or "low"
        content_text = meta.get("reportText") or build_content_text(answers_list)

        summary_user_info = {
            "name": normalized.get("fullName") or "未知",
            "gender": normalize_gender(normalized.get("gender")) if normalized.get("gender") else "未知",
            "yearOfBirth": normalized.get("yearOfBirth") or "未知",
        }

        try:
            bucket = get_storage_bucket()
            path = f"reports/{report_id}.pdf"
            blob = bucket.blob(path)
            blob.upload_from_string(pdf_bytes, content_type="application/pdf")
            signed_url = None
            try:
                signed_url = blob.generate_signed_url(expiration=timedelta(minutes=15))
            except Exception as exc:  # noqa: BLE001
                logger.info("[reports] signed URL not generated for %s: %s", report_id, exc)
        except Exception as exc:  # noqa: BLE001
            logger.error("[reports] storage upload failed uid=%s reportId=%s bytes=%s error=%s", uid, report_id, len(pdf_bytes), exc)
            return jsonify({"error": "Storage upload failed", "detail": str(exc)}), 500

        try:
            db = get_firestore_client()
            doc_ref = db.collection("reports").document(report_id)
            doc_ref.set(
                {
                    "createdAt": admin_firestore.SERVER_TIMESTAMP,
                    "updatedAt": admin_firestore.SERVER_TIMESTAMP,
                    "doctorId": doctor_id or "unassigned",
                    "patientId": uid,
                    "reportId": report_id,
                    "screeningId": screening_id,
                    "riskLevel": risk_level,
                    "source": mode,
                    "status": "ready",
                    "answersRaw": answers_raw,
                    "answersNormalized": answers_normalized,
                    "summaryUserInfo": summary_user_info,
                    "contentText": content_text,
                    "storagePath": path,
                    "downloadUrl": signed_url,
                },
                merge=True,
            )
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
                "storagePath": path,
                "downloadUrl": signed_url,
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

    @app.route("/api/chat", methods=["POST"])
    @require_firebase_auth()
    def chat() -> Any:
        target_base = (os.environ.get("NODE_CHAT_URL") or DEFAULT_NODE_CHAT_URL).rstrip("/")
        parsed_target = urlparse(target_base if "://" in target_base else f"http://{target_base}")
        target_host = parsed_target.hostname or CHAT_HOST
        target_port = parsed_target.port or CHAT_PORT

        if target_host in {"127.0.0.1", "localhost", CHAT_HOST} and not _is_port_open(target_host, target_port):
            ensure_chat_backend()

        target_url = f"{target_base}/api/chat"

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
            print(f"[screen proxy] -> {target_url} status={proxied.status_code}")
            resp_headers = {"Content-Type": proxied.headers.get("Content-Type", "application/json")}
            return Response(proxied.content, status=proxied.status_code, headers=resp_headers)
        except requests.RequestException:
            print(f"[screen proxy] -> {target_url} status=unreachable")
            return (
                jsonify({
                    "ok": False,
                    "error": "screen backend unreachable",
                    "target": target_url,
                }),
                502,
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


def ensure_screen_backend():
    global _screen_process
    if not START_SCREEN:
        print("[screen launcher] START_SCREEN=0, skipping auto-launch")
        return
    if _screen_process and _screen_process.poll() is None:
        return
    if _is_port_open(SCREEN_HOST, SCREEN_PORT):
        print(f"[screen launcher] screen backend already running on {SCREEN_HOST}:{SCREEN_PORT}")
        return

    script_path = BASE_DIR / "screen" / "feiaiagent" / "app.py"
    if not script_path.exists():
        print(f"[screen launcher] screen app not found at {script_path}, cannot auto-start")
        return

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
        print(f"[screen launcher] screen backend started pid={proc.pid}")
    except Exception as exc:
        print(f"[screen launcher] failed to start screen backend: {exc}")
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

    for _ in range(10):
        if _is_port_open(SCREEN_HOST, SCREEN_PORT):
            break
        time.sleep(0.5)
    else:
        print("[screen launcher] warning: screen backend did not open port in time")


def ensure_chat_backend():
    global _chat_process
    if _chat_process and _chat_process.poll() is None:
        return
    if _is_port_open(CHAT_HOST, CHAT_PORT):
        print(f"[chat launcher] chat backend already running on {CHAT_HOST}:{CHAT_PORT}")
        return

    script_path = BASE_DIR / "chatbot.js"
    if not script_path.exists():
        print(f"[chat launcher] chatbot.js not found at {script_path}, cannot auto-start")
        return

    env = os.environ.copy()
    env.setdefault("NODE_CHAT_PORT", str(CHAT_PORT))
    env.setdefault("CHAT_HOST", CHAT_HOST)

    try:
        proc = subprocess.Popen(
            ["node", "chatbot.js"],
            cwd=str(BASE_DIR),
            env=env,
            stdout=None,
            stderr=None,
        )
        _chat_process = proc
        print(f"[chat launcher] chat backend started pid={proc.pid}")
    except FileNotFoundError:
        print("[chat launcher] failed to start chat backend: 'node' not found. Install Node.js or add it to PATH.")
        return
    except Exception as exc:
        print(f"[chat launcher] failed to start chat backend: {exc}")
        return

    def _cleanup():
        global _chat_process
        if _chat_process and _chat_process.poll() is None:
            try:
                _chat_process.terminate()
                _chat_process.wait(5)
            except subprocess.TimeoutExpired:
                _chat_process.kill()
            except Exception:
                pass
        _chat_process = None

    atexit.register(_cleanup)

    for _ in range(20):
        if _is_port_open(CHAT_HOST, CHAT_PORT):
            break
        time.sleep(0.5)
    else:
        print("[chat launcher] warning: chat backend did not open port in time")

# Expose the app instance for WSGI servers (e.g., gunicorn main:app).
app = system_init()

print("\n=== ROUTES (runtime) ===")
for r in app.url_map.iter_rules():
    if "reports" in r.rule:
        print(r.rule, r.endpoint, r.methods)
print("=== END ===\n")


if __name__ == "__main__":
    ensure_chat_backend()
    ensure_screen_backend()
    port = int(os.environ.get("PORT", 8001))
    app.run(host="0.0.0.0", port=port, debug=False)
