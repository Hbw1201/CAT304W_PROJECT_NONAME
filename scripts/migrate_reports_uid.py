import logging
import sys
from pathlib import Path
from typing import Tuple


BASE_DIR = Path(__file__).resolve().parents[1]
BACKEND_DIR = BASE_DIR / "backend"
sys.path.insert(0, str(BACKEND_DIR))

from firebase_admin import firestore as admin_firestore  # noqa: E402
from firebase_admin_init import get_firestore_client, get_storage_bucket  # noqa: E402


logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("migrate_reports_uid")


def _resolve_patient_uid(data: dict, db) -> Tuple[str, str]:
    patient_uid = str(data.get("patientUid") or "").strip()
    if patient_uid:
        return patient_uid, "patientUid"

    user_uid = str(data.get("userUid") or "").strip()
    if user_uid:
        return user_uid, "userUid"

    patient_id = str(data.get("patientId") or data.get("userId") or "").strip()
    if not patient_id:
        return "", ""

    try:
        user_snap = db.collection("users").document(patient_id).get()
        if user_snap.exists:
            uid = str((user_snap.to_dict() or {}).get("uid") or "").strip()
            if uid:
                return uid, "users/{patientId}.uid"
    except Exception as exc:
        logger.warning("users lookup failed patientId=%s error=%s", patient_id, exc)

    return "", ""


def _check_storage_object(bucket, storage_path: str) -> Tuple[bool, int]:
    blob = bucket.blob(storage_path)
    exists = blob.exists()
    try:
        blob.reload()
    except Exception:
        pass
    size = blob.size or 0
    return exists, size


def _migrate_collection(collection_name: str) -> None:
    db = get_firestore_client()
    bucket = get_storage_bucket()
    docs = list(db.collection(collection_name).stream())
    logger.info("collection=%s docs=%s", collection_name, len(docs))

    updated = 0
    skipped = 0
    missing_uid = 0
    for doc_snap in docs:
        data = doc_snap.to_dict() or {}
        report_id = doc_snap.id

        patient_uid, source = _resolve_patient_uid(data, db)
        if not patient_uid:
            missing_uid += 1
            logger.info("skip reportId=%s (missing patientUid)", report_id)
            continue

        storage_path = str(data.get("storagePath") or data.get("pdfPath") or "").strip()
        expected_prefix = f"reports/{patient_uid}/"
        expected_path = f"{expected_prefix}{report_id}.pdf"

        needs_storage_update = not storage_path or not storage_path.startswith(expected_prefix)
        needs_uid_update = not str(data.get("patientUid") or "").strip()

        if not needs_storage_update and not needs_uid_update:
            skipped += 1
            continue

        update_payload = {
            "patientUid": patient_uid,
            "userUid": patient_uid,
            "updatedAt": admin_firestore.SERVER_TIMESTAMP,
        }

        if needs_storage_update:
            exists, size = _check_storage_object(bucket, expected_path)
            pdf_status = "ready" if exists and size > 0 else "missing"
            update_payload.update(
                {
                    "storagePath": expected_path,
                    "pdfPath": expected_path,
                    "pdfStatus": pdf_status,
                    "pdfUpdatedAt": admin_firestore.SERVER_TIMESTAMP,
                }
            )
            logger.info(
                "update reportId=%s source=%s storagePath=%s exists=%s size=%s status=%s",
                report_id,
                source,
                expected_path,
                exists,
                size,
                pdf_status,
            )
        else:
            logger.info("update reportId=%s source=%s patientUid=%s", report_id, source, patient_uid)

        doc_snap.reference.set(update_payload, merge=True)
        updated += 1

    logger.info(
        "done collection=%s updated=%s skipped=%s missing_uid=%s",
        collection_name,
        updated,
        skipped,
        missing_uid,
    )


def main() -> None:
    for collection_name in ("reports", "risk_reports"):
        try:
            _migrate_collection(collection_name)
        except Exception as exc:
            logger.warning("collection=%s failed error=%s", collection_name, exc)


if __name__ == "__main__":
    main()
