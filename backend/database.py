# backend/database.py
import json
import sqlite3
from datetime import datetime
from pathlib import Path

DB_PATH = Path(__file__).parent / "nexov.db"

CLINIC_INFO = {
    "name": "Nexov Healthcare",
    "hours": "Monday to Saturday, 9:00 AM to 6:30 PM. Closed on Sundays.",
    "address": "Suite 204, Nexov Medical Plaza, Jubilee Hills",
    "emergency_note": "For life-threatening emergencies, call 108 or 911 immediately.",
}

DEFAULT_DOCTORS = [
    ("doc_1", "Dr. Priya Sharma", "Cardiology", "Room 201", "₹800", ["10:00 AM", "11:30 AM", "2:00 PM", "4:00 PM"]),
    ("doc_2", "Dr. Arjun Patel", "Dermatology", "Room 104", "₹650", ["9:30 AM", "1:00 PM", "3:30 PM"]),
    ("doc_3", "Dr. Meera Nair", "General Medicine", "Room 102", "₹500", ["10:30 AM", "12:00 PM", "3:00 PM", "5:00 PM"]),
]


def _conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with _conn() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS doctors (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                specialty TEXT NOT NULL,
                room TEXT NOT NULL,
                fee TEXT NOT NULL,
                slots TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS appointments (
                id TEXT PRIMARY KEY,
                doctor_name TEXT NOT NULL,
                specialty TEXT NOT NULL,
                slot TEXT NOT NULL,
                patient_name TEXT NOT NULL,
                phone_number TEXT NOT NULL,
                reason TEXT NOT NULL,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS call_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                started_at TEXT NOT NULL,
                duration_sec INTEGER NOT NULL,
                outcome TEXT NOT NULL,
                transcript TEXT NOT NULL
            )
            """
        )
        count = conn.execute("SELECT COUNT(*) FROM doctors").fetchone()[0]
        if count == 0:
            for doc_id, name, spec, room, fee, slots in DEFAULT_DOCTORS:
                conn.execute(
                    "INSERT INTO doctors (id, name, specialty, room, fee, slots) VALUES (?, ?, ?, ?, ?, ?)",
                    (doc_id, name, spec, room, fee, json.dumps(slots)),
                )


init_db()


def get_all_doctors() -> list[dict]:
    with _conn() as conn:
        rows = conn.execute("SELECT id, name, specialty, room, fee, slots FROM doctors").fetchall()
    return [
        {
            "id": r["id"],
            "name": r["name"],
            "specialty": r["specialty"],
            "room": r["room"],
            "fee": r["fee"],
            "slots": json.loads(r["slots"]),
        }
        for r in rows
    ]


def get_all_appointments() -> list[dict]:
    with _conn() as conn:
        rows = conn.execute(
            "SELECT * FROM appointments ORDER BY created_at DESC"
        ).fetchall()
    return [dict(r) for r in rows]


def get_appointments_by_phone(phone_number: str) -> dict:
    cleaned = phone_number.strip()
    with _conn() as conn:
        rows = conn.execute(
            "SELECT * FROM appointments WHERE phone_number = ? ORDER BY created_at DESC",
            (cleaned,),
        ).fetchall()
    if not rows:
        return {"found": False, "message": f"No appointments found for {cleaned}."}
    return {"found": True, "appointments": [dict(r) for r in rows]}


def get_call_history() -> list[dict]:
    with _conn() as conn:
        rows = conn.execute(
            "SELECT * FROM call_history ORDER BY id DESC LIMIT 20"
        ).fetchall()
    return [
        {
            "id": r["id"],
            "started_at": r["started_at"],
            "duration_sec": r["duration_sec"],
            "outcome": r["outcome"],
            "transcript": json.loads(r["transcript"]),
        }
        for r in rows
    ]


def save_call_record(started_at: str, duration_sec: int, outcome: str, transcript: list):
    with _conn() as conn:
        conn.execute(
            "INSERT INTO call_history (started_at, duration_sec, outcome, transcript) VALUES (?, ?, ?, ?)",
            (started_at, duration_sec, outcome, json.dumps(transcript)),
        )


def add_slot(doctor_id: str, slot: str) -> dict:
    clean_slot = slot.strip()
    if not clean_slot:
        return {"success": False, "message": "Slot cannot be empty."}
    with _conn() as conn:
        row = conn.execute("SELECT slots FROM doctors WHERE id = ?", (doctor_id,)).fetchone()
        if not row:
            return {"success": False, "message": "Doctor not found."}
        slots = json.loads(row["slots"])
        if any(s.lower() == clean_slot.lower() for s in slots):
            return {"success": False, "message": "Slot already exists."}
        slots.append(clean_slot)
        conn.execute("UPDATE doctors SET slots = ? WHERE id = ?", (json.dumps(slots), doctor_id))
    return {"success": True, "slots": slots}


def remove_slot(doctor_id: str, slot: str) -> dict:
    with _conn() as conn:
        row = conn.execute("SELECT slots FROM doctors WHERE id = ?", (doctor_id,)).fetchone()
        if not row:
            return {"success": False, "message": "Doctor not found."}
        slots = [s for s in json.loads(row["slots"]) if s != slot]
        conn.execute("UPDATE doctors SET slots = ? WHERE id = ?", (json.dumps(slots), doctor_id))
    return {"success": True, "slots": slots}


def check_doctor_availability(doctor_name: str = "") -> dict:
    doctors = get_all_doctors()
    if not doctor_name:
        return {"clinic": CLINIC_INFO, "doctors": doctors}

    query = doctor_name.lower().strip()
    matches = [
        doc for doc in doctors
        if query in doc["name"].lower() or query in doc["specialty"].lower()
    ]
    if not matches:
        return {
            "error": f"No doctor found matching '{doctor_name}'.",
            "available_doctors": [f"{d['name']} ({d['specialty']}, {d['fee']})" for d in doctors],
        }
    return {"clinic": CLINIC_INFO, "doctors": matches}


def book_appointment(
    doctor_name: str,
    slot: str,
    phone_number: str,
    patient_name: str = "Caller",
    reason: str = "General consultation",
) -> dict:
    doctors = get_all_doctors()
    query = doctor_name.lower().strip()
    doctor = next(
        (d for d in doctors if query in d["name"].lower() or query in d["specialty"].lower()),
        None,
    )
    if not doctor:
        return {"success": False, "message": f"Doctor '{doctor_name}' not found."}

    matched_slot = next(
        (s for s in doctor["slots"] if s.lower() == slot.lower().strip()),
        None,
    )
    if not matched_slot:
        return {
            "success": False,
            "message": f"Slot '{slot}' is not available for {doctor['name']}. Available slots: {', '.join(doctor['slots'])}",
        }

    new_slots = [s for s in doctor["slots"] if s != matched_slot]
    apt_id = f"apt_{int(datetime.now().timestamp() * 1000) % 1000000}"
    created_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    clean_name = patient_name.strip() if patient_name and patient_name.strip() else "Caller"
    clean_reason = reason.strip() if reason and reason.strip() else "General consultation"

    with _conn() as conn:
        conn.execute(
            "UPDATE doctors SET slots = ? WHERE id = ?",
            (json.dumps(new_slots), doctor["id"]),
        )
        conn.execute(
            """
            INSERT INTO appointments (id, doctor_name, specialty, slot, patient_name, phone_number, reason, status, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                apt_id,
                doctor["name"],
                doctor["specialty"],
                matched_slot,
                clean_name,
                phone_number.strip(),
                clean_reason,
                "Booked",
                created_at,
            ),
        )

    return {
        "success": True,
        "appointment": {
            "id": apt_id,
            "doctor_name": doctor["name"],
            "room": doctor["room"],
            "fee": doctor["fee"],
            "slot": matched_slot,
            "patient_name": clean_name,
            "phone_number": phone_number.strip(),
            "reason": clean_reason,
            "status": "Booked",
        },
    }


