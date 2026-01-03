from __future__ import annotations

import sys
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, Optional

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

import appointments as appt  # noqa: E402


class FakeDocumentSnapshot:
    def __init__(self, data: Optional[Dict[str, Any]]) -> None:
        self._data = data
        self.exists = data is not None

    def to_dict(self) -> Dict[str, Any]:
        return dict(self._data or {})


class FakeDocumentRef:
    def __init__(self, store: Dict[str, Any], collection: str, doc_id: str) -> None:
        self._store = store
        self._collection = collection
        self.id = doc_id

    @property
    def path(self) -> str:
        return f"{self._collection}/{self.id}"


class FakeCollectionRef:
    def __init__(self, store: Dict[str, Any], name: str) -> None:
        self._store = store
        self._name = name

    def document(self, doc_id: Optional[str] = None) -> FakeDocumentRef:
        doc_id = doc_id or f"auto_{uuid.uuid4().hex[:8]}"
        return FakeDocumentRef(self._store, self._name, doc_id)


class FakeTransaction:
    def __init__(self, store: Dict[str, Any]) -> None:
        self._store = store

    def get(self, doc_ref: FakeDocumentRef) -> FakeDocumentSnapshot:
        return FakeDocumentSnapshot(self._store.get(doc_ref.path))

    def set(self, doc_ref: FakeDocumentRef, data: Dict[str, Any], merge: bool = False) -> None:
        if merge and doc_ref.path in self._store:
            merged = dict(self._store[doc_ref.path])
            merged.update(data)
            self._store[doc_ref.path] = merged
        else:
            self._store[doc_ref.path] = dict(data)

    def create(self, doc_ref: FakeDocumentRef, data: Dict[str, Any]) -> None:
        if doc_ref.path in self._store:
            raise KeyError("Document already exists")
        self._store[doc_ref.path] = dict(data)

    def update(self, doc_ref: FakeDocumentRef, data: Dict[str, Any]) -> None:
        if doc_ref.path not in self._store:
            raise KeyError("Document does not exist")
        merged = dict(self._store[doc_ref.path])
        merged.update(data)
        self._store[doc_ref.path] = merged

    def delete(self, doc_ref: FakeDocumentRef) -> None:
        self._store.pop(doc_ref.path, None)


class FakeDB:
    def __init__(self) -> None:
        self._store: Dict[str, Any] = {}

    def collection(self, name: str) -> FakeCollectionRef:
        return FakeCollectionRef(self._store, name)

    def transaction(self) -> FakeTransaction:
        return FakeTransaction(self._store)


def _assert_error(code: str, func, status: Optional[int] = None) -> None:
    try:
        func()
    except appt.AppointmentError as exc:
        assert exc.code == code
        if status is not None:
            assert exc.status == status
        return
    assert False, f"Expected AppointmentError code={code}"


def test_create_success() -> None:
    db = FakeDB()
    tx = db.transaction()
    scheduled_at = datetime(2025, 1, 2, 14, 0, tzinfo=appt.DEFAULT_TZ)
    payload = {
        "patientId": "patient-1",
        "doctorId": "doctor-1",
        "scheduledAt": scheduled_at,
    }
    result = appt.create_appointment_in_transaction(tx, db, payload)
    assert result["appointmentId"]
    slot_doc = f"{appt.DOCTOR_SLOTS_COLLECTION}/doctor-1_{result['dateKey']}_{result['slotKey']}"
    assert slot_doc in db._store
    lock_doc = f"{appt.PATIENT_ACTIVE_COLLECTION}/patient-1"
    assert lock_doc in db._store
    appt_doc = db._store[f"{appt.APPOINTMENTS_COLLECTION}/{result['appointmentId']}"]
    assert appt_doc["status"] == "scheduled"
    assert appt_doc["scheduledAt"] == scheduled_at
    assert appt_doc["endAt"] == scheduled_at + timedelta(minutes=30)


def test_non_working_day_rejected() -> None:
    db = FakeDB()
    tx = db.transaction()
    scheduled_at = datetime(2025, 1, 4, 14, 0, tzinfo=appt.DEFAULT_TZ)  # Saturday
    payload = {
        "patientId": "patient-1",
        "doctorId": "doctor-1",
        "scheduledAt": scheduled_at,
    }
    _assert_error("NON_WORKING_DAY", lambda: appt.create_appointment_in_transaction(tx, db, payload))


def test_invalid_time_range_1630() -> None:
    db = FakeDB()
    tx = db.transaction()
    scheduled_at = datetime(2025, 1, 2, 16, 30, tzinfo=appt.DEFAULT_TZ)
    payload = {
        "patientId": "patient-1",
        "doctorId": "doctor-1",
        "scheduledAt": scheduled_at,
    }
    _assert_error("INVALID_TIME_RANGE", lambda: appt.create_appointment_in_transaction(tx, db, payload))


def test_invalid_time_step() -> None:
    db = FakeDB()
    tx = db.transaction()
    scheduled_at = datetime(2025, 1, 2, 10, 15, tzinfo=appt.DEFAULT_TZ)
    payload = {
        "patientId": "patient-1",
        "doctorId": "doctor-1",
        "scheduledAt": scheduled_at,
    }
    _assert_error("INVALID_TIME_SLOT", lambda: appt.create_appointment_in_transaction(tx, db, payload))


