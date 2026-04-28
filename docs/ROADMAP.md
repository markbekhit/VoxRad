# RadSpeed Roadmap

Updated: 2026-04-28

This document is the canonical product roadmap for RadSpeed. It is intended to
survive context resets — refer back to this file when picking up work
mid-stream.

## Market context (April 2026)

Primary launch market: **Australia + New Zealand**. US/HIPAA is not a near-term
constraint, so audit logging is still important (medico-legal + AU privacy
principles), but we are not pursuing BAA-grade compliance documentation yet.

**Competitive landscape snapshot:**

- **Rad AI** — enterprise replacement (US dominant). $68M Series C, ~1/3 of US
  health systems. Shipped own STT Dec 2025. Best-in-class follow-up tracking
  (Rad AI Continuity).
- **RADPAIR** — bootstrapped, product-led; agentic voice control of PACS
  viewport (Fovia partnership), Groq-accelerated. Open SDK strategy.
- **Scriptor (rScriptor)** — Windows desktop **overlay** that wraps PowerScribe /
  Dragon / M-Modal. <10 min setup per site. Free **rScriptor Impressions**
  copy-paste tool drives funnel. Unique NLP QA layer (laterality / gender /
  anatomy).
- **FinalScribe** — indie, ~100 users mid-2025. 10-language. Pre-PMF.

**Important AU/NZ-specific note**: most radiologists here run **PowerScribe as
a Windows desktop app**, not the web version. A Chrome-extension overlay has
limited reach. The right overlay path for our market is a **Windows desktop
companion** (window monitoring + clipboard / merge field injection, no audio
tap), in the style of Scriptor — not a browser extension.

## What RadSpeed already has (shipped on `main`)

These are confirmed in code on `main` as of this update — not aspirations.

### Radiologist workstation (web)

- **Multi-provider streaming STT** — Deepgram Nova-2 Medical, AssemblyAI
  Universal-3 Medical, Groq Whisper segment fallback. Voice editing by
  selection works including the short-utterance edge case
  (`web/stt_providers/`, `web/app.py` /ws/transcribe).
- **Voice refinement** — select a passage, speak corrections, regenerate.
- **Vocab learning loop** — repeated edits become per-user keyword boosts in
  the streaming STT call (`/vocab`, `/vocab/add`).
- **Style suggestion learning** — repeated style-pattern edits prompt the
  user to adopt the matching style preference
  (`/api/style-suggestion/apply` and friends).
- **Per-user style preferences** with the most granular control of any
  vendor in this segment: spelling (BR/AM), numerals (Roman/Arabic),
  measurement units + separators + decimal precision, laterality, impression
  style, negation phrasing, date format, paste format
  (`config/config.py`, `llm/format.py`).
- **40 bundled radiology templates + 5 guidelines** (BIRADS, TIRADS, PIRADS,
  LIRADS, Fleischner) — see `templates/` and `guidelines/`.
- **Streaming report generation** with patient context block
  (`stream_format_text`, `format_text(patient_context=...)`).
- **Smart paste** — rich / plain / markdown clipboard payloads for
  different RIS text fields, plus one-keystroke "Next Case" reset (Alt+N).
- **OAuth (Google + Microsoft)** with per-user settings persisted in SQLite.

### PACS / RIS / EHR integration (already shipped — needs partner adoption)

This is genuinely bidirectional. The framework is in place; the open work is
deployment and partner sign-on, not new code.

- **HL7 v2.4 ORU^R01 export** — drop final reports to a file-drop inbox for
  RIS integration engines to pick up; atomic writes, collision-safe filenames
  (`llm/hl7_export.py`).
- **HL7 v2.4 ORM^O01 ingestion** — parse inbound orders from integration
  engines, surface them in the worklist; malformed / oversize / mid-write
  files are quarantined (`llm/hl7_import.py`).
- **DICOM Basic Text SR export** — finalised reports written as standard SR
  (SOP Class `1.2.840.10008.5.1.4.1.1.88.11`) for PACS that ingest SR
  directly (`llm/dicom_sr_export.py`).
- **DICOM Modality Worklist (MWL) bridge agent** — on-prem Python agent runs
  C-FIND against the clinic's PACS and pushes orders to the cloud RadSpeed
  instance over HTTPS, avoiding the inbound-firewall problem
  (`agents/voxrad_mwl_agent.py`, `docs/mwl-bridge-agent.md`).
- **FHIR R4 DiagnosticReport export** per report (`llm/fhir_export.py`).
- **FHIR RIS patient lookup** — `/patient/{accession}` queries any FHIR R4
  server for ImagingStudy + Patient (`web/app.py`).
- **In-app worklist panel** — modality filter chips (CT/MR/US/XR/Other),
  waiting-time labels, one-click archive (`/api/hl7/worklist`,
  `/api/hl7/worklist/{order_id}/archive`, `/api/worklist/push`).

### Public free wedge tool (just shipped)

