import json
import os
import sys
from pathlib import Path

from google.cloud import storage


BASE_DIR = Path(__file__).resolve().parents[1]
DEFAULT_SERVICE_ACCOUNT = BASE_DIR / "secrets" / "feiai-service-account.json"


def resolve_service_account_path() -> Path:
    candidates = [
        ("GOOGLE_APPLICATION_CREDENTIALS", os.getenv("GOOGLE_APPLICATION_CREDENTIALS", "").strip()),
        ("FIREBASE_SERVICE_ACCOUNT", os.getenv("FIREBASE_SERVICE_ACCOUNT", "").strip()),
    ]
    for name, value in candidates:
        if not value:
            continue
        path = Path(value)
        if path.exists():
            return path
        print(f"[context] {name} set but file not found: {path}")
    if DEFAULT_SERVICE_ACCOUNT.exists():
        return DEFAULT_SERVICE_ACCOUNT
    raise FileNotFoundError(
        f"No service account JSON found. Set GOOGLE_APPLICATION_CREDENTIALS or FIREBASE_SERVICE_ACCOUNT, "
        f"or place file at {DEFAULT_SERVICE_ACCOUNT}."
    )


def main() -> int:
    try:
        path = resolve_service_account_path()
    except Exception as exc:
        print(f"[context] failed to resolve service account: {exc}")
        return 1

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        print(f"[context] failed to read service account json: {exc}")
        return 2

    project_id = str(data.get("project_id") or "").strip()
    bucket_name = os.getenv("FIREBASE_STORAGE_BUCKET", "").strip() or f"{project_id}.appspot.com"

    print(f"[context] service_account={path}")
    print(f"[context] project_id={project_id or '(missing)'}")
    print(f"[context] bucket={bucket_name}")

    try:
        client = storage.Client.from_service_account_json(str(path), project=project_id or None)
        bucket = client.bucket(bucket_name)
        exists = bucket.exists()
        print(f"[context] bucket_exists={exists}")
    except Exception as exc:
        print(f"[context] bucket_exists=error ({type(exc).__name__}: {exc})")
        return 3

    return 0


if __name__ == "__main__":
    sys.exit(main())
