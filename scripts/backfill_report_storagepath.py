from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Optional


BASE_DIR = Path(__file__).resolve().parents[1]
BACKEND_DIR = BASE_DIR / "backend"
sys.path.insert(0, str(BACKEND_DIR))

from firebase_admin import firestore as admin_firestore  # noqa: E402
from firebase_admin_init import get_firestore_client, get_storage_bucket  # noqa: E402


logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("backfill_reports")

REPORT_DIRS = [
    BASE_DIR / "report",
    BASE_DIR / "backend" / "screen" / "feiaiagent" / "report",
]


def resolve_local_pdf(data: dict) -> Optional[Path]:
    candidates = []
    local_path = str(data.get("localPath") or "").strip()
    if local_path:
        candidates.append(Path(local_path))

    pdf_path = str(data.get("pdfPath") or "").strip()
    if pdf_path and not pdf_path.startswith("reports/"):
        candidates.append(Path(pdf_path))

    file_name = str(data.get("fileName") or "").strip()
    if file_name:
        for base in REPORT_DIRS:
            candidates.append(base / file_name)

    for candidate in candidates:
        candidate = candidate if candidate.is_absolute() else BASE_DIR / candidate
        if candidate.exists():
            return candidate
    return None


def is_missing_pdf_status(value: str) -> bool:
    return value in ("", "null", "undefined")


def backfill_collection(collection_name: str) -> None:
    db = get_firestore_client()
    bucket = get_storage_bucket()
    docs = list(db.collection(collection_name).stream())
    logger.info("collection=%s docs=%s", collection_name, len(docs))

    for doc_snap in docs:
        data = doc_snap.to_dict() or {}
        patient_id = str(data.get("patientId") or data.get("userId") or "").strip()
        report_id = doc_snap.id
        storage_path = str(data.get("storagePath") or data.get("pdfPath") or "").strip()
        pdf_status = str(data.get("pdfStatus") or "").strip().lower()

        if storage_path and storage_path.startswith("reports/"):
            if is_missing_pdf_status(pdf_status):
                logger.info("update pdfStatus=ready reportId=%s storagePath=%s", report_id, storage_path)
                doc_snap.reference.set(
                    {
                        "storagePath": storage_path,
                        "pdfPath": storage_path,
                        "pdfStatus": "ready",
                        "pdfUpdatedAt": admin_firestore.SERVER_TIMESTAMP,
                        "updatedAt": admin_firestore.SERVER_TIMESTAMP,
                    },
                    merge=True,
                )
            continue

        if not patient_id:
            logger.info("skip reportId=%s (missing patientId)", report_id)
            doc_snap.reference.set(
                {
                    "pdfStatus": "missing",
                    "updatedAt": admin_firestore.SERVER_TIMESTAMP,
                },
                merge=True,
            )
            continue

        local_pdf = resolve_local_pdf(data)
        if not local_pdf:
            logger.info("missing local pdf reportId=%s", report_id)
            doc_snap.reference.set(
                {
                    "pdfStatus": "missing",
                    "updatedAt": admin_firestore.SERVER_TIMESTAMP,
                },
                merge=True,
            )
            continue

        target_path = f"reports/{patient_id}/{report_id}.pdf"
        try:
            blob = bucket.blob(target_path)
            blob.upload_from_filename(str(local_pdf), content_type="application/pdf")
            logger.info("uploaded reportId=%s storagePath=%s", report_id, target_path)
            doc_snap.reference.set(
                {
                    "storagePath": target_path,
                    "pdfPath": target_path,
                    "pdfStatus": "ready",
                    "pdfUpdatedAt": admin_firestore.SERVER_TIMESTAMP,
                    "updatedAt": admin_firestore.SERVER_TIMESTAMP,
                },
                merge=True,
            )
        except Exception as exc:
            logger.error("upload failed reportId=%s error=%s", report_id, exc)
            doc_snap.reference.set(
                {
                    "pdfStatus": "error",
                    "updatedAt": admin_firestore.SERVER_TIMESTAMP,
                },
                merge=True,
            )


def main() -> None:
    for collection_name in ("reports", "risk_reports"):
        try:
            backfill_collection(collection_name)
        except Exception as exc:
            logger.warning("collection=%s failed error=%s", collection_name, exc)


if __name__ == "__main__":
    main()
