"""HL7 v2.4 ORU^R01 export for VoxRad.

Converts a completed radiology report into an HL7 v2.4 ORU^R01 (Observation
Result Unsolicited) message and drops it in a configured outbox directory.
Hospital integration engines (Sectra, Kestral, Intelerad, Voyager, etc.) poll
this directory and route the message to the RIS/PACS.

Usage
-----
    from llm.hl7_export import save_hl7_report
    path = save_hl7_report(
        report_text=report,
        outbox_path="/var/voxrad/hl7",
        patient_context={"patient_name": "Smith, John", ...},
        template_name="CT_Chest.txt",
        sending_facility="VOXRAD",
        receiving_facility="NSWHEALTH",
    )
"""

from __future__ import annotations

import logging
import os
import re
import uuid
from datetime import datetime
from typing import Optional

logger = logging.getLogger(__name__)

_FIELD_SEP = "|"
_COMP_SEP = "^"
_REP_SEP = "~"
_ESC_CHAR = "\\"
_SUBCOMP_SEP = "&"
_SEG_TERM = "\r"

_ENCODING_CHARS = _COMP_SEP + _REP_SEP + _ESC_CHAR + _SUBCOMP_SEP  # "^~\&"


def _escape(value: Optional[str]) -> str:
    """Escape HL7 v2 field-level delimiters in a text value."""
    if not value:
        return ""
    v = str(value)
    # Escape sequences in HL7 v2 are delimited by the escape char on both sides,
    # e.g. \E\ (literal backslash), \F\ (field sep), \S\ (component sep), etc.
    v = v.replace(_ESC_CHAR, "\\E\\")
    v = v.replace(_FIELD_SEP, "\\F\\")
    v = v.replace(_COMP_SEP, "\\S\\")
    v = v.replace(_REP_SEP, "\\R\\")
    v = v.replace(_SUBCOMP_SEP, "\\T\\")
    return v


def _format_name(full_name: Optional[str]) -> str:
    """Split a free-form patient name into HL7 XPN format Last^First^Middle.

    Accepts either "Last, First Middle" or "First Middle Last" conventions.
    """
    if not full_name:
        return ""
    s = full_name.strip()
    if "," in s:
        last, rest = s.split(",", 1)
        parts = rest.strip().split()
    else:
        parts = s.split()
        if len(parts) == 1:
            return _escape(parts[0])
        last = parts[-1]
        parts = parts[:-1]
    first = parts[0] if parts else ""
    middle = " ".join(parts[1:]) if len(parts) > 1 else ""
    return _COMP_SEP.join(_escape(p) for p in (last, first, middle) if p or middle == "")


def _format_dob(dob: Optional[str]) -> str:
    """Normalise a DOB string to HL7 TS format YYYYMMDD.

    Accepts DD/MM/YYYY, YYYY-MM-DD, DD-MM-YYYY, or already-YYYYMMDD.
    Returns an empty string if unparseable.
    """
    if not dob:
        return ""
    s = str(dob).strip()
    patterns = (
        ("%Y%m%d", re.compile(r"^\d{8}$")),
        ("%Y-%m-%d", re.compile(r"^\d{4}-\d{2}-\d{2}$")),
        ("%d/%m/%Y", re.compile(r"^\d{2}/\d{2}/\d{4}$")),
        ("%d-%m-%Y", re.compile(r"^\d{2}-\d{2}-\d{4}$")),
    )
    for fmt, rx in patterns:
        if rx.match(s):
            try:
                return datetime.strptime(s, fmt).strftime("%Y%m%d")
            except ValueError:
                continue
    return ""


def _ts_now() -> str:
    """Current timestamp in HL7 TS format YYYYMMDDHHMMSS (local time)."""
    return datetime.now().strftime("%Y%m%d%H%M%S")


def _format_text_for_ft(text: str) -> str:
    """Escape a block of report text for an OBX FT (formatted text) field.

    Converts newlines to \\.br\\ HL7 formatting commands and escapes
    field separators. Paragraphs are preserved as single FT strings.
    """
    escaped = _escape(text)
    # After escaping, real newlines in the source become literal \n in the
    # string. Replace them with the HL7 line-break formatting command.
    escaped = escaped.replace("\r\n", "\n").replace("\r", "\n")
    escaped = escaped.replace("\n", "\\.br\\")
    return escaped


