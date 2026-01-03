from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional, Tuple

try:
    from zoneinfo import ZoneInfo
except Exception:  # pragma: no cover - fallback for older runtimes
    ZoneInfo = None

from firebase_admin import firestore as admin_firestore

TIMEZONE_NAME = "Asia/Kuala_Lumpur"
SLOT_START_MINUTES = 9 * 60 + 30
SLOT_END_MINUTES = 16 * 60  # last slot starts at 16:00 and ends at 16:30

APPOINTMENTS_COLLECTION = "appointments"
DOCTOR_SLOTS_COLLECTION = "doctorSlots"
PATIENT_ACTIVE_COLLECTION = "patientActiveAppointments"
HOLIDAYS_COLLECTION = "holidays"
PRIVILEGED_ROLES = {"doctor", "admin"}


class AppointmentError(Exception):
    def __init__(self, code: str, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.code = code
        self.status = status


def _tx_get_one(transaction: Any, ref: Any):
    snap = transaction.get(ref)
    if isinstance(snap, (list, tuple)):
        return snap[0] if snap else None
    if hasattr(snap, "__iter__") and not hasattr(snap, "exists"):
        try:
            return next(iter(snap), None)
        except Exception:
            return None
    return snap


def resolve_patient_id(requested_patient_id: Any, auth_uid: str, role: str) -> str:
    auth_uid = str(auth_uid or "").strip()
    requested = str(requested_patient_id or "").strip()
    role_lower = (role or "patient").lower()

    if not auth_uid:
        raise AppointmentError("UNAUTHORIZED", "Missing auth uid", status=401)
    if requested and requested != auth_uid and role_lower not in PRIVILEGED_ROLES:
        raise AppointmentError("FORBIDDEN", "patientId mismatch", status=403)
    if role_lower in PRIVILEGED_ROLES and requested:
        return requested
    return auth_uid


def _get_default_tz() -> timezone:
    if ZoneInfo is not None:
        try:
            return ZoneInfo(TIMEZONE_NAME)
        except Exception:
            pass
    return datetime.now().astimezone().tzinfo or timezone.utc


DEFAULT_TZ = _get_default_tz()


def _ensure_tz(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=DEFAULT_TZ)
    return dt.astimezone(DEFAULT_TZ)


def parse_scheduled_at(value: Any) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return _ensure_tz(value)
    if isinstance(value, (int, float)):
        ts_value = float(value)
        if ts_value > 1_000_000_000_000:
            ts_seconds = ts_value / 1000.0
        else:
            ts_seconds = ts_value
        return datetime.fromtimestamp(ts_seconds, tz=timezone.utc).astimezone(DEFAULT_TZ)
    if isinstance(value, str):
        raw = value.strip()
        if not raw:
            return None
        if raw.isdigit():
            return parse_scheduled_at(int(raw))
        if raw.endswith("Z"):
            raw = raw[:-1] + "+00:00"
        try:
            parsed = datetime.fromisoformat(raw)
        except ValueError:
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=DEFAULT_TZ)
        else:
            parsed = parsed.astimezone(DEFAULT_TZ)
        return parsed
    return None


def to_date_key(dt: datetime) -> str:
    return dt.strftime("%Y%m%d")


def to_slot_key(dt: datetime) -> str:
    return dt.strftime("%H%M")


def _coerce_datetime(value: Any) -> Optional[datetime]:
    if isinstance(value, datetime):
        return _ensure_tz(value)
    return parse_scheduled_at(value)


def is_holiday(date_key: str, db: Any = None, transaction: Any = None) -> bool:
    if not date_key or not db:
        return False
    try:
        ref = db.collection(HOLIDAYS_COLLECTION).document(date_key)
        snap = _tx_get_one(transaction, ref) if transaction else ref.get()
    except Exception:
        return False
    if not getattr(snap, "exists", False):
        return False
    data = snap.to_dict() or {}
    status = str(data.get("status") or "").lower()
    return bool(data.get("isHoliday")) or status in {"holiday", "closed"}


