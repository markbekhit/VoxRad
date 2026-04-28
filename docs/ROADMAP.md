# RadSpeed Roadmap

Updated: 2026-04-28

This document is the canonical product roadmap for RadSpeed (formerly VoxRad).
It is intended to survive context resets — refer back to this file when picking
up work mid-stream.

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

## What RadSpeed already has

Confirmed strengths (from `/home/user/VoxRad` codebase audit):

- Multi-provider streaming STT: Deepgram Nova-2 Medical, AssemblyAI Universal-3
  Medical, Groq Whisper segment fallback. Voice editing by selection works
  including the short-utterance edge case (`web/app.py:2095+`,
  `web/stt_providers/`).
- Per-user style preferences with the most granular control of any vendor in
  this segment: spelling (BR/AM), numerals (Roman/Arabic), measurement units +
  separators + decimal precision, laterality, impression style, negation
  phrasing, date format, paste format. (`config/config.py`,
  `llm/format.py:280-383`)
- 27 bundled radiology templates + 6 guidelines (BIRADS x2, PIRADS, LIRADS,
  TIRADS, Fleischner) shipped in `templates/` and `guidelines/`.
- Streaming report generation with patient context block (`llm/format.py`
  `stream_format_text`, `format_text(patient_context=...)`).
- **PACS/RIS framework already in place — needs a partner, not new code**:
  - Read: `/patient/{accession}` queries any FHIR R4 server for ImagingStudy
    + Patient (`web/app.py:892-975`). Configurable via `FHIR_BASE_URL` and
    `FHIR_BEARER_TOKEN` env vars.
  - Write: FHIR R4 DiagnosticReport export per-report (`llm/fhir_export.py`).
    Currently saves to local disk; pushing to the FHIR server is a small
    additional step gated on a partner conversation.
- OAuth (Google + Microsoft) with per-user settings persisted in SQLite.
- Vocabulary suggestion + style-drift detection on user edits
  (`/api/check-edit-suggestion`), now debounced on typing pause.
- RadSpeed brand applied throughout the web UI (cyan/mint gradient palette,
  matching favicon).

## Roadmap — sequenced

### Phase 0 (now, ≤2 weeks): RadSpeed Impressions wedge tool

**Status: in progress.** This is the immediate deliverable.

**Goal**: a free, public, copy-paste tool that takes radiology findings and
returns a guideline-aware impression in <2 seconds. Modeled on Scriptor's free
rScriptor Impressions funnel.

**Why**: cheapest customer-acquisition mechanism in the segment. Validates
demand. Builds an email list before any sales motion. Reuses existing
`format.py` plumbing — minimal new code.

**Scope**:
- New page at `/impressions` (no auth required, public).
- Single findings textarea + modality dropdown + "Generate" button.
- SSE-streaming impression output, auto-copy to clipboard on completion.
- Optional toggle: "Apply guideline recommendations" (Fleischner / BIRADS /
  LIRADS / PIRADS / TIRADS based on dictated content).
- Style preferences (spelling, numerals, separators, etc.) saved in
  `localStorage` so non-logged-in users get persistent preferences.
- Simple per-IP rate limit (e.g. 20/hour) to control API cost.
- Branding consistent with main app (gradient logo, mint/cyan palette).
- Footer link: "Sign in for full RadSpeed dictation →".

**Files to create / modify**:
- `llm/impressions.py` — new module: `_IMPRESSION_SYSTEM_PROMPT`,
  `stream_impression(findings, modality, style, with_guidelines)`.
- `web/templates/impressions.html` — public page.
- `web/static/impressions.js` — SSE consumer + clipboard.
- `web/app.py` — `GET /impressions` (public), `POST /api/impressions/stream`
  (public, rate-limited).

### Phase 1 (Q2-Q3, ~1 quarter): Audit log + worklist

**Goal**: enterprise-credible foundation for AU/NZ radiology practice sales.

- Audit log table (SQLite extension): every dictation, edit, format, sign-off
  event. Tamper-evident hash chain. Per-user retention policy.
- Case worklist: pull from FHIR ImagingStudy by ordering provider / location.
  Replace the freeform-only flow with case-driven dictation. Status states:
  pending / draft / signed.
- Sign-off step (radiologist locks the report; any further edit creates an
  amendment). Store amendment history.

### Phase 2 (Q3-Q4): NLP QA layer (Scriptor-style differentiator)

**Goal**: catch laterality / gender / anatomy / unit-drift errors before sign-off.

- Deterministic pre-pass:
  - Laterality cross-check vs ImagingStudy bodySite.
  - Gender mismatch (e.g. "uterus" in a male patient).
  - Modality / anatomy mismatch (e.g. "cardiac chambers normal" on a knee MR).
  - Unit drift (mixing mm and cm in same lesion).
- LLM cross-check pass with a tightly scoped system prompt — flag-only, never
  silently rewrite.
- Inline highlights in the report editor; user explicitly accepts/dismisses.
- This is **the** technical moat — none of the four competitors market it
  beyond Scriptor.

### Phase 3 (Q4-2027 Q1): Windows desktop overlay

**Goal**: reach the AU/NZ PowerScribe **desktop** install base, not just web.

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

- Branch policy: develop on feature branches per task, push and ask user
  before merging to `main`.
- Voice edit fix landed in commit `10a2c6d` — short-utterance edits no longer
  drop on stop in streaming providers.
- Debounced edit-suggestion landed in `d5c9ef9`.
- README "ARCHIVED" banner removed; project is under active development.
- The user prefers concise updates and direct technical communication.
