#!/usr/bin/env python3
"""VoxRad MWL bridge agent.

Runs on-prem inside the clinic's firewall, polls their DICOM Modality
Worklist SCP via C-FIND, and POSTs matched orders to VoxRad's
/api/worklist/push endpoint.

Why a bridge?
-------------
VoxRad is hosted on Fly.io; clinic PACS/MWL brokers live on private
networks and will not accept inbound connections from the public
internet. Inverting the direction — have a small on-prem script make
outbound HTTPS calls — avoids the firewall problem entirely and keeps
VoxRad's footprint inside the clinic minimal.

Required Python packages
------------------------
    pip install pynetdicom pydicom requests

Quick start
-----------
    export VOXRAD_URL=https://voxrad.example.com
    export VOXRAD_AGENT_TOKEN=<same token as server VOXRAD_MWL_AGENT_TOKEN>
    export MWL_HOST=pacs.clinic.local
    export MWL_PORT=104
    export MWL_CALLED_AE=MWLSCP         # the SCP's AE title
    export MWL_CALLING_AE=VOXRAD        # this agent's AE title
    python agents/voxrad_mwl_agent.py

    # or all at once on the command line:
    python agents/voxrad_mwl_agent.py \\
        --mwl-host pacs.clinic.local --mwl-port 104 \\
        --called-ae MWLSCP --calling-ae VOXRAD \\
        --voxrad-url https://voxrad.example.com \\
        --token $VOXRAD_AGENT_TOKEN \\
        --interval 60 --modalities CT,MR,US,XR

Testing against a public SCP (no real PACS needed)
--------------------------------------------------
    # Orthanc has DICOMweb/MWL enabled when configured; dicomserver.co.uk
    # offers a public C-FIND test server.
    python agents/voxrad_mwl_agent.py \\
        --mwl-host www.dicomserver.co.uk --mwl-port 104 \\
        --called-ae DCMQRSCP --dry-run
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from typing import Optional

logger = logging.getLogger("voxrad.mwl_agent")


# ─── Lazy imports so --help works without pynetdicom installed ──────────────
def _require_pynetdicom():
    try:
        from pynetdicom import AE, debug_logger  # noqa: F401
        from pynetdicom.sop_class import ModalityWorklistInformationFind
        from pydicom.dataset import Dataset
        return AE, ModalityWorklistInformationFind, Dataset
    except ImportError as e:
        sys.stderr.write(
            f"ERROR: pynetdicom/pydicom not installed ({e}).\n"
            "Install with: pip install pynetdicom pydicom requests\n"
        )
        sys.exit(2)


def _require_requests():
    try:
        import requests
        return requests
    except ImportError:
        sys.stderr.write("ERROR: requests not installed. pip install requests\n")
        sys.exit(2)


# ─── DICOM → VoxRad field mapping ───────────────────────────────────────────
def _pn_to_string(pn) -> str:
    """Convert a DICOM PersonName to 'Last, First Middle'."""
    if pn is None:
        return ""
    s = str(pn).strip()
    # PN format: Family^Given^Middle^Prefix^Suffix
    parts = s.split("^")
    family = parts[0].strip() if len(parts) > 0 else ""
    given  = parts[1].strip() if len(parts) > 1 else ""
    middle = parts[2].strip() if len(parts) > 2 else ""
    if family and (given or middle):
        tail = " ".join(p for p in (given, middle) if p)
        return f"{family}, {tail}".strip()
    return family or s


def _da_to_iso(da) -> str:
    """DICOM DA (YYYYMMDD) → DD/MM/YYYY."""
    s = str(da or "").strip()
    if len(s) == 8 and s.isdigit():
        return f"{s[6:8]}/{s[4:6]}/{s[0:4]}"
    return s


def _dt_to_iso(dt) -> str:
    """DICOM DT (YYYYMMDDHHMMSS[.FFFFFF][&ZZXX]) → YYYY-MM-DD HH:MM."""
    s = str(dt or "").strip()
    if len(s) >= 12 and s[:12].isdigit():
        return f"{s[0:4]}-{s[4:6]}-{s[6:8]} {s[8:10]}:{s[10:12]}"
    if len(s) == 8 and s.isdigit():
        return f"{s[0:4]}-{s[4:6]}-{s[6:8]}"
    return s


def _mwl_dataset_to_order(ds) -> dict:
    """Map a returned MWL C-FIND dataset to VoxRad's order dict shape."""
    out: dict = {}

    if getattr(ds, "PatientName", None):
        out["patient_name"] = _pn_to_string(ds.PatientName)
    if getattr(ds, "PatientID", None):
        out["patient_id"] = str(ds.PatientID).strip()
    if getattr(ds, "PatientBirthDate", None):
        out["patient_dob"] = _da_to_iso(ds.PatientBirthDate)
    if getattr(ds, "AccessionNumber", None):
        out["accession"] = str(ds.AccessionNumber).strip()
    if getattr(ds, "ReferringPhysicianName", None):
        out["referring_physician"] = _pn_to_string(ds.ReferringPhysicianName)

    # Scheduled procedure step (nested) — use first item
    sps_seq = getattr(ds, "ScheduledProcedureStepSequence", None)
    if sps_seq and len(sps_seq) > 0:
        sps = sps_seq[0]
        if getattr(sps, "Modality", None):
            out["modality"] = str(sps.Modality).strip().upper()
        if getattr(sps, "ScheduledProcedureStepDescription", None):
            out["procedure"] = str(sps.ScheduledProcedureStepDescription).strip()
        start_date = getattr(sps, "ScheduledProcedureStepStartDate", None)
        start_time = getattr(sps, "ScheduledProcedureStepStartTime", None)
        if start_date:
            combined = f"{start_date}{start_time or ''}"
            out["scheduled_datetime"] = _dt_to_iso(combined)

    # RequestedProcedureDescription is often the human-readable body part
    if getattr(ds, "RequestedProcedureDescription", None):
        desc = str(ds.RequestedProcedureDescription).strip()
        out.setdefault("procedure", desc)
        # Best-effort body-part from the procedure description's trailing words
        out.setdefault("body_part", desc)

    return out