def build_oru_r01(
    report_text: str,
    patient_context: Optional[dict] = None,
    template_name: Optional[str] = None,
    sending_facility: str = "VOXRAD",
    receiving_facility: str = "",
    message_control_id: Optional[str] = None,
) -> str:
    """Build an HL7 v2.4 ORU^R01 message from a completed report.

    Segments produced: MSH, PID, PV1, ORC, OBR, OBX (one per paragraph).
    Lines are separated by CR (\\r) per HL7 v2 wire format.
    """
    ctx = patient_context or {}
    patient_id = ctx.get("patient_id") or ""
    patient_name = ctx.get("patient_name") or ""
    patient_dob = ctx.get("patient_dob") or ""
    accession = ctx.get("accession") or ""
    modality = ctx.get("modality") or ""
    body_part = ctx.get("body_part") or ""
    radiologist = ctx.get("radiologist") or ""
    referring = ctx.get("referring_physician") or ""

    exam_display = template_name or "Radiology Report"
    exam_display = (
        exam_display.replace("_", " ").replace(".txt", "").replace(".md", "")
    )

    msg_id = message_control_id or uuid.uuid4().hex[:20]
    ts = _ts_now()

    # MSH — message header
    msh = _FIELD_SEP.join([
        "MSH",
        _ENCODING_CHARS,
        _escape(sending_facility),
        _escape(sending_facility),
        "",  # receiving application (leave for integration engine to fill)
        _escape(receiving_facility),
        ts,
        "",
        "ORU^R01^ORU_R01",
        msg_id,
        "P",  # processing ID: P = Production
        "2.4",
    ])

    # PID — patient identification
    pid_name = _format_name(patient_name)
    pid_dob = _format_dob(patient_dob)
    pid = _FIELD_SEP.join([
        "PID",
        "1",
        "",
        _escape(patient_id),
        "",
        pid_name,
        "",
        pid_dob,
    ])

    # PV1 — patient visit (minimal; outpatient unless site overrides)
    pv1 = _FIELD_SEP.join(["PV1", "1", "O"])

    # ORC — common order
    orc = _FIELD_SEP.join(["ORC", "RE"])

    # OBR — observation request
    exam_code_component = _COMP_SEP.join([
        "",
        _escape(exam_display),
        "",
    ])
    obr_diagnostic_service = _COMP_SEP.join([
        _escape(modality) if modality else "",
        _escape(body_part) if body_part else "",
    ])
    obr = _FIELD_SEP.join([
        "OBR",
        "1",
        "",
        _escape(accession),
        exam_code_component,
        "",
        ts,  # observation date/time
        "",
        "",
        "",
        "",
        "",
        "",
        "",
        "",
        "",
        _escape(referring),
        "",
        "",
        "",
        "",
        "",
        "",
        obr_diagnostic_service,
        ts,
        "",
        "",
        "",
        "",
        "F",  # result status: F = Final
        "",
        "",
        "",
        "",
        _escape(radiologist),  # principal result interpreter (OBR-32)
    ])

    # OBX segments — one per paragraph so a single FT field stays readable
    # and no individual segment crosses typical site length limits.
    paragraphs = [p for p in re.split(r"\n\s*\n", report_text.strip()) if p.strip()]
    if not paragraphs:
        paragraphs = [report_text.strip() or "(empty report)"]

    obx_segments = []
    for i, para in enumerate(paragraphs, start=1):
        obx_segments.append(_FIELD_SEP.join([
            "OBX",
            str(i),
            "FT",
            "&GDT^Report Text",
            "",
            _format_text_for_ft(para),
            "",
            "",
            "",
            "",
            "F",
        ]))

    segments = [msh, pid, pv1, orc, obr] + obx_segments
    return _SEG_TERM.join(segments) + _SEG_TERM


def save_hl7_report(
    report_text: str,
    outbox_path: str,
    patient_context: Optional[dict] = None,
    template_name: Optional[str] = None,
    sending_facility: str = "VOXRAD",
    receiving_facility: str = "",
) -> Optional[str]:
    """Build an ORU^R01 and write it to the outbox directory.

    Filename: ``VOXRAD_{accession}_{timestamp}_{uid}.hl7`` (accession replaced
    with ``NOACC`` when not provided; ``uid`` is an 8-char random suffix to
    prevent collisions on concurrent or same-second writes).

    The write is atomic: the message is flushed+fsync'd to a ``.tmp`` file,
    then renamed into place via ``os.replace``. An integration engine polling
    the outbox will therefore never observe a partial/truncated .hl7 file.

    Returns the saved path, or ``None`` on error.
    """
    try:
        if not outbox_path:
            logger.warning("HL7 outbox path not configured; skipping export.")
            return None
        os.makedirs(outbox_path, exist_ok=True)

        message = build_oru_r01(
            report_text=report_text,
            patient_context=patient_context,
            template_name=template_name,
            sending_facility=sending_facility,
            receiving_facility=receiving_facility,
        )

        accession = (patient_context or {}).get("accession") or "NOACC"
        safe_acc = re.sub(r"[^A-Za-z0-9_-]", "_", str(accession))[:40]
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        uid = uuid.uuid4().hex[:8]
        filename = f"VOXRAD_{safe_acc}_{ts}_{uid}.hl7"
        filepath = os.path.join(outbox_path, filename)
        tmp_path = filepath + ".tmp"

        # HL7 wire encoding is typically 8-bit ASCII or ISO-8859-1; UTF-8 is
        # widely accepted by modern integration engines.
        with open(tmp_path, "w", encoding="utf-8", newline="") as f:
            f.write(message)
            f.flush()
            try:
                os.fsync(f.fileno())
            except OSError:
                # Not all filesystems support fsync (tmpfs, NFS with certain
                # mount options). The rename below is still atomic.
                pass
        os.replace(tmp_path, filepath)

        logger.info("HL7 v2.4 ORU^R01 saved: %s", filepath)
        return filepath

    except Exception as e:
        logger.error("HL7 export failed: %s", e)
        # Best-effort cleanup of any leftover .tmp so a retry can succeed.
        try:
            if "tmp_path" in locals() and os.path.exists(tmp_path):
                os.remove(tmp_path)
        except OSError:
            pass
        return None
