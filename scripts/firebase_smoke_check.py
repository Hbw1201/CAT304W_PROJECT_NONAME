import sys
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parents[1]
BACKEND_DIR = BASE_DIR / "backend"
sys.path.insert(0, str(BACKEND_DIR))

from firebase_admin_init import (  # noqa: E402
    get_project_id,
    get_storage_bucket,
    init_firebase_or_die,
    storage_healthcheck,
)


def main() -> int:
    try:
        init_firebase_or_die()
    except Exception as exc:
        print(f"[smoke-check] init failed: {exc}")
        return 1

    project_id = get_project_id()
    bucket = get_storage_bucket()
    print(f"[smoke-check] project_id={project_id}")
    print(f"[smoke-check] bucket={bucket.name}")

    try:
        storage_healthcheck()
    except Exception as exc:
        print(f"[smoke-check] storage healthcheck failed: {exc}")
        return 2

    print("[smoke-check] ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