def _build_cfind_identifier(Dataset, modality: str = "",
                            scheduled_ae: Optional[str] = None) -> "Dataset":
    """Build the minimal C-FIND identifier for an MWL query.

    `modality` filters to a single modality when non-empty. Multi-modality
    filtering is handled by the caller issuing one query per modality —
    many PACS implementations do not honour multi-valued CS queries.
    """
    ds = Dataset()
    # Patient-level (return keys)
    ds.PatientName = ""
    ds.PatientID = ""
    ds.PatientBirthDate = ""
    ds.PatientSex = ""
    ds.AccessionNumber = ""
    ds.ReferringPhysicianName = ""
    ds.RequestedProcedureDescription = ""

    # Scheduled Procedure Step — required sequence for MWL
    sps = Dataset()
    sps.Modality = modality
    sps.ScheduledStationAETitle = scheduled_ae or ""
    sps.ScheduledProcedureStepStartDate = ""
    sps.ScheduledProcedureStepStartTime = ""
    sps.ScheduledPerformingPhysicianName = ""
    sps.ScheduledProcedureStepDescription = ""
    sps.ScheduledProcedureStepID = ""
    ds.ScheduledProcedureStepSequence = [sps]

    return ds


# ─── Core loop ──────────────────────────────────────────────────────────────
def _query_one(ae, ModalityWorklistInformationFind, Dataset, args,
               modality: str) -> list[dict]:
    """Open an association, run a single C-FIND, return parsed orders."""
    identifier = _build_cfind_identifier(
        Dataset,
        modality=modality,
        scheduled_ae=args.scheduled_ae or None,
    )
    assoc = ae.associate(args.mwl_host, args.mwl_port, ae_title=args.called_ae)
    if not assoc.is_established:
        logger.error("Association rejected by %s (modality=%s)",
                     args.mwl_host, modality or "ANY")
        return []
    orders: list[dict] = []
    try:
        responses = assoc.send_c_find(identifier, ModalityWorklistInformationFind)
        for status, ds in responses:
            if not status:
                continue
            # Pending status = 0xFF00 / 0xFF01 means "more to come"
            if status.Status in (0xFF00, 0xFF01) and ds is not None:
                order = _mwl_dataset_to_order(ds)
                if order:
                    orders.append(order)
    finally:
        assoc.release()
    return orders


def run_once(args) -> dict:
    """Query the MWL SCP once and push results to VoxRad. Returns stats dict."""
    AE, ModalityWorklistInformationFind, Dataset = _require_pynetdicom()

    ae = AE(ae_title=args.calling_ae)
    ae.add_requested_context(ModalityWorklistInformationFind)

    logger.info("Connecting to %s:%d (called AE=%s, calling AE=%s)",
                args.mwl_host, args.mwl_port, args.called_ae, args.calling_ae)

    modalities = [m.strip().upper() for m in args.modalities.split(",") if m.strip()] \
                 if args.modalities else [""]

    # Dedupe: a PACS can match the same order under multiple modality queries
    # (e.g. dual-modality studies). Key on accession (preferred) or falls back
    # to patient_id + scheduled_datetime.
    seen: set[str] = set()
    orders: list[dict] = []
    for mod in modalities:
        batch = _query_one(ae, ModalityWorklistInformationFind, Dataset, args, mod)
        for o in batch:
            key = o.get("accession") or f"{o.get('patient_id','')}|{o.get('scheduled_datetime','')}"
            if key in seen:
                continue
            seen.add(key)
            orders.append(o)
        logger.info("C-FIND [modality=%s] returned %d orders", mod or "ANY", len(batch))

    logger.info("Total unique orders: %d", len(orders))

    if args.dry_run:
        for i, o in enumerate(orders, 1):
            logger.info("  [%d] %s", i, o)
        return {"ok": True, "orders": orders, "pushed": 0, "dry_run": True}

    if not orders:
        return {"ok": True, "orders": [], "pushed": 0}

    return _push_to_voxrad(args, orders)


