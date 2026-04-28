//! Keyboard simulation via `enigo` + clipboard via `arboard`.
//!
//! Notes from the AHK helper iterations:
//!   - Always release every modifier explicitly. Lingering Ctrl turns later
//!     `Send` calls into shortcut activations (`v` becomes Ctrl+V repeats,
//!     letters become invisible bind activations, etc.).
//!   - Prefer Shift+Insert over Ctrl+V for paste. Most Windows text fields
//!     accept Shift+Insert and it sidesteps the modifier-collision class
//!     of bugs entirely.
//!   - Build the entire paste payload on the clipboard up front. Don't
//!     SendText the heading char-by-char — every char is another chance
//!     for a modifier to interfere.

use arboard::Clipboard;
use enigo::{Direction, Enigo, Key, Keyboard, Settings};
use std::thread;
use std::time::Duration;

const MOD_SETTLE_MS: u64 = 30;
const CLIP_SETTLE_MS: u64 = 180;

fn new_enigo() -> Result<Enigo, String> {
    Enigo::new(&Settings::default()).map_err(|e| format!("enigo init: {e}"))
}

/// Save the current clipboard, simulate Ctrl+C to grab the active selection,
/// read the resulting clipboard, restore the previous clipboard.
/// Returns the captured selection (may be empty if the host had no selection).
pub fn capture_selection() -> Result<String, String> {
    // Snapshot existing clipboard (best-effort — empty is fine)
    let saved = Clipboard::new().ok().and_then(|mut c| c.get_text().ok());

    // Clear so we can detect "nothing selected" reliably
    if let Ok(mut c) = Clipboard::new() {
        let _ = c.set_text("");
    }

    let mut enigo = new_enigo()?;
    enigo
        .key(Key::Control, Direction::Press)
        .map_err(|e| format!("ctrl press: {e}"))?;
    thread::sleep(Duration::from_millis(MOD_SETTLE_MS));
    enigo
        .key(Key::Unicode('c'), Direction::Click)
        .map_err(|e| format!("c click: {e}"))?;
    thread::sleep(Duration::from_millis(MOD_SETTLE_MS));
    enigo
        .key(Key::Control, Direction::Release)
        .map_err(|e| format!("ctrl release: {e}"))?;
    thread::sleep(Duration::from_millis(CLIP_SETTLE_MS));

    let captured = Clipboard::new()
        .ok()
        .and_then(|mut c| c.get_text().ok())
        .unwrap_or_default();

    // Restore the saved clipboard so we don't pollute the user's Ctrl+V.
    if let Some(orig) = saved {
        if let Ok(mut c) = Clipboard::new() {
            let _ = c.set_text(orig);
        }
    }
    Ok(captured)
}

/// Send a sequence like "tab", "tab tab", or "down enter" — space-separated
/// keystroke names. Used by goto_impression to navigate from FINDINGS to
/// the IMPRESSION field in PowerScribe One templates.
pub fn send_keys(spec: &str) -> Result<(), String> {
    let spec = spec.trim();
    if spec.is_empty() {
        return Ok(());
    }
    let mut enigo = new_enigo()?;
    for token in spec.split_whitespace() {
        let key = match token.to_lowercase().as_str() {
            "tab" => Key::Tab,
            "enter" | "return" => Key::Return,
            "down" => Key::DownArrow,
            "up" => Key::UpArrow,
            "right" => Key::RightArrow,
            "left" => Key::LeftArrow,
            "home" => Key::Home,
            "end" => Key::End,
            "pgdn" | "pagedown" => Key::PageDown,
            "pgup" | "pageup" => Key::PageUp,
            // Single character literal — useful for things like "f5" later.
            other if other.len() == 1 => Key::Unicode(other.chars().next().unwrap()),
            other => {
                log::warn!("unknown jump key: {other}");
                continue;
            }
        };
        enigo
            .key(key, Direction::Click)
            .map_err(|e| format!("send {token}: {e}"))?;
        thread::sleep(Duration::from_millis(20));
    }
    Ok(())
}

/// Place `payload` on the clipboard and trigger Shift+Insert to paste.
/// The previous clipboard is NOT restored automatically — call sites that
/// want preservation should snapshot beforehand.
pub fn paste_block(payload: &str) -> Result<(), String> {
    let mut clipboard = Clipboard::new().map_err(|e| format!("clipboard init: {e}"))?;
    clipboard
        .set_text(payload)
        .map_err(|e| format!("clipboard set: {e}"))?;
    thread::sleep(Duration::from_millis(CLIP_SETTLE_MS));

    let mut enigo = new_enigo()?;
    enigo
        .key(Key::Shift, Direction::Press)
        .map_err(|e| format!("shift press: {e}"))?;
    thread::sleep(Duration::from_millis(MOD_SETTLE_MS));
    enigo
        .key(Key::Insert, Direction::Click)
        .map_err(|e| format!("insert click: {e}"))?;
    thread::sleep(Duration::from_millis(MOD_SETTLE_MS));
    enigo
        .key(Key::Shift, Direction::Release)
        .map_err(|e| format!("shift release: {e}"))?;
    Ok(())
}
