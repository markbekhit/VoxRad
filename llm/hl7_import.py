"""HL7 v2 ORM^O01 inbound order parser for VoxRad.

Parses radiology orders delivered by a hospital integration engine to a
VoxRad inbox directory and exposes them as Python dicts matching VoxRad's
patient_context shape.

Typical RIS → VoxRad flow
-------------------------
  1. RIS emits an ORM^O01 when the scan is acquired.
  2. The integration engine (Sectra, Kestral, Intelerad, Voyager, etc.)
     drops the message in /var/voxrad/hl7/inbox/*.hl7.
  3. VoxRad's /api/hl7/worklist endpoint scans the inbox and returns
     pending orders.
  4. The radiologist picks an order; VoxRad pre-fills patient_name, DOB,
     accession, modality, procedure, and referring_physician for the
     dictation.
  5. When the report is finalised, the outbound ORU^R01 carries the same
     accession number back to the RIS (see llm/hl7_export.py).
"""

from __future__ import annotations

import logging
import os
import re
import time
from datetime import datetime
from typing import Optional

logger = logging.getLogger(__name__)

# Fallback delimiters; real values come from MSH-1/MSH-2 per message.
_DEFAULT_FIELD_SEP = "|"
_DEFAULT_COMP_SEP = "^"
_DEFAULT_REP_SEP = "~"
_DEFAULT_ESC = "\\"
_DEFAULT_SUB_SEP = "&"

_VALID_EXTENSIONS = (".hl7", ".HL7", ".txt", ".TXT", ".dat", ".DAT")
_MWL_EXTENSIONS = (".json", ".JSON")
# MWL bridge agents write one JSON file per order into the same inbox dir.
# The file contains the already-parsed order dict so no HL7 parsing is needed.
_MWL_FIELDS = (
    "patient_name", "patient_dob", "patient_id",
    "accession", "modality", "body_part", "procedure",
    "referring_physician", "scheduled_datetime",
)

# --- Robustness guards ------------------------------------------------------
# An HL7 ORM^O01 is typically < 10 KB. Anything > 5 MB is almost certainly a
# misdelivery (a full DICOM file, a log blob, a runaway export). Reject rather
# than OOM the /worklist request parsing it.
_MAX_INBOX_FILE_BYTES = 5 * 1024 * 1024

# Integration engines typically create `.foo.hl7` or `foo.hl7.tmp` while
# writing, then rename to the final name atomically. Skip anything that looks
# like an in-progress write or an OS metadata artefact.
_TEMP_FILE_SUFFIXES = (".tmp", ".TMP", ".part", ".PART", ".crdownload", ".swp")
_TEMP_FILE_PREFIXES = (".", "_")

# Guard against partial writes from engines that do NOT use atomic renames:
# don't parse a file whose mtime is within this window of `now` — it may
# still be open for writing. One second is enough for most fsync-then-close
# patterns without noticeably delaying order visibility.
_MIN_INBOX_AGE_SECONDS = 1.0

# Archive subdir names. Files that parse successfully go to `processed/` via
# archive_order(). Files that are malformed / unparseable get quarantined so
# the worklist doesn't repeatedly try — and fail — to parse them on every poll.
_FAILED_SUBDIR = "failed"
_PROCESSED_SUBDIR = "processed"


def _should_defer_file(name: str, full_path: str) -> Optional[str]:
    """Return a skip reason (str) for files that shouldn't be read right now.

    Returns None when the file is ready to parse. Deferred files (too new, in
    a temp state) are skipped silently on this poll and retried on the next
    one — NOT quarantined, since they may become valid moments later.
    """
    if name.startswith(_TEMP_FILE_PREFIXES):
        return "hidden or in-progress prefix"
    if name.endswith(_TEMP_FILE_SUFFIXES):
        return "temp-file suffix"
    try:
        st = os.stat(full_path)
    except OSError:
        return "stat failed"
    if st.st_size == 0:
        return "empty"
    age = time.time() - st.st_mtime
    if age < _MIN_INBOX_AGE_SECONDS:
        return f"too new ({age:.2f}s — may still be writing)"
    return None


def _quarantine(fpath: str, reason: str) -> bool:
    """Move an unparseable inbox file into a `failed/` subdir.

    Keeps the active inbox clean so every /worklist poll doesn't retry the
    same broken file. Filename is prefixed with a timestamp for traceability.
    """
    try:
        inbox_dir = os.path.dirname(fpath)
        failed_dir = os.path.join(inbox_dir, _FAILED_SUBDIR)
        os.makedirs(failed_dir, exist_ok=True)
        base = os.path.basename(fpath)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        dst = os.path.join(failed_dir, f"{ts}_{base}")
        os.replace(fpath, dst)
        logger.warning("Quarantined inbox file %s → %s: %s", base, dst, reason)
        return True
    except OSError as e:
        logger.error("Failed to quarantine %s: %s", fpath, e)
        return False


