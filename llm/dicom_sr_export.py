"""DICOM Structured Report (Basic Text SR) export for VoxRad.

Writes a finalised radiology report as a DICOM Basic Text SR instance to
an outbox directory, ready for a PACS/RIS to pick up (file-drop model, same
pattern as :mod:`llm.hl7_export`). For clinics whose PACS ingests SR but
not HL7, this is a standards-based, vendor-neutral alternative.

Why Basic Text SR (not Enhanced/Comprehensive)?
-----------------------------------------------
Radiology free-text reports are overwhelmingly sent as Basic Text SR
(SOP Class UID 1.2.840.10008.5.1.4.1.1.88.11). Every PACS that claims
"DICOM SR support" handles it. Enhanced/Comprehensive SR are richer but
require structured coded content — wasted complexity for a narrative
report.

Output shape
------------
A valid DICOM file with:
- Patient module (PatientName, PatientID, PatientBirthDate, PatientSex)
- General Study module (StudyInstanceUID, AccessionNumber, StudyDate,
  StudyTime, ReferringPhysicianName, StudyDescription)
- SR Document Series module (Modality=SR, SeriesInstanceUID,
  SeriesNumber)
- SR Document General module (ContentDate, ContentTime,
  InstanceNumber, CompletionFlag=COMPLETE, VerificationFlag=UNVERIFIED,
  PreliminaryFlag=FINAL)
- SR Document Content module — a CONTAINER root with one TEXT content
  item per paragraph of the report

Usage
-----
    from llm.dicom_sr_export import save_dicom_sr_report
    path = save_dicom_sr_report(
        report_text=report,
        outbox_path="/var/voxrad/sr_outbox",
        patient_context={"patient_name": "Smith, John", ...},
        institution_name="VOXRAD",
    )
"""

from __future__ import annotations

import logging
import os
import re
from datetime import datetime
from typing import Optional

logger = logging.getLogger(__name__)

# Basic Text SR Storage SOP Class
_SOP_CLASS_BASIC_TEXT_SR = "1.2.840.10008.5.1.4.1.1.88.11"

# LOINC code for "Diagnostic imaging report" — standard concept name for
# the SR root container in radiology reports.
_CONCEPT_RADIOLOGY_REPORT = {
    "CodeValue": "18748-4",
    "CodingSchemeDesignator": "LN",
    "CodeMeaning": "Diagnostic imaging report",
}

# Concept for each text content item (a section / paragraph of the narrative).
_CONCEPT_FINDING = {
    "CodeValue": "121071",
    "CodingSchemeDesignator": "DCM",
    "CodeMeaning": "Finding",
}


def _require_pydicom():
    """Lazy import so the web server starts even without pydicom installed.

    DICOM SR is an opt-in feature; we don't want to force pydicom on every
    deployment. Callers of :func:`save_dicom_sr_report` get a clean no-op
    with a warning if pydicom is missing.
    """
    try:
        from pydicom import Dataset
        from pydicom.dataset import FileDataset, FileMetaDataset
        from pydicom.uid import ExplicitVRLittleEndian, generate_uid
        return Dataset, FileDataset, FileMetaDataset, ExplicitVRLittleEndian, generate_uid
    except ImportError:
        return None


def _parse_dicom_date(value: str) -> str:
    """Convert a date string in various formats to DICOM DA (YYYYMMDD)."""
    if not value:
        return ""
    s = str(value).strip()
    # Already DICOM DA?
    if len(s) == 8 and s.isdigit():
        return s
    # DD/MM/YYYY or DD-MM-YYYY
    m = re.match(r"^(\d{1,2})[/\-.](\d{1,2})[/\-.](\d{4})$", s)
    if m:
        return f"{m.group(3)}{int(m.group(2)):02d}{int(m.group(1)):02d}"
    # YYYY-MM-DD
    m = re.match(r"^(\d{4})[/\-.](\d{1,2})[/\-.](\d{1,2})$", s)
    if m:
        return f"{m.group(1)}{int(m.group(2)):02d}{int(m.group(3)):02d}"
    return ""


