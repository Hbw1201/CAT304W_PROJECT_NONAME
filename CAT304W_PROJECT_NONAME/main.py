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
from pathlib import Path
from typing import Any, Dict, Optional
from urllib.parse import urlparse

from flask import Flask, abort, jsonify, request, send_from_directory, Response
from flask_cors import CORS
import requests


BASE_DIR = Path(__file__).resolve().parent
UI_DIR = BASE_DIR / "ui"
SCREEN_BACKEND_URL = os.environ.get("SCREEN_BACKEND_URL", "http://127.0.0.1:5100").rstrip("/")
_parsed_screen_url = urlparse(SCREEN_BACKEND_URL if "://" in SCREEN_BACKEND_URL else f"http://{SCREEN_BACKEND_URL}")
SCREEN_HOST = os.environ.get("SCREEN_HOST", _parsed_screen_url.hostname or "127.0.0.1")
SCREEN_PORT = int(os.environ.get("SCREEN_PORT", _parsed_screen_url.port or 5100))
START_SCREEN = os.environ.get("START_SCREEN", "1").lower() not in ("0", "false", "no")
_screen_process: Optional[subprocess.Popen] = None

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

    @app.route("/api/chat", methods=["POST"])
    def chat() -> Any:
        target_base = os.environ.get("NODE_CHAT_URL", "http://127.0.0.1:3000").rstrip("/")
        target_url = f"{target_base}/api/chat"

        content_type = request.content_type or "application/json"
        raw_body = request.get_data(cache=True, as_text=False) or b""

        try:
            proxied = requests.post(
                target_url,
                data=raw_body,
                headers={
                    "Content-Type": content_type,
                    "Accept": "application/json",
                },
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
        try:
            if method.upper() == "GET":
                proxied = requests.get(
                    target_url,
                    params=request.args,
                    timeout=20,
                    headers={"Accept": "application/json"},
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
                        "Accept": "application/json",
                    },
                    timeout=20,
                )
        except requests.RequestException as exc:
            print(f"[screen proxy] -> {target_url} error={exc}")
            return jsonify({"error": "screen backend unreachable", "detail": str(exc)}), 502
        resp_headers = {"Content-Type": proxied.headers.get("Content-Type", "application/json")}
        return Response(proxied.content, status=proxied.status_code, headers=resp_headers)

    @app.route("/api/screen/voice/start", methods=["POST"])
    def screen_voice_start():
        return proxy_screen("/voice/start", "POST")

    @app.route("/api/screen/voice/stop", methods=["POST"])
    def screen_voice_stop():
        return proxy_screen("/voice/stop", "POST")

    @app.route("/api/screen/voice/status", methods=["GET"])
    def screen_voice_status():
        return proxy_screen("/voice/status", "GET")

    @app.route("/api/screen/metagpt/start", methods=["POST"])
    def screen_metagpt_start():
        return proxy_screen("/metagpt/start", "POST")

    @app.route("/api/screen/metagpt/next", methods=["POST"])
    def screen_metagpt_next():
        return proxy_screen("/metagpt/next", "POST")

    @app.route("/api/screen/health", methods=["GET"])
    def screen_health():
        target_url = f"{SCREEN_BACKEND_URL}/health"
        try:
            proxied = requests.get(
                target_url,
                timeout=5,
                headers={"Accept": "application/json"},
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

    @app.route("/doctor/<path:doctor_path>", methods=["GET"])
    def serve_doctor_assets(doctor_path: str) -> Any:
        # Serve doctor-specific static assets and nested pages.
        return send_from_directory(UI_DIR / "doctor", doctor_path)

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


# Expose the app instance for WSGI servers (e.g., gunicorn main:app).
app = system_init()


if __name__ == "__main__":
    ensure_screen_backend()
    port = int(os.environ.get("PORT", 8001))
    app.run(host="0.0.0.0", port=port, debug=False)