def _read_inbox_text(fpath: str) -> Optional[str]:
    """Read an HL7 inbox file with encoding fallback.

    HL7 v2 is conventionally ASCII; real-world messages may carry non-ASCII
    Latin-1 characters in names (German ß, Swedish å, etc.). Try UTF-8 first
    because modern engines default to it, then fall back to Latin-1 (always
    succeeds, never corrupts ASCII).
    """
    try:
        size = os.path.getsize(fpath)
    except OSError as e:
        logger.warning("stat failed on %s: %s", fpath, e)
        return None
    if size > _MAX_INBOX_FILE_BYTES:
        logger.warning(
            "Inbox file %s is %d bytes (cap %d) — quarantining",
            fpath, size, _MAX_INBOX_FILE_BYTES,
        )
        _quarantine(fpath, f"over-size ({size} bytes)")
        return None
    for enc in ("utf-8", "latin-1"):
        try:
            with open(fpath, "r", encoding=enc) as f:
                return f.read()
        except UnicodeDecodeError:
            continue
        except OSError as e:
            logger.warning("Read failed on %s: %s", fpath, e)
            return None
    logger.warning("Unable to decode %s with utf-8 or latin-1 — quarantining", fpath)
    _quarantine(fpath, "encoding decode failed")
    return None


def _unescape(value: str, esc: str) -> str:
    """Reverse HL7 v2 escape sequences (\\F\\, \\S\\, \\R\\, \\T\\, \\E\\, \\.br\\)."""
    if not value or esc not in value:
        return value
    replacements = {
        f"{esc}F{esc}": "|",
        f"{esc}S{esc}": "^",
        f"{esc}R{esc}": "~",
        f"{esc}T{esc}": "&",
        f"{esc}.br{esc}": "\n",
        f"{esc}E{esc}": esc,  # must be last to avoid re-escaping
    }
    v = value
    for k, r in replacements.items():
        v = v.replace(k, r)
    return v


def _parse_xpn(value: str, comp_sep: str, esc: str) -> str:
    """Parse an HL7 XPN name field (Last^First^Middle^...) to 'Last, First Middle'."""
    if not value:
        return ""
    parts = value.split(comp_sep)
    last = _unescape(parts[0], esc) if len(parts) > 0 else ""
    first = _unescape(parts[1], esc) if len(parts) > 1 else ""
    middle = _unescape(parts[2], esc) if len(parts) > 2 else ""
    given = " ".join(p for p in (first, middle) if p)
    if last and given:
        return f"{last}, {given}"
    return last or given


def _parse_xcn(value: str, comp_sep: str, rep_sep: str, esc: str) -> str:
    """Parse an HL7 XCN (extended composite ID) into a human name.

    XCN format: ID^Family^Given^Middle^Suffix^Prefix^Degree
    (The leading ID is often a medical registration number.)
    """
    if not value:
        return ""
    first_rep = value.split(rep_sep)[0]
    parts = first_rep.split(comp_sep)
    last = _unescape(parts[1], esc) if len(parts) > 1 else ""
    first = _unescape(parts[2], esc) if len(parts) > 2 else ""
    prefix = _unescape(parts[5], esc) if len(parts) > 5 else ""
    given = " ".join(p for p in (prefix, first, last) if p)
    return given


def _parse_ts_dmy(value: str) -> str:
    """Normalise an HL7 TS timestamp (YYYYMMDD[HHMM[SS]]) to DD/MM/YYYY."""
    if not value:
        return ""
    m = re.match(r"^(\d{4})(\d{2})(\d{2})", value)
    if not m:
        return ""
    year, month, day = m.groups()
    return f"{day}/{month}/{year}"


_KNOWN_MODALITY_CODES = {
    "CT", "MR", "US", "XR", "DX", "CR", "NM", "PT", "MG",
    "FL", "RF", "BD", "OT", "XA", "ES", "GM", "SR", "SC",
}


def _extract_modality(procedure: str, obr24: str) -> str:
    """Derive modality from OBR-24 if it looks like a modality code, else from procedure prefix."""
    if obr24:
        candidate = obr24.strip().upper()
        if candidate in _KNOWN_MODALITY_CODES:
            return candidate
        # Otherwise fall through to description-based extraction (some RIS engines
        # misuse OBR-24 for result-status or other flags).
    if not procedure:
        return ""
    m = re.match(r"^\s*(CT|MR|MRI|US|XR|X-RAY|NM|PT|PET|DX|MG|FL|BD|OT)\b", procedure, re.IGNORECASE)
    if m:
        raw = m.group(1).upper()
        return {"MRI": "MR", "X-RAY": "XR", "PET": "PT"}.get(raw, raw)
    return ""


