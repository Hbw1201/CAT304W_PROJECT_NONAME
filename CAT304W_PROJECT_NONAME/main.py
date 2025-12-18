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


def _set_identity(decoded: Dict[str, Any], token: str) -> None:
    uid = decoded.get("uid") or decoded.get("sub") or ""
    email = decoded.get("email", "") or ""
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
                logger.warning("[auth] token verification failed: %s", exc)
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
    else:
        data = request.get_json(silent=True, force=True) or {}
        meta["reportId"] = data.get("reportId")
        meta["screeningId"] = data.get("screeningId")
        meta["doctorId"] = data.get("doctorId")
        meta["riskLevel"] = data.get("riskLevel")
        pdf_b64 = data.get("pdfBase64")
        if pdf_b64:
            try:
                payload = pdf_b64.split(",", 1)[-1]
                pdf_bytes = base64.b64decode(payload)
            except Exception as exc:  # noqa: BLE001
                logger.warning("[reports] failed to decode pdfBase64: %s", exc)
                raise

    return pdf_bytes, meta


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

        report_id = meta.get("reportId") or _build_report_id(uid)
        screening_id = meta.get("screeningId") or _build_screening_id()
        doctor_id = meta.get("doctorId") or "unassigned"
        risk_level = (meta.get("riskLevel") or "low").strip() or "low"

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
            doc_ref, _ = db.collection("reports").add(
                {
                    "createdAt": admin_firestore.SERVER_TIMESTAMP,
                    "doctorId": doctor_id or "unassigned",
                    "patientId": uid,
                    "reportId": report_id,
                    "screeningId": screening_id,
                    "riskLevel": risk_level,
                }
            )
            doc_id = doc_ref.id
        except Exception as exc:  # noqa: BLE001
            logger.error("[reports] firestore write failed uid=%s reportId=%s error=%s", uid, report_id, exc)
            return jsonify({"error": "Firestore write failed", "detail": str(exc)}), 500

        logger.info("[reports] created uid=%s reportId=%s docId=%s bytes=%s", uid, report_id, doc_id, len(pdf_bytes))
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
        try:
            if method.upper() == "GET":
                proxied = requests.get(
                    target_url,
                    params=request.args,
                    timeout=20,
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
                    timeout=20,
                )
        except requests.RequestException as exc:
            print(f"[screen proxy] -> {target_url} error={exc}")
            return jsonify({"error": "screen backend unreachable", "detail": str(exc)}), 502
        resp_headers = {"Content-Type": proxied.headers.get("Content-Type", "application/json")}
        return Response(proxied.content, status=proxied.status_code, headers=resp_headers)

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

    @app.route("/patient/login", methods=["GET"])
    @app.route("/patient/login.html", methods=["GET"])
    def serve_patient_login() -> Any:
        # Serve the canonical login page for legacy /patient/login* URLs.
        return send_from_directory(UI_DIR, "login.html")

    @app.route("/script.js", methods=["GET"])
    def serve_root_script() -> Any:
        # Compatibility route for root-level script reference.
        return send_from_directory(UI_DIR, "script.js")

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

print("\n=== ROUTES (runtime) ===")
for r in app.url_map.iter_rules():
    if "reports" in r.rule:
        print(r.rule, r.endpoint, r.methods)
print("=== END ===\n")

# Expose the app instance for WSGI servers (e.g., gunicorn main:app).
app = system_init()


if __name__ == "__main__":
    ensure_chat_backend()
    ensure_screen_backend()
    port = int(os.environ.get("PORT", 8001))
    app.run(host="0.0.0.0", port=port, debug=False)