def reschedule_appointment(phone_number: str, new_slot: str, new_doctor_name: str = "") -> dict:
    cleaned_phone = phone_number.strip()
    with _conn() as conn:
        row = conn.execute(
            "SELECT * FROM appointments WHERE phone_number = ? AND status = 'Booked' ORDER BY created_at DESC LIMIT 1",
            (cleaned_phone,),
        ).fetchone()
    if not row:
        return {"success": False, "message": f"No active appointment found for {cleaned_phone}."}

    target_doc = new_doctor_name.strip() or row["doctor_name"]
    doctors = get_all_doctors()
    doctor = next(
        (d for d in doctors if target_doc.lower() in d["name"].lower() or target_doc.lower() in d["specialty"].lower()),
        None,
    )
    if not doctor:
        return {"success": False, "message": f"Doctor '{target_doc}' not found."}

    matched_slot = next(
        (s for s in doctor["slots"] if s.lower() == new_slot.lower().strip()),
        None,
    )
    if not matched_slot:
        return {
            "success": False,
            "message": f"Slot '{new_slot}' is not available for {doctor['name']}. Available: {', '.join(doctor['slots'])}",
        }

    old_doc = next((d for d in doctors if d["name"] == row["doctor_name"]), None)
    with _conn() as conn:
        if old_doc:
            old_slots = old_doc["slots"]
            if row["slot"] not in old_slots:
                old_slots.append(row["slot"])
            conn.execute("UPDATE doctors SET slots = ? WHERE id = ?", (json.dumps(old_slots), old_doc["id"]))

        updated_target_slots = [
            s
            for s in json.loads(
                conn.execute("SELECT slots FROM doctors WHERE id = ?", (doctor["id"],)).fetchone()["slots"]
            )
            if s != matched_slot
        ]
        conn.execute("UPDATE doctors SET slots = ? WHERE id = ?", (json.dumps(updated_target_slots), doctor["id"]))
        conn.execute(
            "UPDATE appointments SET doctor_name = ?, specialty = ?, slot = ? WHERE id = ?",
            (doctor["name"], doctor["specialty"], matched_slot, row["id"]),
        )

    return {
        "success": True,
        "message": f"Rescheduled to {doctor['name']} ({doctor['room']}) at {matched_slot}.",
        "doctor_name": doctor["name"],
        "slot": matched_slot,
    }