def _extract_body_part(procedure: str, modality: str) -> str:
    """Strip the modality prefix and contrast suffix to leave the body region."""
    if not procedure:
        return ""
    s = procedure.strip()
    # Remove leading modality token
    if modality:
        s = re.sub(rf"^\s*{re.escape(modality)}\s+", "", s, flags=re.IGNORECASE)
    s = re.sub(r"^\s*(CT|MR|MRI|US|XR|X-RAY|NM|PT|PET|DX|MG)\s+", "", s, flags=re.IGNORECASE)
    # Trim common contrast / technique qualifiers.
    s = re.sub(r"\s+(WITH|WITHOUT|W/|W/O|\+/-)\s+CONTRAST.*$", "", s, flags=re.IGNORECASE)
    return s.strip(" -,")


def parse_orm_o01(message: str) -> Optional[dict]:
    """Parse an HL7 v2 ORM^O01 message into a VoxRad patient context dict.

    Returned dict keys mirror FormatRequest fields:
      accession, patient_id, patient_name, patient_dob,
      modality, body_part, referring_physician, procedure, order_datetime

    Returns None if the message isn't a well-formed ORM^O01.
    """
    if not message or not message.strip():
        return None

    # HL7 files arrive with \r, \n, or \r\n segment terminators.
    msg = message.replace("\r\n", "\r").replace("\n", "\r").strip("\r")
    segments = [s for s in msg.split("\r") if s.strip()]
    if not segments:
        return None

    first = segments[0]
    if not first.startswith("MSH") or len(first) < 8:
        logger.debug("Not an HL7 message (no MSH header): %r", first[:40])
        return None

    # MSH-1 is the field separator; MSH-2 is the next four encoding chars.
    field_sep = first[3]
    enc = first[4:8]
    comp_sep = enc[0] if len(enc) > 0 else _DEFAULT_COMP_SEP
    rep_sep  = enc[1] if len(enc) > 1 else _DEFAULT_REP_SEP
    esc      = enc[2] if len(enc) > 2 else _DEFAULT_ESC

    by_segment: dict[str, list[list[str]]] = {}
    for seg in segments:
        fields = seg.split(field_sep)
        by_segment.setdefault(fields[0], []).append(fields)

    # MSH is special: fields[1] is the encoding chars, so MSH-9 = fields[8].
    msh = by_segment.get("MSH", [[]])[0]
    msg_type_field = msh[8] if len(msh) > 8 else ""
    msg_type_parts = msg_type_field.split(comp_sep)
    msg_type = msg_type_parts[0] if msg_type_parts else ""
    trigger = msg_type_parts[1] if len(msg_type_parts) > 1 else ""
    if msg_type != "ORM":
        logger.debug("Skipping non-ORM message: %s^%s", msg_type, trigger)
        return None

    result: dict = {}

    # --- PID ---
    pid = by_segment.get("PID", [None])[0]
    if pid:
        pid3 = pid[3] if len(pid) > 3 else ""
        if pid3:
            first_rep = pid3.split(rep_sep)[0]
            result["patient_id"] = _unescape(first_rep.split(comp_sep)[0], esc)

        pid5 = pid[5] if len(pid) > 5 else ""
        if pid5:
            first_rep = pid5.split(rep_sep)[0]
            result["patient_name"] = _parse_xpn(first_rep, comp_sep, esc)

        pid7 = pid[7] if len(pid) > 7 else ""
        if pid7:
            result["patient_dob"] = _parse_ts_dmy(pid7)

    # --- OBR (the rich one for radiology orders) ---
    obr = by_segment.get("OBR", [None])[0]
    if obr:
        obr3 = obr[3] if len(obr) > 3 else ""
        if obr3:
            result["accession"] = _unescape(obr3.split(comp_sep)[0], esc)

        obr4 = obr[4] if len(obr) > 4 else ""
        procedure = ""
        if obr4:
            parts = obr4.split(comp_sep)
            # Universal service ID is code^description^coding_system
            procedure = _unescape(parts[1], esc) if len(parts) > 1 else _unescape(parts[0], esc)
            result["procedure"] = procedure

        obr16 = obr[16] if len(obr) > 16 else ""
        if obr16:
            name = _parse_xcn(obr16, comp_sep, rep_sep, esc)
            if name:
                result["referring_physician"] = name if name.lower().startswith("dr") else f"Dr. {name}"

        obr24 = obr[24] if len(obr) > 24 else ""
        modality = _extract_modality(procedure, obr24)
        if modality:
            result["modality"] = modality
            body = _extract_body_part(procedure, modality)
            if body:
                result["body_part"] = body

        obr7 = obr[7] if len(obr) > 7 else ""
        if obr7:
            result["order_datetime"] = obr7  # raw; UI formats as needed

    # --- ORC (fallback for accession if OBR-3 missing) ---
    if "accession" not in result:
        orc = by_segment.get("ORC", [None])[0]
        if orc:
            orc3 = orc[3] if len(orc) > 3 else ""
            if orc3:
                result["accession"] = _unescape(orc3.split(comp_sep)[0], esc)

    return result if result else None


