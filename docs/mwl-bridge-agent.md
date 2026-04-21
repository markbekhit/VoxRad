# VoxRad MWL Bridge Agent

This doc covers setting up the DICOM Modality Worklist (MWL) integration for
clinics running VoxRad against the cloud-hosted web app.

## Why a bridge?

VoxRad's web app is hosted on Fly.io. A clinic's PACS / MWL broker lives on
their private LAN and will not accept inbound connections from the public
internet. The bridge inverts the direction: it runs on-prem, makes the
DICOM association **outbound** to the local MWL SCP, and POSTs the parsed
orders to VoxRad over HTTPS.

```
┌───────────── Clinic LAN ─────────────┐         ┌──── Fly.io ────┐
│                                      │         │                │
│   PACS / RIS ◀── C-FIND ──┐          │         │                │
│                           │          │  HTTPS  │                │
│                  ┌────────┴───────┐  │   POST  │   VoxRad web   │
│                  │  Bridge agent  │──┼─────────▶   /api/worklist│
│                  │ (Python script)│  │  +token │    /push       │
│                  └────────────────┘  │         │                │
└──────────────────────────────────────┘         └────────────────┘
```

No firewall changes are needed beyond permitting the agent's outbound HTTPS.

## Server-side setup

Set the shared-secret token in VoxRad's environment before starting the app:

```bash
fly secrets set VOXRAD_MWL_AGENT_TOKEN=$(openssl rand -hex 32)
```

Confirm the HL7 inbox directory is writable (the agent's orders are stored
there as JSON so they feed the existing worklist UI):

```bash
# defaults to {save_directory}/hl7_inbox
fly secrets set VOXRAD_HL7_INBOX=/data/hl7_inbox
```

That's it on the server side — the push endpoint is only live when the
token env var is set, so there's no exposed surface otherwise.

## Bridge agent setup

### 1. Install

On a clinic machine that can reach the PACS (any Linux/Mac/Windows box will
do; a Raspberry Pi is plenty). Python 3.10+:

```bash
git clone https://github.com/markbekhit/VoxRad.git
cd VoxRad
pip install -r agents/requirements.txt
```

### 2. Configure

Either by environment variables or CLI flags. Minimum:

```bash
export VOXRAD_URL=https://voxrad.example.com
export VOXRAD_AGENT_TOKEN=<the token you set on the server>
export MWL_HOST=pacs.clinic.local
export MWL_PORT=104
export MWL_CALLED_AE=MWLSCP
export MWL_CALLING_AE=VOXRAD
```

Optional filters:

```bash
export MWL_MODALITIES=CT,MR,US,XR           # only these modalities
export MWL_SCHEDULED_STATION_AE=VOXRAD_RM1  # only orders routed to this station
export MWL_POLL_INTERVAL=60                 # seconds (default 60)
```

### 3. Test with a dry-run

```bash
python agents/voxrad_mwl_agent.py --once --dry-run --verbose
```

This runs a single C-FIND and prints the results without touching VoxRad.
Use `--verbose` to see the raw DICOM association negotiation.

### 4. Run continuously

As a systemd unit (Linux):

```ini
# /etc/systemd/system/voxrad-mwl.service
[Unit]
Description=VoxRad MWL Bridge Agent
After=network.target

[Service]
Type=simple
User=voxrad
EnvironmentFile=/etc/voxrad/mwl-agent.env
ExecStart=/usr/bin/python3 /opt/voxrad/agents/voxrad_mwl_agent.py
Restart=on-failure
RestartSec=30

[Install]
WantedBy=multi-user.target
```

Create `/etc/voxrad/mwl-agent.env`:

```
VOXRAD_URL=https://voxrad.example.com
VOXRAD_AGENT_TOKEN=...
MWL_HOST=pacs.clinic.local
MWL_PORT=104
MWL_CALLED_AE=MWLSCP
MWL_CALLING_AE=VOXRAD
MWL_POLL_INTERVAL=60
MWL_MODALITIES=CT,MR,US,XR
```

Then:

```bash
systemctl daemon-reload
systemctl enable --now voxrad-mwl.service
journalctl -u voxrad-mwl -f
```

## Testing without a real PACS

Any public MWL SCP works for integration testing:

```bash
# dicomserver.co.uk (Medical Connections' public test server)
python agents/voxrad_mwl_agent.py --once --dry-run \
    --mwl-host www.dicomserver.co.uk --mwl-port 104 \
    --called-ae DCMQRSCP

# Orthanc (self-hosted — enable the MWL plugin in orthanc.json)
python agents/voxrad_mwl_agent.py --once --dry-run \
    --mwl-host localhost --mwl-port 4242 \
    --called-ae ORTHANC
```

## How orders flow

1. Bridge runs C-FIND against the MWL SCP.
2. Each returned Scheduled Procedure Step is mapped to VoxRad's order dict:
   `{patient_name, patient_dob, patient_id, accession, modality,
   body_part, procedure, referring_physician, scheduled_datetime}`.
3. Batch POSTed to `/api/worklist/push` with
   `X-VoxRad-Agent-Token: <token>`.
4. VoxRad writes one `mwl_<accession>.json` file per order into the HL7
   inbox directory.
5. The existing worklist UI in the web app picks them up — the radiologist
   sees them alongside any HL7-sourced orders, with the same modality
   filter chips, waiting-time labels, and archive flow.

Orders are deduplicated by accession number (or patient_id + scheduled
datetime when accession is missing), so re-pushing the same order simply
overwrites the file instead of duplicating.

## Security notes

- Token auth only — anyone with the token can push orders. Rotate via
  `fly secrets set` when team members change.
- HTTPS is mandatory: all PHI (names, DOBs, patient IDs) crosses the
  public internet in the POST body. Never run with `http://` in
  `VOXRAD_URL` except against localhost for testing.
- The agent does not persist any PHI on disk. It reads from MWL, pushes,
  and forgets.
- The agent only issues C-FIND; it does not C-STORE or modify anything
  on the PACS. Safe to run against production.