def update_appointment_status(apt_id: str, new_status: str) -> dict:
    if new_status not in ("Booked", "Checked In", "Completed", "Cancelled"):
        return {"success": False, "message": "Invalid status."}
    if new_status == "Cancelled":
        return cancel_appointment_by_id(apt_id)
    with _conn() as conn:
        row = conn.execute("SELECT * FROM appointments WHERE id = ?", (apt_id,)).fetchone()
        if not row:
            return {"success": False, "message": "Appointment not found."}
        conn.execute("UPDATE appointments SET status = ? WHERE id = ?", (new_status, apt_id))
    return {"success": True, "id": apt_id, "status": new_status, "patient_name": row["patient_name"]}


def cancel_appointment(phone_number: str) -> dict:
    cleaned_phone = phone_number.strip()
    with _conn() as conn:
        row = conn.execute(
            "SELECT * FROM appointments WHERE phone_number = ? AND status = 'Booked' ORDER BY created_at DESC LIMIT 1",
            (cleaned_phone,),
        ).fetchone()
    if not row:
        return {"success": False, "message": f"No active appointment found for {cleaned_phone}."}
    return cancel_appointment_by_id(row["id"])


def cancel_appointment_by_id(apt_id: str) -> dict:
    with _conn() as conn:
        row = conn.execute("SELECT * FROM appointments WHERE id = ?", (apt_id,)).fetchone()
        if not row or row["status"] == "Cancelled":
            return {"success": False, "message": "Active appointment not found."}
        conn.execute("UPDATE appointments SET status = 'Cancelled' WHERE id = ?", (apt_id,))
        doc_row = conn.execute(
            "SELECT id, slots FROM doctors WHERE name = ?", (row["doctor_name"],)
        ).fetchone()
        if doc_row:
            slots = json.loads(doc_row["slots"])
            if row["slot"] not in slots:
                slots.append(row["slot"])
                conn.execute("UPDATE doctors SET slots = ? WHERE id = ?", (json.dumps(slots), doc_row["id"]))

    return {"success": True, "cancelled_id": apt_id, "doctor_name": row["doctor_name"], "slot": row["slot"]}