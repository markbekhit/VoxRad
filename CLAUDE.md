# VoxRad

VoxRad is a voice transcription desktop application for radiologists. It transcribes voice dictations and formats them into structured radiology reports using LLMs.

## Tech Stack

- **Language**: Python
- **UI**: Tkinter (cross-platform)
- **Audio**: sounddevice, soundfile, lameenc
- **LLM backends**: OpenAI API-compatible (OpenAI, Google Gemini)
- **Entry point**: `VoxRad.py`

## Project Structure

```
VoxRad/
├── VoxRad.py          # Entry point
├── audio/             # Voice recording and transcription
├── ui/                # Desktop UI (PyObjC)
├── llm/               # LLM integration and report formatting
├── config/            # Configuration and settings
├── utils/             # Utilities (encryption, etc.)
├── templates/         # Radiology report templates
├── guidelines/        # Medical guidelines (BIRADS, TIRADS, PIRADS, LIRADS, etc.)
└── docs/              # Documentation
```

## gstack

gstack is installed globally at `~/.claude/skills/gstack`. Use the `/browse` skill from gstack for all web browsing — never use `mcp__claude-in-chrome__*` tools.

Available skills:
- `/office-hours` — YC Office Hours: startup diagnostic + builder brainstorm
- `/plan-ceo-review` — CEO/founder plan review
- `/plan-eng-review` — Engineering plan review
- `/plan-design-review` — Design plan review
- `/design-consultation` — Design system from scratch
- `/autoplan` — Auto-review pipeline: CEO → design → eng
- `/review` — Paranoid code review
- `/ship` — One-command release with tests and PR creation
- `/land-and-deploy` — Merge → deploy → canary verify
- `/canary` — Post-deploy monitoring loop
- `/benchmark` — Performance regression detection
- `/browse` — Headless browser for QA, testing, and dogfooding
- `/qa` — Automated QA with fixes
- `/qa-only` — QA report only (no fixes)
- `/design-review` — Design audit + fix loop
- `/setup-browser-cookies` — Import cookies for authenticated browsing
- `/setup-deploy` — One-time deploy configuration
- `/retro` — Team retrospective
- `/investigate` — Systematic root-cause debugging
- `/document-release` — Auto-update docs after shipping
- `/codex` — Multi-AI second opinion via OpenAI Codex
- `/cso` — OWASP Top 10 + STRIDE security audit
- `/careful` — Warn before destructive commands
- `/freeze` — Lock edits to one directory
- `/guard` — Activate careful + freeze
- `/unfreeze` — Remove freeze
- `/gstack-upgrade` — Upgrade gstack to latest version
