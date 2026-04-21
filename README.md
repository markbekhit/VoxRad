<p align="center">
  <img src="images/voxrad_logo.jpg" alt="VoxRad Logo" />
</p>

<div align="center">

[![Python Badge](https://img.shields.io/badge/Python-3776AB?logo=python&logoColor=fff&style=for-the-badge)](#)
[![FastAPI](https://img.shields.io/badge/FastAPI-009688?logo=fastapi&logoColor=fff&style=for-the-badge)](#)
[![Fly.io](https://img.shields.io/badge/Fly.io-7C3AED?logo=flydotio&logoColor=fff&style=for-the-badge)](#)
[![License](https://flat.badgen.net/badge/license/GPLv3/green?icon=github)](LICENSE)
[![Python Version](https://flat.badgen.net/badge/python/3.11%20|%203.12/blue?icon=github)](#)

</div>

# 🚀 VoxRad

VoxRad is an AI-assisted voice reporting system for radiologists. Dictate a
study, get back a structured, style-consistent report ready to paste into
your RIS — or, where integrations are available, delivered automatically.

The project is web-first: deploy a single container (or push to Fly.io) and
your radiologists get a browser-based workstation with streaming speech-
to-text, LLM report formatting, patient-context awareness, a DICOM/HL7
worklist, and standards-based export back to the RIS. A legacy desktop
(Tkinter) build is still in the tree for offline use.

> **Note on lineage** — this codebase began as a fork of Ankush Ankush's
> original VoxRad desktop app (see [citation below](#-cite)) and has since
> evolved into an independent, actively-developed web platform. It is
> unrelated to any "VoxRad2" effort.

## ✨ What's in here

### Radiologist workstation (web)
- 🎤 **Streaming STT** — Deepgram Nova-2-Medical or AssemblyAI with medical
  vocabulary boost; falls back to Groq Whisper segment mode when no
  streaming key is configured
- 📝 **LLM report formatting** — OpenAI-compatible endpoint (OpenAI,
  Gemini, Groq, local Ollama / vLLM, etc.) with user-editable templates
- 🎯 **Voice refinement** — select a passage, speak corrections, regenerate
- 🗂 **Worklist panel** — modality filter chips (CT/MR/US/XR/Other),
  waiting-time labels, one-click archive
- 📋 **Smart paste** — rich / plain / markdown clipboard payloads for
  different RIS text fields, plus one-keystroke "Next Case" reset (Alt+N)
- 🎨 **Reporting style preferences** — British/American spelling, grade
  numerals, measurement units, impression format, laterality, date format
- 👤 **Patient context** — name, DOB, MRN, accession, modality, body part,
  referring physician; auto-populated from HL7 / MWL / FHIR lookup
- 🔐 **Auth** — HTTP Basic or Google / Microsoft OAuth (per-user settings
  in OAuth mode)

### Integration features
- 📤 **HL7 v2.4 ORU^R01 export** — drop final reports to a file-drop inbox
  for RIS integration engines to pick up
- 📥 **HL7 v2.4 ORM^O01 ingestion** — parse inbound orders from integration
  engines, surface them in the worklist
- 🛰️ **DICOM Modality Worklist (MWL) bridge agent** — an on-prem Python
  agent runs C-FIND against the clinic's PACS and pushes orders to the
  cloud VoxRad instance over HTTPS, avoiding the inbound-firewall problem
- 🧬 **FHIR R4 export** — `DiagnosticReport` JSON written per report
- 🔎 **FHIR RIS patient lookup** — query a FHIR server by accession to
  auto-fill patient context

### Desktop (legacy, still in tree)
- Tkinter UI, multimodal (Gemini) mode, encrypted clipboard paste
- Not at feature parity with the web app — new integration features are
  web-only

## 🏗️ Architecture

```
                    ┌── Clinic LAN ──┐             ┌── Fly.io / Docker ──┐
                    │                │             │                     │
  ┌─────────┐       │   ┌─────────┐  │             │                     │
  │ PACS /  │──MWL──┼──▶│  MWL    │──┼── HTTPS ───▶│                     │
  │  MWL    │       │   │  Bridge │  │             │      VoxRad         │
  └─────────┘       │   └─────────┘  │             │      Web App        │
                    │                │             │      (FastAPI)      │
  ┌─────────┐       │   ┌─────────┐  │             │                     │
  │   RIS   │──HL7──┼──▶│ Inbox   │  │ file drop   │                     │
  │ engine  │◀─HL7──┼───│ Outbox  │──┼─────────────│                     │
  └─────────┘       │   └─────────┘  │             │                     │
                    └────────────────┘             │        │            │
                                                   │        ▼            │
                                                   │  ┌──────────────┐   │
                                     browser ◀─────┤  │   OpenAI-    │   │
                                     (dictate)─────┤──▶│   compatible │   │
                                                   │  │   LLM        │   │
                                                   │  └──────────────┘   │
                                                   └─────────────────────┘
```

Core subsystems:

- `web/` — FastAPI app, Jinja2 templates, WebSocket streaming STT proxy
- `llm/` — report formatting, HL7 import/export, FHIR export, style prompt
- `audio/` — microphone capture + segment/stream encoding
- `agents/` — on-prem MWL bridge (stands apart from the server)
- `config/`, `utils/` — settings loader, encryption
- `templates/` — ~29 bundled radiology report templates (CT, MR, US, XR,
  mammo, bone, echo, obstetric, ophthalmology, paediatric, PET, etc.)
- `guidelines/` — BIRADS, TIRADS, PIRADS, LIRADS, Fleischner reference
- `ui/` — legacy Tkinter desktop

## 🚀 Quick start — web app

### Fly.io (recommended — always-on free tier)

```bash
flyctl auth login
flyctl apps create voxrad-yourname
flyctl volumes create voxrad_config --size 1 --region syd
flyctl volumes create voxrad_data   --size 1 --region syd

flyctl secrets set \
    VOXRAD_WEB_PASSWORD=changeme \
    VOXRAD_TRANSCRIPTION_API_KEY=gsk_... \
    VOXRAD_TEXT_API_KEY=sk-...

flyctl deploy
```

Full guide: [docs/deploy-web.md](docs/deploy-web.md).

### Docker (local / self-hosted)

```bash
docker compose up -d
# → http://localhost:8000
```

### Configuration

Everything is env-var driven; non-sensitive prefs live in `settings.ini`.
Key vars:

| Variable | Purpose |
|---|---|
| `VOXRAD_WEB_PASSWORD` | HTTP Basic password (single-user mode) |
| `VOXRAD_TRANSCRIPTION_API_KEY` | Groq / Whisper API key |
| `VOXRAD_TEXT_API_KEY` | LLM API key for report formatting |
| `DEEPGRAM_API_KEY` / `ASSEMBLYAI_API_KEY` | Streaming STT provider keys |
| `VOXRAD_STREAMING_STT_PROVIDER` | `deepgram` \| `assemblyai` \| unset |
| `VOXRAD_WORKING_DIR` | Where templates / reports / inbox live |
| `GOOGLE_CLIENT_ID` / `MICROSOFT_CLIENT_ID` / ... | OAuth mode |

## 🔌 Integration setup

### HL7 file-drop (generic RIS)

Any integration engine that can write/read HL7 v2.x files to a shared
directory works. Point the engine at VoxRad's inbox/outbox:

```bash
flyctl secrets set \
    VOXRAD_HL7_INBOX=/data/hl7_inbox \
    VOXRAD_HL7_OUTBOX=/data/hl7_outbox \
    VOXRAD_HL7_SENDING_FACILITY=VOXRAD \
    VOXRAD_HL7_RECEIVING_FACILITY=MYCLINIC
```

Inbound `ORM^O01` orders land in the worklist automatically. Outbound
`ORU^R01` reports are written when `VOXRAD_HL7_ENABLED=true`.

### DICOM MWL bridge (no integration engine required)

For clinics with a PACS/MWL broker but no HL7 integration engine, run the
on-prem bridge agent:

```bash
# Server side — set the shared secret
flyctl secrets set VOXRAD_MWL_AGENT_TOKEN=$(openssl rand -hex 32)

# Clinic side — run the bridge
pip install -r agents/requirements.txt
python agents/voxrad_mwl_agent.py \
    --mwl-host pacs.clinic.local --mwl-port 104 \
    --called-ae MWLSCP --calling-ae VOXRAD \
    --voxrad-url https://voxrad-yourname.fly.dev \
    --token $VOXRAD_AGENT_TOKEN
```

Full guide: [docs/mwl-bridge-agent.md](docs/mwl-bridge-agent.md). This
includes systemd unit, firewall / security notes, and recipes for
testing against public SCPs (`dicomserver.co.uk`, Orthanc) without a
real PACS.

### FHIR R4

```bash
flyctl secrets set \
    FHIR_BASE_URL=https://ris.example.com/fhir \
    VOXRAD_FHIR_EXPORT_ENABLED=true
```

Each finalised report emits a `DiagnosticReport` JSON to the working
directory; the "Lookup" button queries the FHIR server by accession to
pre-fill patient context.

## 🖥️ Desktop app (legacy)

The original Tkinter desktop app still works for local, offline use:

```bash
pip install -r requirements.txt
python VoxRad.py
```

It supports encrypted paste, multimodal (Gemini) mode, and the same
template library — but **does not** have the HL7 / MWL / FHIR
integration features the web app has. New work lands web-first.

## 📚 Documentation

In this repo:
- [`docs/deploy-web.md`](docs/deploy-web.md) — Fly.io / Docker deployment
- [`docs/mwl-bridge-agent.md`](docs/mwl-bridge-agent.md) — MWL bridge setup
- [`docs/local-whisper-setup.md`](docs/local-whisper-setup.md) — self-hosted STT
- [`docs/FFmpeg.md`](docs/FFmpeg.md) — audio pipeline notes
- [`CLAUDE.md`](CLAUDE.md) — project instructions for AI-assisted development

Original desktop app's GitBook: https://voxrad.gitbook.io/voxrad
(historical reference — predates the web work)

## 🛠️ Development

```bash
# Clone + install
git clone https://github.com/markbekhit/VoxRad.git
cd VoxRad
pip install -r requirements-web.txt

# Dev run with mock APIs
VOXRAD_MOCK_MODE=1 VOXRAD_WEB_PASSWORD=dev \
    python VoxRad.py --web --port 8000

# → http://localhost:8000   (user: voxrad, pass: dev)
```

`VOXRAD_MOCK_MODE=1` stubs out transcription + LLM calls with canned
responses — useful for UI work and CI without burning API credits.

## 🤝 Contributing

See [`contributing.md`](contributing.md). Bug reports and feature
requests via GitHub issues; please include logs and the deployment mode
(desktop / Fly.io / Docker).

## 📜 License

GPLv3 — see [LICENSE](LICENSE). Third-party licences for bundled
binaries (e.g. FFmpeg in legacy desktop builds) are noted in
[`docs/FFmpeg.md`](docs/FFmpeg.md).

## 🚨 Disclaimer

VoxRad is software for *drafting* radiology reports. It does not replace
professional medical judgement, is not a medical device, and has not been
certified for clinical use in any jurisdiction. Users are responsible for:

- Verifying every generated report against the imaging
- Compliance with local regulations for handling patient data (HIPAA,
  GDPR, Australian Privacy Act, etc.) — for sensitive data, self-host
  both the transcription and LLM endpoints
- Reviewing and agreeing to the terms of service of any third-party API
  keys configured (OpenAI, Groq, Deepgram, AssemblyAI, Gemini, etc.)

## 🔖 Cite

The upstream desktop project is published in *Clinical Imaging*:

```bibtex
@article{ankush_voxrad_2025,
    title     = {{VoxRad}: {Building} an open-source locally-hosted radiology reporting system},
    volume    = {119},
    issn      = {0899-7071, 1873-4499},
    shorttitle = {{VoxRad}},
    url       = {https://www.clinicalimaging.org/article/S0899-7071(25)00014-2/abstract},
    doi       = {10.1016/j.clinimag.2025.110414},
    journal   = {Clinical Imaging},
    author    = {Ankush, Ankush},
    month     = mar,
    year      = {2025},
    pmid      = {39884167},
}
```

Ankush A. (2025). VoxRad: Building an open-source locally-hosted
radiology reporting system. *Clinical Imaging*, 119, 110414.
[doi:10.1016/j.clinimag.2025.110414](https://doi.org/10.1016/j.clinimag.2025.110414)
· PMID [39884167](https://pubmed.ncbi.nlm.nih.gov/39884167/)