def _format_person_name(name: str) -> str:
    """Convert 'Last, First Middle' to DICOM PN format 'Last^First^Middle'."""
    if not name:
        return ""
    parts = [p.strip() for p in name.split(",", 1)]
    if len(parts) == 2:
        family = parts[0]
        given_parts = parts[1].split()
        given = given_parts[0] if given_parts else ""
        middle = " ".join(given_parts[1:]) if len(given_parts) > 1 else ""
        return f"{family}^{given}^{middle}".rstrip("^")
    # Single-token fallback
    return name.strip()


def _make_text_content_item(Dataset, text: str) -> "Dataset":
    """Build a TEXT ValueType content item (one paragraph of the report)."""
    item = Dataset()
    item.RelationshipType = "CONTAINS"
    item.ValueType = "TEXT"

    concept = Dataset()
    concept.CodeValue = _CONCEPT_FINDING["CodeValue"]
    concept.CodingSchemeDesignator = _CONCEPT_FINDING["CodingSchemeDesignator"]
    concept.CodeMeaning = _CONCEPT_FINDING["CodeMeaning"]
    item.ConceptNameCodeSequence = [concept]

    item.TextValue = text
    return item


def build_dicom_sr(
    report_text: str,
    patient_context: Optional[dict] = None,
    template_name: Optional[str] = None,
    institution_name: str = "VOXRAD",
):
    """Build a Basic Text SR Dataset ready for writing. Returns a FileDataset.

    Raises ``RuntimeError`` if pydicom is not installed.
    """
    deps = _require_pydicom()
    if deps is None:
        raise RuntimeError("pydicom not installed — cannot build DICOM SR")
    Dataset, FileDataset, FileMetaDataset, ExplicitVRLittleEndian, generate_uid = deps

    ctx = patient_context or {}
    now = datetime.now()
    dicom_date = now.strftime("%Y%m%d")
    dicom_time = now.strftime("%H%M%S")

    # File Meta
    file_meta = FileMetaDataset()
    file_meta.MediaStorageSOPClassUID = _SOP_CLASS_BASIC_TEXT_SR
    file_meta.MediaStorageSOPInstanceUID = generate_uid()
    file_meta.TransferSyntaxUID = ExplicitVRLittleEndian
    file_meta.ImplementationClassUID = generate_uid(prefix="1.2.826.0.1.3680043.10.1354.")
    file_meta.ImplementationVersionName = "VOXRAD"

    ds = FileDataset(
        filename_or_obj=None,
        dataset={},
        file_meta=file_meta,
        preamble=b"\0" * 128,
    )

    # ─── Patient module ─────────────────────────────────────────────────
    ds.PatientName = _format_person_name(ctx.get("patient_name", ""))
    ds.PatientID = str(ctx.get("patient_id", ""))
    ds.PatientBirthDate = _parse_dicom_date(ctx.get("patient_dob", ""))
    ds.PatientSex = ctx.get("patient_sex", "") or ""

    # ─── General Study module ────────────────────────────────────────────
    # StudyInstanceUID should match the imaging study this report is for.
    # If the caller didn't supply one, generate a new one — PACS will either
    # match on AccessionNumber + PatientID (most do) or drop the SR.
    ds.StudyInstanceUID = ctx.get("study_instance_uid") or generate_uid()
    ds.AccessionNumber = str(ctx.get("accession", ""))
    ds.StudyDate = _parse_dicom_date(ctx.get("study_date", "")) or dicom_date
    ds.StudyTime = ctx.get("study_time", "") or dicom_time
    ds.ReferringPhysicianName = _format_person_name(ctx.get("referring_physician", ""))
    ds.StudyID = ctx.get("study_id", "") or ""
    ds.StudyDescription = ctx.get("procedure", "") or (template_name or "")

    # ─── SR Document Series module ───────────────────────────────────────
    ds.Modality = "SR"
    ds.SeriesInstanceUID = generate_uid()
    ds.SeriesNumber = 1
    ds.SeriesDescription = "VoxRad Radiology Report"

    # ─── General Equipment + SOP Common ──────────────────────────────────
    ds.Manufacturer = "VoxRad"
    ds.InstitutionName = institution_name
    ds.SOPClassUID = _SOP_CLASS_BASIC_TEXT_SR
    ds.SOPInstanceUID = file_meta.MediaStorageSOPInstanceUID

    # ─── SR Document General module ──────────────────────────────────────
    ds.InstanceNumber = 1
    ds.ContentDate = dicom_date
    ds.ContentTime = dicom_time
    ds.CompletionFlag = "COMPLETE"
    ds.VerificationFlag = "UNVERIFIED"
    ds.PreliminaryFlag = "FINAL"

    # Radiologist name: exposed via OperatorsName. We deliberately skip the
    # SR Observer Context sequence (TID 1002) — it requires a tree of coded
    # content items rather than a flat sequence, and PACS don't enforce it
    # for Basic Text SR. The narrative is the source of truth.
    radiologist = ctx.get("radiologist", "")
    if radiologist:
        ds.OperatorsName = _format_person_name(radiologist)

    # ─── SR Document Content module (the report body) ────────────────────
    # Root: CONTAINER, concept = "Diagnostic imaging report" (LOINC 18748-4)
    ds.ValueType = "CONTAINER"

    root_concept = Dataset()
    root_concept.CodeValue = _CONCEPT_RADIOLOGY_REPORT["CodeValue"]
    root_concept.CodingSchemeDesignator = _CONCEPT_RADIOLOGY_REPORT["CodingSchemeDesignator"]
    root_concept.CodeMeaning = _CONCEPT_RADIOLOGY_REPORT["CodeMeaning"]
    ds.ConceptNameCodeSequence = [root_concept]

    ds.ContinuityOfContent = "SEPARATE"

    # One TEXT content item per paragraph — keeps individual items under
    # the 1024-char TextValue recommendation and mirrors how we split HL7 OBX.
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", report_text.strip()) if p.strip()]
    if not paragraphs:
        paragraphs = [report_text.strip() or "(empty report)"]

    ds.ContentSequence = [_make_text_content_item(Dataset, p) for p in paragraphs]

    return ds