def list_inbox(inbox_path: str) -> list[dict]:
    """Scan an HL7 inbox directory and return parsed ORM^O01 orders.

    Behaviour:
    - Files matching `_VALID_EXTENSIONS` are parsed as HL7 v2 ORM^O01.
    - Files matching `_MWL_EXTENSIONS` are loaded as pre-parsed JSON orders.
    - Hidden files, temp-suffixed files, and files whose mtime is within the
      last second are deferred (silently retried on the next poll).
    - Files too large to be a plausible ORM (> `_MAX_INBOX_FILE_BYTES`),
      files that fail to decode, and files that fail to parse are moved to
      `failed/` so the inbox doesn't keep retrying the same broken input.
    - `processed/` and `failed/` subdirectories are skipped.
    - The file's stable name (without extension) is returned as ``order_id``
      so the UI can reference and dismiss individual entries.
    """
    if not inbox_path or not os.path.isdir(inbox_path):
        return []
    orders: list[dict] = []
    try:
        entries = sorted(os.listdir(inbox_path))
    except OSError as e:
        logger.warning("Could not list HL7 inbox %s: %s", inbox_path, e)
        return []

    for name in entries:
        fpath = os.path.join(inbox_path, name)
        # Skip the archive subdirs and any other directories quickly.
        if not os.path.isfile(fpath):
            continue

        # Extension gate — applied BEFORE defer check so we don't even stat
        # files that aren't ours to begin with (Thumbs.db, .DS_Store, etc.
        # are also caught by the hidden-prefix check below).
        is_hl7 = name.endswith(_VALID_EXTENSIONS)
        is_mwl = name.endswith(_MWL_EXTENSIONS)
        if not (is_hl7 or is_mwl):
            continue

        defer_reason = _should_defer_file(name, fpath)
        if defer_reason is not None:
            logger.debug("Deferring inbox file %s: %s", name, defer_reason)
            continue

        parsed: Optional[dict] = None

        if is_hl7:
            content = _read_inbox_text(fpath)
            if content is None:
                # _read_inbox_text already quarantined or logged.
                continue
            try:
                parsed = parse_orm_o01(content)
            except Exception as e:
                # parse_orm_o01 normally returns None on failure; an
                # exception here is a programming/edge-case bug. Quarantine
                # so operators can inspect the offending message.
                logger.warning("Parser crashed on %s: %s", name, e)
                _quarantine(fpath, f"parser exception: {e!r}")
                continue
            if not parsed:
                # Not an ORM, malformed MSH, or missing required segments.
                _quarantine(fpath, "not a parseable ORM^O01 message")
                continue

        elif is_mwl:
            try:
                import json
                with open(fpath, "r", encoding="utf-8") as f:
                    raw = json.load(f)
            except (OSError, ValueError) as e:
                logger.warning("Could not read MWL inbox file %s: %s", fpath, e)
                _quarantine(fpath, f"json error: {e}")
                continue
            if not isinstance(raw, dict):
                _quarantine(fpath, "JSON root is not an object")
                continue
            parsed = {k: raw.get(k) for k in _MWL_FIELDS if raw.get(k)}
            if not parsed:
                _quarantine(fpath, "no recognised MWL fields")
                continue
            parsed["source"] = raw.get("source", "mwl")

        if not parsed:
            continue
        parsed["order_id"] = os.path.splitext(name)[0]
        parsed["source_file"] = name
        try:
            parsed["received_at"] = int(os.path.getmtime(fpath))
        except OSError:
            pass
        orders.append(parsed)
    return orders


def archive_order(inbox_path: str, order_id: str, archive_dirname: str = _PROCESSED_SUBDIR) -> bool:
    """Move a processed order file out of the inbox into a subfolder.

    Returns True on success, False if the file wasn't found or couldn't be moved.
    """
    if not inbox_path or not order_id:
        return False
    archive_dir = os.path.join(inbox_path, archive_dirname)
    try:
        os.makedirs(archive_dir, exist_ok=True)
    except OSError as e:
        logger.warning("Could not create HL7 archive dir: %s", e)
        return False

    for ext in _VALID_EXTENSIONS + _MWL_EXTENSIONS:
        src = os.path.join(inbox_path, f"{order_id}{ext}")
        if os.path.isfile(src):
            dst = os.path.join(archive_dir, f"{order_id}{ext}")
            try:
                os.replace(src, dst)
                return True
            except OSError as e:
                logger.warning("Could not archive %s: %s", src, e)
                return False
    return False
