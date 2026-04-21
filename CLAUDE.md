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

## Deployment & infrastructure

The owner is **not a developer** and does not use the terminal. All infrastructure operations are Claude's responsibility — never ask the owner to run terminal commands.

### Fly.io

- App name: `voxrad-v-hkvq`, region: `syd` (Sydney, Australia)
- `flyctl` is installed in the Claude Code environment at `/usr/local/bin/flyctl`
- **Auth token is already saved** in `.claude/settings.local.json` as `FLY_API_TOKEN` — valid for 10 years. Claude can run `flyctl` directly in any session without asking the owner for credentials.
- Prefer `flyctl -a voxrad-v-hkvq <command>` (explicit app flag) so commands work regardless of working directory
- Volume `voxrad_data` (vol_vgn7n65eyn2eg604) is mounted at `/data` — persistent across deploys and machine replacements
- Persistent paths: `/data/users.db` (user DB), `/data/working` (templates/reports), `/data/hl7_inbox`, `/data/hl7_outbox`, `/data/sr_outbox`
- Session secret is auto-generated and persisted to `/data/session_secret.key` on first boot — users stay logged in across deploys without any manual setup
- Secrets are set via `flyctl secrets set KEY=VALUE -a voxrad-v-hkvq` — Claude does this, not the owner

### GitHub Actions CI/CD

- Deploys automatically on every push to `main` (workflow: `.github/workflows/fly-deploy.yml`)
- `FLY_API_TOKEN` is stored as a GitHub repo secret — CI can deploy without any manual steps
- The workflow: builds + pushes the Docker image, ensures `voxrad_data` volume exists, **destroys any legacy machines that lack the volume mount** (one-time migration safety), then deploys
- `fly.toml` uses `strategy = "immediate"` so a single volume is sufficient (no rolling-deploy two-machine requirement)
- To trigger a deploy: push any commit to `main`. To force a redeploy without code changes: `git commit --allow-empty -m "redeploy" && git push`

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
