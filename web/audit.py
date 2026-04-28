"""Audit log + report sign-off / amendment versioning.

Phase 1 of the post-PACS roadmap — clinical-governance posture for AU/NZ
practices. Every clinically meaningful event lands in a tamper-evident hash
chain in the same SQLite DB as users / settings.

Two tables:

    reports     — every signed-off report version (preliminary / final / amended).
                  Amendments form a linked list via prior_version_id.
    audit_log   — append-only event journal. Each row's row_hash includes the
                  previous row's row_hash, so any retroactive edit / deletion
                  is detectable by re-walking the chain.

This module is intentionally side-effect-free at import time; the schema is
created by init_audit_db(), which web.app calls at startup.
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from typing import Iterable, Optional

# Reuse the same DB path resolver / connection helper as auth_oauth so
# everything lives in users.db (and on Fly the persistent /data volume).
from web.auth_oauth import _conn, _db_path  # noqa: F401  (re-export indirectly)


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

REPORT_STATUSES = ("preliminary", "final", "amended")
EVENT_TYPES = (
    "login", "logout",
    "transcribe", "format", "edit",
    "sign_off", "amend",
    "export_hl7", "export_sr", "export_fhir",
    "qa_check", "qa_dismiss",
    "vocab_add", "style_apply",
)


def init_audit_db() -> None:
    """Create the audit + reports tables idempotently."""
    with _conn() as db:
        db.execute("""
            CREATE TABLE IF NOT EXISTS reports (
                id                INTEGER PRIMARY KEY,
                user_id           INTEGER NOT NULL REFERENCES users(id),
                accession         TEXT,
                patient_id        TEXT,
                patient_name      TEXT,
                patient_dob       TEXT,
                modality          TEXT,
                body_part         TEXT,
                referring         TEXT,
                radiologist       TEXT,
                template_name     TEXT,
                report_text       TEXT NOT NULL,
                report_hash       TEXT NOT NULL,
                status            TEXT NOT NULL,
                version           INTEGER NOT NULL DEFAULT 1,
                prior_version_id  INTEGER REFERENCES reports(id),
                amendment_reason  TEXT,
                signed_at         TEXT,
                created_at        TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)
        db.execute("CREATE INDEX IF NOT EXISTS reports_accession_idx ON reports(accession)")
        db.execute("CREATE INDEX IF NOT EXISTS reports_user_idx ON reports(user_id)")

        db.execute("""
            CREATE TABLE IF NOT EXISTS audit_log (
                id                INTEGER PRIMARY KEY,
                user_id           INTEGER REFERENCES users(id),
                report_id         INTEGER REFERENCES reports(id),
                accession         TEXT,
                event_type        TEXT NOT NULL,
                event_metadata    TEXT,
                payload_hash      TEXT,
                prev_hash         TEXT,
                row_hash          TEXT NOT NULL,
                created_at        TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)
        db.execute("CREATE INDEX IF NOT EXISTS audit_user_idx ON audit_log(user_id)")
        db.execute("CREATE INDEX IF NOT EXISTS audit_accession_idx ON audit_log(accession)")
        db.execute("CREATE INDEX IF NOT EXISTS audit_report_idx ON audit_log(report_id)")
        db.commit()


# ---------------------------------------------------------------------------
# Hash chain helpers
# ---------------------------------------------------------------------------

def _sha256_hex(data: bytes | str) -> str:
    if isinstance(data, str):
        data = data.encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def _row_hash(prev_hash: str, fields: dict) -> str:
    """Hash the prev_hash + canonicalised fields. Order is fixed."""
    canon = json.dumps(fields, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return _sha256_hex((prev_hash or "") + "|" + canon)


def _last_hash(db: sqlite3.Connection) -> str:
    row = db.execute("SELECT row_hash FROM audit_log ORDER BY id DESC LIMIT 1").fetchone()
    return row[0] if row else ""


def log_event(
    *,
    user_id: Optional[int],
    event_type: str,
    accession: Optional[str] = None,
    report_id: Optional[int] = None,
    metadata: Optional[dict] = None,
) -> int:
    """Append an event to the audit chain. Returns the new row id.

    Logging is best-effort — a failure here must NOT prevent the underlying
    user action from succeeding. Callers are responsible for catching, but
    we also swallow internal errors and return -1 to be safe.
    """
    if event_type not in EVENT_TYPES:
        # Tolerate unknown event types so future additions don't crash the
        # endpoint that introduced them.
        pass
    try:
        meta = json.dumps(metadata, sort_keys=True, ensure_ascii=False) if metadata else None
        payload_hash = _sha256_hex(meta) if meta else None
        with _conn() as db:
            prev = _last_hash(db)
            fields = {
                "user_id": user_id,
                "report_id": report_id,
                "accession": accession,
                "event_type": event_type,
                "payload_hash": payload_hash,
            }
            rh = _row_hash(prev, fields)
            cur = db.execute(
                "INSERT INTO audit_log "
                "(user_id, report_id, accession, event_type, event_metadata, "
                " payload_hash, prev_hash, row_hash) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (user_id, report_id, accession, event_type, meta, payload_hash, prev, rh),
            )
            db.commit()
            return int(cur.lastrowid or -1)
    except Exception:
        # Last-resort guard: the audit log must never bring down the app.
        return -1


def verify_chain(limit: Optional[int] = None) -> dict:
    """Re-walk the audit log and verify each row's row_hash.

    Catches three classes of tampering:
      - prev_hash mismatch (row inserted / removed elsewhere in the chain)
      - row_hash mismatch (any of {user_id, report_id, accession, event_type,
        payload_hash} edited)
      - payload_hash mismatch vs stored metadata (metadata edited
        retroactively without bumping the hash)

    Returns {"ok": bool, "checked": int, "first_bad_id": int|None,
             "reason": str|None}.
    """
    bad_id: Optional[int] = None
    bad_reason: Optional[str] = None
    checked = 0
    with _conn() as db:
        q = (
            "SELECT id, user_id, report_id, accession, event_type, "
            "event_metadata, payload_hash, prev_hash, row_hash "
            "FROM audit_log ORDER BY id"
        )
        if limit:
            q += f" LIMIT {int(limit)}"
        prev = ""
        for row in db.execute(q):
            (rid, user_id, report_id, accession, event_type,
             event_metadata, payload_hash, stored_prev, stored_row) = row
            checked += 1
            if (stored_prev or "") != prev:
                bad_id, bad_reason = rid, "prev_hash mismatch"
                break
            expected_payload = _sha256_hex(event_metadata) if event_metadata else None
            if expected_payload != payload_hash:
                bad_id, bad_reason = rid, "metadata vs payload_hash mismatch"
                break
            fields = {
                "user_id": user_id,
                "report_id": report_id,
                "accession": accession,
                "event_type": event_type,
                "payload_hash": payload_hash,
            }
            expected = _row_hash(prev, fields)
            if expected != stored_row:
                bad_id, bad_reason = rid, "row_hash mismatch"
                break
            prev = stored_row
    return {
        "ok": bad_id is None,
        "checked": checked,
        "first_bad_id": bad_id,
        "reason": bad_reason,
    }


# ---------------------------------------------------------------------------
# Reports — sign-off + amendment helpers
# ---------------------------------------------------------------------------

def _serialise_report_row(row: sqlite3.Row | tuple) -> dict:
    return {
        "id": row[0],
        "user_id": row[1],
        "accession": row[2],
        "patient_id": row[3],
        "patient_name": row[4],
        "patient_dob": row[5],
        "modality": row[6],
        "body_part": row[7],
        "referring": row[8],
        "radiologist": row[9],
        "template_name": row[10],
        "report_text": row[11],
        "report_hash": row[12],
        "status": row[13],
        "version": row[14],
        "prior_version_id": row[15],
        "amendment_reason": row[16],
        "signed_at": row[17],
        "created_at": row[18],
    }


_REPORT_COLS = (
    "id, user_id, accession, patient_id, patient_name, patient_dob, "
    "modality, body_part, referring, radiologist, template_name, "
    "report_text, report_hash, status, version, prior_version_id, "
    "amendment_reason, signed_at, created_at"
)


def save_report_version(
    *,
    user_id: int,
    report_text: str,
    status: str,
    accession: Optional[str] = None,
    patient_id: Optional[str] = None,
    patient_name: Optional[str] = None,
    patient_dob: Optional[str] = None,
    modality: Optional[str] = None,
    body_part: Optional[str] = None,
    referring: Optional[str] = None,
    radiologist: Optional[str] = None,
    template_name: Optional[str] = None,
    prior_version_id: Optional[int] = None,
    amendment_reason: Optional[str] = None,
) -> dict:
    """Persist a report row and return the saved record."""
    if status not in REPORT_STATUSES:
        raise ValueError(f"invalid status: {status}")
    if not report_text or not report_text.strip():
        raise ValueError("report_text required")

    report_hash = _sha256_hex(report_text)

    # Compute version: max(version) for this accession + 1, else 1
    version = 1
    with _conn() as db:
        if accession:
            row = db.execute(
                "SELECT COALESCE(MAX(version), 0) FROM reports WHERE accession = ?",
                (accession,),
            ).fetchone()
            version = (row[0] or 0) + 1
        elif prior_version_id:
            row = db.execute(
                "SELECT version FROM reports WHERE id = ?", (prior_version_id,)
            ).fetchone()
            version = (row[0] if row else 0) + 1

        signed_at = "CURRENT_TIMESTAMP" if status in ("final", "amended") else None
        if signed_at:
            cur = db.execute(
                "INSERT INTO reports "
                "(user_id, accession, patient_id, patient_name, patient_dob, "
                " modality, body_part, referring, radiologist, template_name, "
                " report_text, report_hash, status, version, prior_version_id, "
                " amendment_reason, signed_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, "
                "        CURRENT_TIMESTAMP)",
                (user_id, accession, patient_id, patient_name, patient_dob,
                 modality, body_part, referring, radiologist, template_name,
                 report_text, report_hash, status, version, prior_version_id,
                 amendment_reason),
            )
        else:
            cur = db.execute(
                "INSERT INTO reports "
                "(user_id, accession, patient_id, patient_name, patient_dob, "
                " modality, body_part, referring, radiologist, template_name, "
                " report_text, report_hash, status, version, prior_version_id, "
                " amendment_reason) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (user_id, accession, patient_id, patient_name, patient_dob,
                 modality, body_part, referring, radiologist, template_name,
                 report_text, report_hash, status, version, prior_version_id,
                 amendment_reason),
            )
        rid = int(cur.lastrowid or -1)
        db.commit()
        row = db.execute(
            f"SELECT {_REPORT_COLS} FROM reports WHERE id = ?", (rid,)
        ).fetchone()
    return _serialise_report_row(row)


def get_report(report_id: int) -> Optional[dict]:
    with _conn() as db:
        row = db.execute(
            f"SELECT {_REPORT_COLS} FROM reports WHERE id = ?", (report_id,)
        ).fetchone()
    return _serialise_report_row(row) if row else None


def list_reports_for_accession(accession: str) -> list[dict]:
    with _conn() as db:
        rows = db.execute(
            f"SELECT {_REPORT_COLS} FROM reports "
            "WHERE accession = ? ORDER BY version ASC, id ASC",
            (accession,),
        ).fetchall()
    return [_serialise_report_row(r) for r in rows]


def list_reports_for_user(user_id: int, limit: int = 100) -> list[dict]:
    with _conn() as db:
        rows = db.execute(
            f"SELECT {_REPORT_COLS} FROM reports "
            "WHERE user_id = ? ORDER BY id DESC LIMIT ?",
            (user_id, int(limit)),
        ).fetchall()
    return [_serialise_report_row(r) for r in rows]


# ---------------------------------------------------------------------------
# Audit log queries
# ---------------------------------------------------------------------------

def list_audit_events(
    *,
    user_id: Optional[int] = None,
    accession: Optional[str] = None,
    report_id: Optional[int] = None,
    limit: int = 200,
) -> list[dict]:
    where = []
    args: list = []
    if user_id is not None:
        where.append("user_id = ?")
        args.append(user_id)
    if accession:
        where.append("accession = ?")
        args.append(accession)
    if report_id is not None:
        where.append("report_id = ?")
        args.append(report_id)

    sql = (
        "SELECT id, user_id, report_id, accession, event_type, "
        "event_metadata, payload_hash, prev_hash, row_hash, created_at "
        "FROM audit_log "
    )
    if where:
        sql += "WHERE " + " AND ".join(where) + " "
    sql += "ORDER BY id DESC LIMIT ?"
    args.append(int(limit))

    with _conn() as db:
        rows = db.execute(sql, args).fetchall()
    out = []
    for r in rows:
        meta = None
        if r[5]:
            try:
                meta = json.loads(r[5])
            except Exception:
                meta = r[5]
        out.append({
            "id": r[0],
            "user_id": r[1],
            "report_id": r[2],
            "accession": r[3],
            "event_type": r[4],
            "metadata": meta,
            "payload_hash": r[6],
            "prev_hash": r[7],
            "row_hash": r[8],
            "created_at": r[9],
        })
    return out
