#!/usr/bin/env python3
"""Minimal MWL SCP for integration testing the bridge agent.

Runs a DICOM Modality Worklist provider on localhost that answers
C-FIND queries with a fixed set of fake orders. Usage:

    python agents/tests/mwl_test_scp.py --port 11112 &
    python agents/voxrad_mwl_agent.py --once --dry-run \\
        --mwl-host localhost --mwl-port 11112 \\
        --called-ae TESTMWL --calling-ae VOXRAD
"""
from __future__ import annotations

import argparse
import logging

from pydicom.dataset import Dataset
from pynetdicom import AE, evt
from pynetdicom.sop_class import ModalityWorklistInformationFind

logger = logging.getLogger("mwl.test_scp")


def _make_order(accession: str, patient_family: str, patient_given: str,
                mrn: str, dob: str, modality: str, procedure: str,
                start_date: str = "20260421", start_time: str = "103000",
                referring: str = "Brown^Sarah",
                body_part: str = "") -> Dataset:
    ds = Dataset()
    ds.PatientName = f"{patient_family}^{patient_given}"
    ds.PatientID = mrn
    ds.PatientBirthDate = dob
    ds.PatientSex = "O"
    ds.AccessionNumber = accession
    ds.ReferringPhysicianName = referring
    ds.RequestedProcedureDescription = procedure

    sps = Dataset()
    sps.Modality = modality
    sps.ScheduledStationAETitle = "VOXRAD"
    sps.ScheduledProcedureStepStartDate = start_date
    sps.ScheduledProcedureStepStartTime = start_time
    sps.ScheduledPerformingPhysicianName = ""
    sps.ScheduledProcedureStepDescription = procedure
    sps.ScheduledProcedureStepID = f"SPS_{accession}"
    ds.ScheduledProcedureStepSequence = [sps]
    return ds


FAKE_WORKLIST = [
    _make_order("TEST0001", "Smith", "Jane", "MRN001", "19800615", "CT",
                "CT Chest w/ contrast"),
    _make_order("TEST0002", "Doe",   "John", "MRN002", "19720101", "MR",
                "MRI Brain", start_time="111500"),
    _make_order("TEST0003", "Chen",  "Wei",  "MRN003", "19951220", "US",
                "US Abdomen", start_time="113000"),
    _make_order("TEST0004", "Kumar", "Priya", "MRN004", "19650808", "XR",
                "X-ray Lumbar Spine", start_time="120000"),
]


def _matches_query(order: Dataset, query: Dataset) -> bool:
    """Return True if the order matches the query's non-empty filter fields."""
    try:
        sps_query = query.ScheduledProcedureStepSequence[0]
    except (AttributeError, IndexError):
        return True
    query_mod = getattr(sps_query, "Modality", None)
    if query_mod:
        # pydicom may deliver a multi-valued CS as a MultiValue; normalise to list.
        if hasattr(query_mod, "__iter__") and not isinstance(query_mod, str):
            allowed = [str(m) for m in query_mod]
        else:
            allowed = [s for s in str(query_mod).split("\\") if s]
        order_mod = str(order.ScheduledProcedureStepSequence[0].Modality)
        if order_mod not in allowed:
            return False
    return True


def handle_find(event):
    query = event.identifier
    logger.info("C-FIND received; responding with matching orders")
    for order in FAKE_WORKLIST:
        if _matches_query(order, query):
            yield 0xFF00, order  # Pending
    # 0x0000 = Success (end-of-results)
    yield 0x0000, None


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--port", type=int, default=11112)
    p.add_argument("--ae-title", default="TESTMWL")
    p.add_argument("--verbose", "-v", action="store_true")
    args = p.parse_args()

    logging.basicConfig(
        format="%(asctime)s %(levelname)s %(name)s — %(message)s",
        level=logging.DEBUG if args.verbose else logging.INFO,
    )

    ae = AE(ae_title=args.ae_title)
    ae.add_supported_context(ModalityWorklistInformationFind)
    handlers = [(evt.EVT_C_FIND, handle_find)]

    logger.info("MWL test SCP listening on port %d (AE=%s)", args.port, args.ae_title)
    logger.info("Serving %d fake orders", len(FAKE_WORKLIST))
    ae.start_server(("127.0.0.1", args.port), block=True, evt_handlers=handlers)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