- **`/impressions`** — public, no sign-up. Findings → guideline-aware
  impression in <2s, auto-copy to clipboard. Modality field, optional
  Fleischner/BIRADS/LIRADS/PIRADS/TIRADS toggle, browser-stored style
  preferences. Per-IP hourly rate limit
  (`RADSPEED_IMPRESSIONS_HOURLY_LIMIT`, default 20/hr).
- **`POST /api/impressions/stream`** — public SSE endpoint backing the page.
- **`llm/impressions.py`** — purpose-built impression-only system prompt.

### Deployment

- **Fly.io** — auto-deploy on push to `main` via GitHub Actions; persistent
  volume for users.db + session secret; running at
  `https://dictation.markbekhit.com` (fly app `voxrad-v-hkvq`, region `syd`).
- **Docker** — `docker compose up -d` for self-hosted.

## Roadmap — sequenced

### Phase 0 (just landed): RadSpeed Impressions wedge tool

**Done.** Live at `/impressions` after the next deploy. Cheapest customer
acquisition mechanism in the segment. Validates demand and warms users up
for the full RadSpeed dictation workstation.

### Phase 1 (next, ~1 quarter): Audit log + sign-off + amendments

The PACS/RIS integration is shipped. The next enterprise-credible gap is
medico-legal audit posture, which AU/NZ practices will ask about even
without HIPAA driving it.

- Audit log table (SQLite extension): every dictation, edit, format,
  sign-off, amendment, archive event. Tamper-evident hash chain. Per-user
  retention policy.
- Explicit sign-off step (radiologist locks the report; any further edit
  creates an amendment). Store amendment history.
- View / export audit trail per case for medico-legal queries.

### Phase 2 (Q3-Q4): NLP QA layer (Scriptor-style differentiator)

Catch laterality / gender / anatomy / unit-drift errors before sign-off.

- Deterministic pre-pass:
  - Laterality cross-check vs ImagingStudy bodySite (we already have the
    accession lookup).
  - Gender mismatch (e.g. "uterus" in a male patient — patient data is
    available from FHIR / HL7).
  - Modality / anatomy mismatch (e.g. "cardiac chambers normal" on a knee MR).
  - Unit drift (mixing mm and cm in same lesion).
- LLM cross-check pass with a tightly scoped system prompt — flag-only,
  never silently rewrite.
- Inline highlights in the report editor; user explicitly accepts/dismisses.
- This is **the** technical moat — only Scriptor markets it among the four
  competitors.

### Phase 3 (Q4-2027 Q1): Windows desktop overlay

Reach the AU/NZ PowerScribe **desktop** install base, not just web.

- Native Windows companion (likely Tauri or Electron + native Win32 / UI
  Automation bindings).
- Window-text monitoring (mirrors Scriptor's approach — no audio tap, no
  microphone control).
- Clipboard injection into the active dictation field.
- Side panel hosts the existing RadSpeed web pipeline via a local web view.
- Pricing: per-radiologist subscription. Ride on top of practice's existing
  PowerScribe contract.

### Phase 4 (2027 H1): Critical findings tracking (Rad AI Continuity copy)

- Flag reports with significant incidentals (deterministic + LLM hybrid).
- Persist follow-up table keyed by patient + finding.
- Notification channel to ordering provider (email + in-app).
- Closure tracking: "Was the recommended follow-up actually done?"

### Phase 5 (later): Structured scoring widgets

- Convert bundled BIRADS / LIRADS / PIRADS / TIRADS / Fleischner guidelines
  from reference markdown into form-style entry blocks.
- Each block emits a guideline-correct report fragment.
- Currently the guidelines ship as files but are not actively applied — this
  closes that loop.

## Explicitly NOT doing (and why)

- **HIPAA BAA documentation** — not relevant for AU/NZ launch. Revisit if/when
  US enters scope.
- **Chrome extension overlay for PowerScribe Web** — AU/NZ market is desktop
  PowerScribe. Browser extension is the wrong bet here.
- **Agentic voice control of the PACS viewport** — RADPAIR's fight; requires
  a deep PACS partnership we don't have.
- **Multi-language support beyond English** — FinalScribe's niche, too narrow
  to anchor brand positioning.
- **Out-marketing Rad AI on follow-up tracking in the US** — they raised $68M
  for that fight. Phase 4 is "table stakes" parity for our market, not a
  US-style enterprise sales motion.

## Notes for future Claude sessions

- `main` is the deployment branch — push to main triggers fly.io auto-deploy
  to `dictation.markbekhit.com`.
- The Impressions page lives at `/impressions` (public, no auth).
- The PACS/RIS/EHR integration framework is ALREADY SHIPPED on main (HL7
  ORU/ORM, DICOM SR, MWL bridge, FHIR R4). Don't re-plan it as a future
  feature — it's deployment-and-partners work, not new code.
- The user prefers concise updates and direct technical communication.