def validate_slot(
    dt: Optional[datetime],
    db: Any = None,
    transaction: Any = None,
) -> Tuple[bool, Optional[str]]:
    if dt is None:
        return False, "INVALID_TIME_SLOT"
    local_dt = _ensure_tz(dt)
    date_key = to_date_key(local_dt)
    if is_holiday(date_key, db=db, transaction=transaction):
        return False, "NON_WORKING_DAY"
    if local_dt.weekday() >= 5:
        return False, "NON_WORKING_DAY"
    total_minutes = local_dt.hour * 60 + local_dt.minute
    if total_minutes < SLOT_START_MINUTES or total_minutes > SLOT_END_MINUTES:
        return False, "INVALID_TIME_RANGE"
    if local_dt.minute not in (0, 30) or local_dt.second != 0 or local_dt.microsecond != 0:
        return False, "INVALID_TIME_SLOT"
    return True, None


def serialize_datetime(value: Any) -> Optional[str]:
    dt = _coerce_datetime(value)
    if not dt:
        return None
    return _ensure_tz(dt).isoformat()


def _get_appointment_by_id(db: Any, appointment_id: str):
    appointment_id = str(appointment_id or "").strip()
    if not appointment_id or not db:
        return None, None
    ref = db.collection(APPOINTMENTS_COLLECTION).document(appointment_id)
    snap = ref.get()
    if getattr(snap, "exists", False):
        data = snap.to_dict() or {}
        data["id"] = snap.id
        return data, ref
    return None, None


def create_appointment_in_transaction(
    transaction: Any,
    db: Any,
    payload: Dict[str, Any],
) -> Dict[str, Any]:
    patient_id = str(payload.get("patientId") or "").strip()
    doctor_id = str(payload.get("doctorId") or "").strip()
    scheduled_at = parse_scheduled_at(payload.get("scheduledAt"))

    if scheduled_at is None:
        raise AppointmentError("INVALID_SCHEDULED_AT", "Invalid scheduledAt")

    if not patient_id or not doctor_id:
        raise AppointmentError("INVALID_REQUEST", "patientId and doctorId are required")

    ok, error_code = validate_slot(scheduled_at, db=db, transaction=transaction)
    if not ok:
        raise AppointmentError(error_code or "INVALID_TIME_SLOT", "Invalid appointment time")

    slot_start_at = _ensure_tz(scheduled_at).replace(second=0, microsecond=0)
    date_key = to_date_key(slot_start_at)
    slot_key = to_slot_key(slot_start_at)

    appointment_ref = db.collection(APPOINTMENTS_COLLECTION).document()
    appointment_id = appointment_ref.id

    patient_lock_ref = db.collection(PATIENT_ACTIVE_COLLECTION).document(patient_id)
    patient_lock_snap = _tx_get_one(transaction, patient_lock_ref)
    if getattr(patient_lock_snap, "exists", False):
        lock_data = patient_lock_snap.to_dict() or {}
        lock_status = str(lock_data.get("status") or "").lower().strip()
        lock_appt_id = str(lock_data.get("appointmentId") or "").strip()
        stale_lock = False

        if lock_appt_id:
            lock_appt_ref = db.collection(APPOINTMENTS_COLLECTION).document(lock_appt_id)
            lock_appt_snap = _tx_get_one(transaction, lock_appt_ref)
            if not getattr(lock_appt_snap, "exists", False):
                stale_lock = True
            else:
                appt_data = lock_appt_snap.to_dict() or {}
                appt_status = str(appt_data.get("status") or "").lower().strip()
                if appt_status in {"cancelled", "completed", "expired"}:
                    stale_lock = True
        else:
            stale_lock = True

        if stale_lock:
            print(
                f"[create.tx] stale lock removed patient={patient_id} "
                f"lockAppt={lock_appt_id} lockStatus={lock_status}"
            )
            transaction.delete(patient_lock_ref)
        else:
            raise AppointmentError(
                "PATIENT_HAS_ACTIVE_APPOINTMENT",
                "Patient already has an active appointment",
                status=409,
            )

    slot_doc_id = f"{doctor_id}_{date_key}_{slot_key}"
    slot_ref = db.collection(DOCTOR_SLOTS_COLLECTION).document(slot_doc_id)
    slot_snap = _tx_get_one(transaction, slot_ref)
    if getattr(slot_snap, "exists", False):
        slot_data = slot_snap.to_dict() or {}
        slot_status = str(slot_data.get("status") or "").lower()
        if slot_status in {"held", "booked"} or slot_status:
            raise AppointmentError("SLOT_TAKEN", "Time slot is already taken", status=409)

    slot_end_at = slot_start_at + timedelta(minutes=30)
    appointment_payload = {
        "appointmentId": appointment_id,
        "patientId": patient_id,
        "doctorId": doctor_id,
        "scheduledAt": slot_start_at,
        "slotStartAt": slot_start_at,
        "endAt": slot_end_at,
        "dateKey": date_key,
        "slotKey": slot_key,
        "status": "scheduled",
        "createdAt": admin_firestore.SERVER_TIMESTAMP,
        "updatedAt": admin_firestore.SERVER_TIMESTAMP,
    }
    doctor_name = str(payload.get("doctorName") or "").strip()
    hospital_name = str(payload.get("hospitalName") or "").strip()
    if doctor_name:
        appointment_payload["doctorName"] = doctor_name
    if hospital_name:
        appointment_payload["hospitalName"] = hospital_name
    if payload.get("expiredAt") is not None:
        appointment_payload["expiredAt"] = payload.get("expiredAt")

    transaction.set(appointment_ref, appointment_payload)

    slot_payload = {
        "doctorId": doctor_id,
        "patientId": patient_id,
        "appointmentId": appointment_id,
        "dateKey": date_key,
        "slotKey": slot_key,
        "status": "held",
        "createdAt": admin_firestore.SERVER_TIMESTAMP,
    }
    try:
        transaction.create(slot_ref, slot_payload)
    except Exception as exc:  # noqa: BLE001
        raise AppointmentError("SLOT_TAKEN", "Time slot is already taken", status=409) from exc

    patient_lock_payload = {
        "patientId": patient_id,
        "appointmentId": appointment_id,
        "doctorId": doctor_id,
        "scheduledAt": slot_start_at,
        "dateKey": date_key,
        "slotKey": slot_key,
        "status": "scheduled",
        "createdAt": admin_firestore.SERVER_TIMESTAMP,
    }
    try:
        transaction.create(patient_lock_ref, patient_lock_payload)
    except Exception as exc:  # noqa: BLE001
        raise AppointmentError(
            "PATIENT_HAS_ACTIVE_APPOINTMENT",
            "Patient already has an active appointment",
            status=409,
        ) from exc

    return {
        "appointmentId": appointment_id,
        "dateKey": date_key,
        "slotKey": slot_key,
        "slotStartAt": slot_start_at,
    }


