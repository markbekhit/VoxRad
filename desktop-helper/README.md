# RadSpeed Impressions — Windows desktop helper

Free overlay tool. Select your dictated findings in PowerScribe One (or
anywhere else), press **Ctrl+I**, and a guideline-aware impression appears
in the IMPRESSION section of the report.

## Install

1. Install **AutoHotkey v2** (free): https://www.autohotkey.com/
2. Download `RadSpeedImpressions.ahk` from this folder.
3. Double-click the `.ahk` file to run it. Look for the green **H** icon (AutoHotkey's default — hovering shows "RadSpeed Impressions") in the
   system tray.
4. Optional: drop a shortcut to the `.ahk` file in your Windows Startup
   folder so the helper launches with Windows.
   (Win+R → type `shell:startup` → drop a shortcut there.)

## Use

In PowerScribe One:

1. Dictate the FINDINGS section as normal.
2. Select the findings text (mouse drag, or Ctrl+Shift+End at the start of
   the section).
3. Press **Ctrl+I**.
4. ~1.5 s later, the impression is inserted into the IMPRESSION section
   (or after the selection — depends on configuration, see below).

A tray notification confirms each step. Errors (no selection, server down,
rate limit hit) appear as toast notifications.

## Updating

Right-click the tray icon → **Check for updates**. The helper downloads the
latest script directly from GitHub, replaces itself, and reloads — no
manual download required after the first install.

If "Check for updates" fails with a permission error, the helper is in a
folder Windows protects — move the `.ahk` file to your Documents folder
or run AutoHotkey as administrator.

## Configuration

Right-click the tray icon → **Edit settings** to open
`RadSpeedImpressions.ini`. After saving, right-click → **Reload**.

| Setting | Purpose |
|---|---|
| `Hotkey` | Default `^i` (Ctrl+I). AHK syntax: `^`=Ctrl, `+`=Shift, `!`=Alt, `#`=Win. Examples: `^!i` for Ctrl+Alt+I. |
| `WithGuidelines` | `true` (default) applies Fleischner / BI-RADS / LI-RADS / PI-RADS / TI-RADS recommendations when relevant findings are detected. |
| `PasteMode` | How the impression is inserted. Default `goto_impression`. See below. |
| `JumpKeys` | The keystrokes that take your cursor from FINDINGS to IMPRESSION in your PowerScribe template. Default empty. See below. |
| `Modality` | Optional context hint sent with each request. Leave blank to let the model infer from the findings. |

### Paste modes

- **`goto_impression`** (default) — sends `JumpKeys` first to navigate to the
  IMPRESSION section, then pastes there. Falls back to `after_selection`
  behaviour if `JumpKeys` is empty.
- **`after_selection`** — moves to the end of the selected findings, inserts
  a blank line and an `IMPRESSION:` heading, then pastes the impression.
- **`replace_selection`** — overwrites the selected text with the impression.
  Useful if you've selected the IMPRESSION field's existing placeholder.
- **`at_cursor`** — does not require a selection; reads the entire active
  document via Ctrl+A, generates from that, then pastes at end of document.

### Finding the right `JumpKeys` for PowerScribe One

PowerScribe One does not have a single universal "jump to impression"
hotkey — sites configure their own. Common options to try:

- **`{F2}`** — "Next required field" (PowerScribe default in many templates).
  Press F2 enough times to land on IMPRESSION; e.g. `{F2 2}` for two presses.
- **`{Tab 3}`** — Tab three times. Brittle; depends on field count.
- **`^+i`** — A custom shortcut you have configured in PowerScribe to jump to
  IMPRESSION.
- **Voice command + macro** — Some sites have voice commands like "go to
  impression" mapped to a hotkey via PowerScribe Auto-Text. Use the same
  hotkey here.

To discover what works in your template: open a study, click in the
FINDINGS field, then try F2 / Tab / Ctrl+Shift+I and watch where the cursor
goes. Use whatever combination lands on IMPRESSION.

## Known constraints

- **Rate limit**: 20 impressions per hour per IP address while in free
  preview. Plenty for a normal day; if you hit it, the helper will show a
  rate-limit toast.
- **Running PowerScribe as administrator**: if PowerScribe One runs
  elevated at your site, AutoHotkey scripts running unelevated cannot send
  keys to it. Right-click the `.ahk` file → "Run as administrator" (or set
  the shortcut's Compatibility properties to always run as admin).
- **Clipboard**: the helper saves and restores your clipboard around each
  request, so pasting Ctrl+I doesn't trash whatever you had copied.
- **Network**: requires internet access to `dictation.markbekhit.com`. If
  your site blocks it on the clinical network, a local self-hosted RadSpeed
  is an option (point `ApiUrl` at it).

## Troubleshooting

- **Hotkey does nothing** — make sure the script is running (R icon in
  tray). If PowerScribe is elevated, see "Running as administrator" above.
- **"Select the findings text first"** — you pressed Ctrl+I without
  selecting anything. Drag-select the findings and try again.
- **"HTTP 429: Hourly limit reached"** — wait an hour or contact us to
  raise your limit.
- **Impression appears in the wrong place** — your `PasteMode` /
  `JumpKeys` need adjusting. Try `after_selection` mode first to confirm
  generation is working, then tune `JumpKeys`.

## What's next

- Code-signed installer (no AHK install needed)
- Mac / Fluency Direct equivalent
- PowerScribe Auto-Text / Connect SDK integration (lets the helper find the
  IMPRESSION field automatically without `JumpKeys`)
