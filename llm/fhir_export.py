"""FHIR R4 DiagnosticReport export for VoxRad.

Converts a completed radiology report (Markdown text) into a FHIR R4
DiagnosticReport resource and saves it to the working directory as
{timestamp}_report.json.

Usage
-----
    from llm.fhir_export import save_fhir_report
    path = save_fhir_report(report_text, template_name="CT_Chest.txt")
"""

import base64
import configparser
import json
import os
import uuid
from datetime import datetime, timezone
from typing import Optional


def _get_working_directory() -> str:
    """Return the configured working directory (mirrors format.py logic)."""
    if os.name == "nt":
        config_dir = os.path.join(os.environ["APPDATA"], "VOXRAD")
    else:
        config_dir = os.path.join(os.path.expanduser("~"), ".voxrad")

    config_path = os.path.join(config_dir, "settings.ini")
    if os.path.exists(config_path):
        parser = configparser.ConfigParser()
        parser.read(config_path)
        if "DEFAULT" in parser and "WorkingDirectory" in parser["DEFAULT"]:
            return parser["DEFAULT"]["WorkingDirectory"]
    return config_dir


def report_to_fhir(
    report_text: str,
    template_name: Optional[str] = None,
    patient_id: Optional[str] = None,
    accession: Optional[str] = None,
    radiologist: Optional[str] = None,
) -> dict:
    """Build a FHIR R4 DiagnosticReport dict from a completed report.

    Parameters
    ----------
    report_text   : Formatted Markdown report text.
    template_name : Template filename used (e.g. ``CT_Chest.txt``).
    patient_id    : Optional patient identifier (omitted if None).
    accession     : Optional accession number (omitted if None).
    radiologist   : Optional reporting radiologist name (omitted if None).

    Returns
    -------
    dict  FHIR R4 DiagnosticReport as a plain Python dict (JSON-serialisable).
    """
    exam_display = "Radiology Report"
    if template_name:
        exam_display = (
            template_name.replace("_", " ").replace(".txt", "").replace(".md", "")
        )

    resource: dict = {
        "resourceType": "DiagnosticReport",
        "id": str(uuid.uuid4()),
        "status": "final",
        "category": [
            {
                "coding": [
                    {
                        "system": "http://terminology.hl7.org/CodeSystem/v2-0074",
                        "code": "RAD",
                        "display": "Radiology",
                    }
                ]
            }
        ],
        "code": {
            "coding": [
                {
                    "system": "http://loinc.org",
                    "code": "18748-4",
                    "display": "Diagnostic imaging study",
                }
            ],
            "text": exam_display,
        },
        "issued": datetime.now(timezone.utc).isoformat(),
        "presentedForm": [
            {
                "contentType": "text/plain",
                "data": base64.b64encode(report_text.encode("utf-8")).decode("ascii"),
                "title": exam_display,
            }
        ],
    }

    if patient_id:
        resource["subject"] = {
            "reference": f"Patient/{patient_id}",
            "display": patient_id,
        }

    if accession:
        resource["identifier"] = [
            {"system": "urn:voxrad:accession", "value": accession}
        ]

    if radiologist:
        resource["performer"] = [{"display": radiologist}]

    return resource


def save_fhir_report(
    report_text: str,
    template_name: Optional[str] = None,
    patient_id: Optional[str] = None,
    accession: Optional[str] = None,
    radiologist: Optional[str] = None,
) -> Optional[str]:
    """Build a FHIR R4 DiagnosticReport and save it to the working directory.

    The file is saved as ``{working_dir}/{YYYYMMDD_HHMMSS}_report.json``.

    Returns the saved file path, or ``None`` if saving failed.
    """
    try:
        resource = report_to_fhir(
            report_text=report_text,
            template_name=template_name,
            patient_id=patient_id,
            accession=accession,
            radiologist=radiologist,
        )

        working_dir = _get_working_directory()
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filepath = os.path.join(working_dir, f"{timestamp}_report.json")

        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(resource, f, indent=2, ensure_ascii=False)

        print(f"FHIR R4 report saved: {filepath}")
        return filepath

    except Exception as e:
        print(f"FHIR export failed: {e}")
        return None