def cancel_appointment_in_transaction(
    transaction: Any,
    db: Any,
    appointment_id: str,
    patient_id: str,
    reason: str,
    now: Optional[datetime] = None,
    allow_privileged: bool = False,
) -> Dict[str, Any]:
    appointment_id = str(appointment_id or "").strip()
    patient_id = str(patient_id or "").strip()
    reason = str(reason or "").strip()

    if not appointment_id or (not patient_id and not allow_privileged):
        raise AppointmentError("INVALID_REQUEST", "appointmentId is required")
    if not reason:
        raise AppointmentError("REASON_REQUIRED", "Cancellation reason is required")

    appointment_ref = db.collection(APPOINTMENTS_COLLECTION).document(appointment_id)
    appointment_snap = _tx_get_one(transaction, appointment_ref)
    print("[cancel.tx] snap type =", type(appointment_snap))
    print("[cancel.tx] appointment_id raw =", repr(appointment_id))
    print("[cancel.tx] ref path =", appointment_ref.path)
    print("[cancel.tx] exists =", getattr(appointment_snap, "exists", None))
    if getattr(appointment_snap, "exists", False):
        _d = appointment_snap.to_dict() or {}
        print("[cancel.tx] doc.patientId =", _d.get("patientId"), "status =", _d.get("status"))
    if not getattr(appointment_snap, "exists", False):
        raise AppointmentError("NOT_FOUND", "Appointment not found", status=404)

    appointment_data = appointment_snap.to_dict() or {}
    owner_id = str(appointment_data.get("patientId") or "").strip()
    if not allow_privileged and owner_id != patient_id:
        raise AppointmentError("FORBIDDEN", "Appointment does not belong to patient", status=403)

    status = str(appointment_data.get("status") or "scheduled").lower()
    if status not in {"scheduled", "pending", "upcoming", "active"}:
        raise AppointmentError("INVALID_STATUS", "Appointment cannot be cancelled", status=409)

    slot_dt = _coerce_datetime(appointment_data.get("slotStartAt") or appointment_data.get("scheduledAt"))
    if not slot_dt:
        raise AppointmentError("INVALID_TIME_SLOT", "Appointment slot time missing", status=400)

    slot_dt = _ensure_tz(slot_dt)
    now_dt = _ensure_tz(now) if isinstance(now, datetime) else datetime.now(DEFAULT_TZ)

    if now_dt >= slot_dt - timedelta(hours=24):
        raise AppointmentError(
            "CANCEL_WINDOW_CLOSED",
            "Cancellation must be at least 24 hours before the appointment",
            status=409,
        )

    effective_patient_id = owner_id or patient_id

    transaction.update(
        appointment_ref,
        {
            "status": "cancelled",
            "cancelledAt": admin_firestore.SERVER_TIMESTAMP,
            "cancelReason": reason,
            "updatedAt": admin_firestore.SERVER_TIMESTAMP,
        },
    )

    doctor_id = str(appointment_data.get("doctorId") or "").strip()
    date_key = str(appointment_data.get("dateKey") or "").strip() or to_date_key(slot_dt)
    slot_key = str(appointment_data.get("slotKey") or "").strip() or to_slot_key(slot_dt)

    if doctor_id and date_key and slot_key:
        slot_doc_id = f"{doctor_id}_{date_key}_{slot_key}"
        slot_ref = db.collection(DOCTOR_SLOTS_COLLECTION).document(slot_doc_id)
        slot_snap = _tx_get_one(transaction, slot_ref)
        if getattr(slot_snap, "exists", False):
            slot_data = slot_snap.to_dict() or {}
            if slot_data.get("appointmentId") == appointment_id:
                transaction.delete(slot_ref)

    if effective_patient_id:
        lock_ref = db.collection(PATIENT_ACTIVE_COLLECTION).document(effective_patient_id)
        lock_snap = _tx_get_one(transaction, lock_ref)
        if getattr(lock_snap, "exists", False):
            lock_data = lock_snap.to_dict() or {}
            lock_appt_id = str(lock_data.get("appointmentId") or "").strip()
            lock_doctor_id = str(lock_data.get("doctorId") or "").strip()
            lock_date_key = str(lock_data.get("dateKey") or "").strip()
            lock_slot_key = str(lock_data.get("slotKey") or "").strip()
            lock_status = str(lock_data.get("status") or "").lower().strip()

            same_appt = lock_appt_id == appointment_id
            same_slot = (
                doctor_id
                and date_key
                and slot_key
                and lock_doctor_id == doctor_id
                and lock_date_key == date_key
                and lock_slot_key == slot_key
            )
            stale_like = lock_status in {"scheduled", "active", "pending", "upcoming"}

            if same_appt or same_slot or stale_like:
                transaction.delete(lock_ref)

    print(f"[cancel.tx] lock cleanup done patient={effective_patient_id} appt={appointment_id}")

    return {"appointmentId": appointment_id}


def run_create_appointment(db: Any, payload: Dict[str, Any]) -> Dict[str, Any]:
    transaction = db.transaction()

    @admin_firestore.transactional
    def _run(transaction: Any) -> Dict[str, Any]:
        return create_appointment_in_transaction(transaction, db, payload)

    return _run(transaction)


def run_cancel_appointment(
    db: Any,
    appointment_id: str,
    patient_id: str,
    reason: str,
    now: Optional[datetime] = None,
    allow_privileged: bool = False,
) -> Dict[str, Any]:
    transaction = db.transaction()

    @admin_firestore.transactional
    def _run(transaction: Any) -> Dict[str, Any]:
        return cancel_appointment_in_transaction(
            transaction,
            db,
            appointment_id=appointment_id,
            patient_id=patient_id,
            reason=reason,
            now=now,
            allow_privileged=allow_privileged,
        )

    return _run(transaction)
