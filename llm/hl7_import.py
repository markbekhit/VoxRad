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
from typing import Optional

logger = logging.getLogger(__name__)

# Fallback delimiters; real values come from MSH-1/MSH-2 per message.
_DEFAULT_FIELD_SEP = "|"
_DEFAULT_COMP_SEP = "^"
_DEFAULT_REP_SEP = "~"
_DEFAULT_ESC = "\\"
_DEFAULT_SUB_SEP = "&"

_VALID_EXTENSIONS = (".hl7", ".HL7", ".txt", ".TXT", ".dat", ".DAT")


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

    The file's stable name (without extension) is returned as ``order_id`` so
    the UI can reference and dismiss individual entries. Invalid or non-ORM
    files are silently skipped to keep the worklist resilient to engine
    misconfiguration.
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
        if not name.endswith(_VALID_EXTENSIONS):
            continue
        fpath = os.path.join(inbox_path, name)
        if not os.path.isfile(fpath):
            continue
        try:
            with open(fpath, "r", encoding="utf-8", errors="replace") as f:
                content = f.read()
        except OSError as e:
            logger.warning("Could not read HL7 inbox file %s: %s", fpath, e)
            continue
        parsed = parse_orm_o01(content)
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


def archive_order(inbox_path: str, order_id: str, archive_dirname: str = "processed") -> bool:
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

    for ext in _VALID_EXTENSIONS:
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
