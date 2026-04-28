# RadSpeed Desktop Companion

A Windows system-tray application that bridges RadSpeed cloud with PowerScribe One (or any RIS/dictation UI). Select your findings text, press a hotkey, and the formatted impression is pasted back automatically.

Built with [Tauri 2](https://tauri.app/) (Rust + WebView2).

---

## End-user install

1. Go to the [Releases](https://github.com/markbekhit/voxrad/releases) page and find the latest `desktop-v*` release.
2. Download `RadSpeed_x.x.x_x64-setup.exe` (NSIS) or `RadSpeed_x.x.x_x64_en-US.msi` (MSI — use this for group-policy/silent installs).
3. Run the installer. Windows SmartScreen may warn "Unknown publisher" — click **More info → Run anyway**. (Code signing removes this warning; see [Phase 3.1](#phase-31-code-signing) below.)
4. RadSpeed appears in the system tray (bottom-right of the taskbar).

### First run

On first launch the settings window opens automatically. Fill in:

| Field | Value |
|---|---|
| **Server URL** | `https://dictation.markbekhit.com` (or your self-hosted URL) |
| **Bearer token** | Leave blank unless your server requires one |
| **Hotkey** | Default `ctrl+i` — change if it conflicts with PowerScribe |
| **Paste mode** | `goto_impression` (recommended) or `after_selection` |

Click **Save**, then **Test connection** to verify the server is reachable.

### Daily use

1. In PowerScribe One (or any RIS), type or dictate your findings.
2. Select the findings text.
3. Press your configured hotkey (default **Ctrl+I**).
4. The desktop app:
   - Copies the selection
   - Sends it to RadSpeed cloud for LLM formatting
   - Navigates to the Impression field (using the configured jump keys)
   - Pastes the formatted impression

You can also click **Trigger now** in the settings window to fire the flow manually (useful for testing).

---

## Developer setup

### Prerequisites

- **Rust** (stable) — install from [rustup.rs](https://rustup.rs/)
- **Tauri CLI** — `cargo install tauri-cli --version "^2" --locked`
- **WebView2 runtime** — pre-installed on Windows 10/11; download from Microsoft if missing

### Run in development mode

```powershell
cd desktop
cargo tauri dev
```

This opens the settings window with hot-reload for the frontend (`src/`) and recompiles Rust on save.

### Project layout

```
desktop/
├── src/              # Frontend (plain HTML/CSS/JS — no bundler)
│   ├── index.html
│   ├── main.js
│   └── styles.css
└── src-tauri/        # Rust backend
    ├── Cargo.toml
    ├── tauri.conf.json
    ├── capabilities/
    │   └── default.json
    ├── icons/
    └── src/
        ├── main.rs       # Entry point
        ├── lib.rs        # Tauri commands + app setup
        ├── settings.rs   # Config read/write (app_config_dir/config.json)
        ├── api.rs        # HTTP client (reqwest) — /api/impressions/text, /health
        ├── hotkey.rs     # Global shortcut + impressions flow orchestration
        ├── keyboard.rs   # Clipboard capture + key injection (enigo, arboard)
        └── tray.rs       # System-tray icon + menu
```

### Tauri commands (IPC)

| Command | What it does |
|---|---|
| `cmd_get_settings` | Returns current config.json contents |
| `cmd_save_settings` | Writes config.json, re-registers hotkey |
| `cmd_test_api` | GET /health on the configured server |
| `cmd_hide_settings` | Hides the settings window |
| `cmd_trigger_now` | Fires the impressions flow immediately |

---

## Releasing a new version

### Bump the version

Edit `src-tauri/tauri.conf.json` → `version` field, and `src-tauri/Cargo.toml` → `[package] version`.

### Push a tag

```bash
git tag desktop-v0.2.0
git push origin desktop-v0.2.0
```

GitHub Actions (`.github/workflows/desktop-release.yml`) will:
1. Build the Rust release binary on `windows-latest`
2. Bundle NSIS and MSI installers via `cargo tauri build`
3. Upload installers to a new GitHub Release with auto-generated release notes

You can also trigger a build without a tag via **Actions → Desktop release → Run workflow** (useful for testing the pipeline).

---

## Phase 3.1: Code signing

Without a code-signing certificate, Windows SmartScreen shows "Unknown publisher". To remove the warning:

### Generate a signing keypair

```powershell
cargo tauri signer generate -w tauri.key
```

This writes `tauri.key` (private, keep secret) and `tauri.key.pub`.

### Add secrets to GitHub

Go to **Settings → Secrets and variables → Actions** and add:

| Secret name | Value |
|---|---|
| `TAURI_SIGNING_PRIVATE_KEY` | Full contents of `tauri.key` |
| `TAURI_SIGNING_PRIVATE_KEY_PASSWORD` | Password you chose (or blank) |

The release workflow already passes these to `cargo tauri build` via environment variables — no workflow changes needed.

### EV certificate (optional, removes SmartScreen entirely)

A self-signed key above only enables Tauri's update signature verification. To fully suppress SmartScreen you need a code-signing certificate from a CA (DigiCert, Sectigo, etc.). Add it to the workflow:

```yaml
- name: Sign installers
  run: |
    signtool sign /fd SHA256 /tr http://timestamp.digicert.com /td SHA256 `
      /f cert.pfx /p $env:CERT_PASSWORD release-artifacts/*.exe
  env:
    CERT_PASSWORD: ${{ secrets.CERT_PASSWORD }}
```

Store the `.pfx` as a base64 GitHub Secret and decode it in the workflow before signing.

---

## Auto-update (future)

Tauri's built-in updater requires:
1. A signing keypair (see above)
2. A JSON endpoint listing the latest version and download URLs
3. `tauri.conf.json` `plugins.updater` block pointing at that endpoint

This is deferred until Phase 3.1 is complete (signing keys configured).