def _push_to_voxrad(args, orders: list[dict]) -> dict:
    """POST the orders batch to VoxRad."""
    requests = _require_requests()
    url = args.voxrad_url.rstrip("/") + "/api/worklist/push"
    headers = {
        "Content-Type": "application/json",
        "X-VoxRad-Agent-Token": args.token,
    }
    payload = {"orders": orders}
    try:
        resp = requests.post(url, json=payload, headers=headers, timeout=args.http_timeout)
    except requests.exceptions.RequestException as e:
        logger.error("Push failed: %s", e)
        return {"ok": False, "error": str(e)}
    if resp.status_code != 200:
        logger.error("VoxRad returned %d: %s", resp.status_code, resp.text[:200])
        return {"ok": False, "status": resp.status_code, "body": resp.text[:200]}
    body = resp.json()
    logger.info("Pushed %d orders (written=%s skipped=%s)",
                len(orders), body.get("written"), body.get("skipped"))
    return {"ok": True, "orders": orders, "pushed": body.get("written", 0), "response": body}


def run_loop(args) -> None:
    logger.info("Starting MWL bridge loop (interval=%ds)", args.interval)
    while True:
        try:
            run_once(args)
        except KeyboardInterrupt:
            logger.info("Interrupted — exiting.")
            return
        except Exception as e:
            logger.exception("Unhandled error during poll: %s", e)
        time.sleep(args.interval)


# ─── CLI ────────────────────────────────────────────────────────────────────
def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default)


def _parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="VoxRad MWL bridge agent — polls a DICOM Modality Worklist SCP and pushes orders to VoxRad.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--mwl-host",   default=_env("MWL_HOST"),
                   help="MWL SCP hostname/IP (env: MWL_HOST)")
    p.add_argument("--mwl-port",   type=int, default=int(_env("MWL_PORT", "104") or 104),
                   help="MWL SCP port (env: MWL_PORT, default 104)")
    p.add_argument("--called-ae",  default=_env("MWL_CALLED_AE", "MWLSCP"),
                   help="SCP's AE title (env: MWL_CALLED_AE)")
    p.add_argument("--calling-ae", default=_env("MWL_CALLING_AE", "VOXRAD"),
                   help="This agent's AE title (env: MWL_CALLING_AE)")
    p.add_argument("--scheduled-ae", default=_env("MWL_SCHEDULED_STATION_AE", ""),
                   help="Filter by scheduled station AE title (optional)")
    p.add_argument("--modalities", default=_env("MWL_MODALITIES", ""),
                   help="Comma-separated modality filter (e.g. CT,MR,US,XR)")
    p.add_argument("--voxrad-url", default=_env("VOXRAD_URL"),
                   help="VoxRad base URL (env: VOXRAD_URL)")
    p.add_argument("--token",      default=_env("VOXRAD_AGENT_TOKEN"),
                   help="Shared-secret agent token (env: VOXRAD_AGENT_TOKEN)")
    p.add_argument("--interval",   type=int, default=int(_env("MWL_POLL_INTERVAL", "60") or 60),
                   help="Poll interval in seconds (default 60)")
    p.add_argument("--http-timeout", type=int, default=30,
                   help="HTTP timeout for the VoxRad push (default 30s)")
    p.add_argument("--once",     action="store_true",
                   help="Run a single poll and exit")
    p.add_argument("--dry-run",  action="store_true",
                   help="Query MWL and print results; do not push to VoxRad")
    p.add_argument("--verbose",  "-v", action="store_true",
                   help="Enable DICOM protocol debug logging")
    args = p.parse_args(argv)

    # Validation
    if not args.mwl_host:
        p.error("--mwl-host (or MWL_HOST) is required")
    if not args.dry_run:
        if not args.voxrad_url:
            p.error("--voxrad-url (or VOXRAD_URL) is required unless --dry-run")
        if not args.token:
            p.error("--token (or VOXRAD_AGENT_TOKEN) is required unless --dry-run")

    return args


def main(argv: Optional[list[str]] = None) -> int:
    args = _parse_args(argv)
    logging.basicConfig(
        format="%(asctime)s %(levelname)s %(name)s — %(message)s",
        level=logging.DEBUG if args.verbose else logging.INFO,
    )
    if args.verbose:
        from pynetdicom import debug_logger
        debug_logger()

    if args.once or args.dry_run:
        result = run_once(args)
        return 0 if result.get("ok") else 1
    run_loop(args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