def test_slot_taken() -> None:
    db = FakeDB()
    slot_doc = f"{appt.DOCTOR_SLOTS_COLLECTION}/doctor-1_20250102_0930"
    db._store[slot_doc] = {"status": "held"}
    tx = db.transaction()
    payload = {
        "patientId": "patient-1",
        "doctorId": "doctor-1",
        "scheduledAt": datetime(2025, 1, 2, 9, 30, tzinfo=appt.DEFAULT_TZ),
    }
    _assert_error("SLOT_TAKEN", lambda: appt.create_appointment_in_transaction(tx, db, payload))


def test_patient_has_active() -> None:
    db = FakeDB()
    lock_doc = f"{appt.PATIENT_ACTIVE_COLLECTION}/patient-1"
    db._store[lock_doc] = {"status": "active"}
    tx = db.transaction()
    payload = {
        "patientId": "patient-1",
        "doctorId": "doctor-1",
        "scheduledAt": datetime(2025, 1, 2, 10, 0, tzinfo=appt.DEFAULT_TZ),
    }
    _assert_error(
        "PATIENT_HAS_ACTIVE_APPOINTMENT",
        lambda: appt.create_appointment_in_transaction(tx, db, payload),
    )


def test_cancel_window_closed() -> None:
    db = FakeDB()
    appointment_id = "appt-1"
    slot_start = datetime(2025, 1, 2, 9, 30, tzinfo=appt.DEFAULT_TZ)
    db._store[f"{appt.APPOINTMENTS_COLLECTION}/{appointment_id}"] = {
        "appointmentId": appointment_id,
        "patientId": "patient-1",
        "doctorId": "doctor-1",
        "status": "scheduled",
        "slotStartAt": slot_start,
        "dateKey": appt.to_date_key(slot_start),
        "slotKey": appt.to_slot_key(slot_start),
    }
    now = slot_start - timedelta(hours=23)
    tx = db.transaction()
    _assert_error(
        "CANCEL_WINDOW_CLOSED",
        lambda: appt.cancel_appointment_in_transaction(
            tx,
            db,
            appointment_id=appointment_id,
            patient_id="patient-1",
            reason="change of plans",
            now=now,
        ),
    )


def test_cancel_reason_required() -> None:
    db = FakeDB()
    appointment_id = "appt-2"
    slot_start = datetime(2025, 1, 2, 9, 30, tzinfo=appt.DEFAULT_TZ)
    db._store[f"{appt.APPOINTMENTS_COLLECTION}/{appointment_id}"] = {
        "appointmentId": appointment_id,
        "patientId": "patient-1",
        "doctorId": "doctor-1",
        "status": "scheduled",
        "slotStartAt": slot_start,
        "dateKey": appt.to_date_key(slot_start),
        "slotKey": appt.to_slot_key(slot_start),
    }
    tx = db.transaction()
    _assert_error(
        "REASON_REQUIRED",
        lambda: appt.cancel_appointment_in_transaction(
            tx,
            db,
            appointment_id=appointment_id,
            patient_id="patient-1",
            reason="",
            now=slot_start - timedelta(days=2),
        ),
    )


def test_cancel_success_releases_locks() -> None:
    db = FakeDB()
    appointment_id = "appt-3"
    slot_start = datetime(2025, 1, 2, 14, 0, tzinfo=appt.DEFAULT_TZ)
    date_key = appt.to_date_key(slot_start)
    slot_key = appt.to_slot_key(slot_start)
    db._store[f"{appt.APPOINTMENTS_COLLECTION}/{appointment_id}"] = {
        "appointmentId": appointment_id,
        "patientId": "patient-1",
        "doctorId": "doctor-1",
        "status": "scheduled",
        "slotStartAt": slot_start,
        "dateKey": date_key,
        "slotKey": slot_key,
    }
    db._store[f"{appt.DOCTOR_SLOTS_COLLECTION}/doctor-1_{date_key}_{slot_key}"] = {
        "appointmentId": appointment_id,
        "status": "held",
    }
    db._store[f"{appt.PATIENT_ACTIVE_COLLECTION}/patient-1"] = {
        "appointmentId": appointment_id,
        "status": "scheduled",
    }
    tx = db.transaction()
    appt.cancel_appointment_in_transaction(
        tx,
        db,
        appointment_id=appointment_id,
        patient_id="patient-1",
        reason="reschedule",
        now=slot_start - timedelta(days=2),
    )
    appt_doc = db._store[f"{appt.APPOINTMENTS_COLLECTION}/{appointment_id}"]
    assert appt_doc["status"] == "cancelled"
    assert appt_doc["cancelReason"] == "reschedule"
    assert f"{appt.DOCTOR_SLOTS_COLLECTION}/doctor-1_{date_key}_{slot_key}" not in db._store
    assert f"{appt.PATIENT_ACTIVE_COLLECTION}/patient-1" not in db._store


def test_patient_id_mismatch_forbidden() -> None:
    _assert_error(
        "FORBIDDEN",
        lambda: appt.resolve_patient_id("patient-2", "patient-1", "patient"),
        status=403,
    )


def test_patient_id_binding() -> None:
    assert appt.resolve_patient_id("patient-1", "patient-1", "patient") == "patient-1"
    assert appt.resolve_patient_id("", "patient-1", "patient") == "patient-1"
    assert appt.resolve_patient_id("patient-2", "doctor-1", "doctor") == "patient-2"


if __name__ == "__main__":
    test_create_success()
    test_non_working_day_rejected()
    test_invalid_time_range_1630()
    test_invalid_time_step()
    test_slot_taken()
    test_patient_has_active()
    test_cancel_window_closed()
    test_cancel_reason_required()
    test_cancel_success_releases_locks()
    test_patient_id_mismatch_forbidden()
    test_patient_id_binding()
    print("appointment smoke tests passed")