def save_dicom_sr_report(
    report_text: str,
    outbox_path: str,
    patient_context: Optional[dict] = None,
    template_name: Optional[str] = None,
    institution_name: str = "VOXRAD",
) -> Optional[str]:
    """Build a Basic Text SR and write it to the outbox directory.

    Filename: ``VOXRAD_SR_{accession}_{timestamp}.dcm`` (accession replaced
    with ``NOACC`` when not provided). Returns the saved path, or ``None``
    when disabled / skipped / pydicom unavailable.
    """
    try:
        if not outbox_path:
            logger.warning("DICOM SR outbox path not configured; skipping export.")
            return None

        deps = _require_pydicom()
        if deps is None:
            logger.warning(
                "pydicom not installed — DICOM SR export skipped. "
                "Add 'pydicom' to your server deps to enable."
            )
            return None

        os.makedirs(outbox_path, exist_ok=True)

        ds = build_dicom_sr(
            report_text=report_text,
            patient_context=patient_context,
            template_name=template_name,
            institution_name=institution_name,
        )

        accession = (patient_context or {}).get("accession") or "NOACC"
        safe_acc = re.sub(r"[^A-Za-z0-9_-]", "_", str(accession))[:40]
        filename = f"VOXRAD_SR_{safe_acc}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.dcm"
        filepath = os.path.join(outbox_path, filename)

        # Explicit LE + proper DICOM file format. The TransferSyntaxUID on
        # file_meta tells save_as the encoding; enforce_file_format writes the
        # 128-byte preamble + DICM magic + file meta group instead of a raw
        # dataset.
        try:
            ds.save_as(filepath, enforce_file_format=True)
        except TypeError:
            # pydicom < 3.0 fallback
            ds.save_as(filepath, write_like_original=False)
        logger.info("DICOM Basic Text SR saved: %s", filepath)
        return filepath

    except Exception as e:
        logger.error("DICOM SR export failed: %s", e)
        return None
