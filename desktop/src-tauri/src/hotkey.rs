//! Parse hotkey strings like "ctrl+i", "ctrl+shift+f1" into Tauri's Shortcut
//! struct, and run the impressions round-trip when triggered.

use std::sync::Arc;
use tauri::AppHandle;
use tauri_plugin_global_shortcut::{Code, Modifiers, Shortcut};

use crate::api;
use crate::keyboard;
use crate::settings::{self, Settings};
use crate::tray;

pub fn parse(spec: &str) -> Result<Shortcut, String> {
    let spec = spec.trim().to_lowercase();
    if spec.is_empty() {
        return Err("empty hotkey".to_string());
    }
    let parts: Vec<&str> = spec.split('+').map(|s| s.trim()).collect();
    let mut mods = Modifiers::empty();
    let mut key: Option<Code> = None;
    for part in parts {
        match part {
            "ctrl" | "control" => mods |= Modifiers::CONTROL,
            "shift" => mods |= Modifiers::SHIFT,
            "alt" => mods |= Modifiers::ALT,
            "win" | "super" | "meta" => mods |= Modifiers::META,
            other => {
                key = Some(parse_code(other)?);
            }
        }
    }
    let code = key.ok_or_else(|| format!("hotkey '{spec}' has no key"))?;
    let modifiers = if mods.is_empty() { None } else { Some(mods) };
    Ok(Shortcut::new(modifiers, code))
}

fn parse_code(token: &str) -> Result<Code, String> {
    // Letters
    if token.len() == 1 {
        let c = token.chars().next().unwrap();
        if c.is_ascii_alphabetic() {
            return match c.to_ascii_uppercase() {
                'A' => Ok(Code::KeyA), 'B' => Ok(Code::KeyB), 'C' => Ok(Code::KeyC),
                'D' => Ok(Code::KeyD), 'E' => Ok(Code::KeyE), 'F' => Ok(Code::KeyF),
                'G' => Ok(Code::KeyG), 'H' => Ok(Code::KeyH), 'I' => Ok(Code::KeyI),
                'J' => Ok(Code::KeyJ), 'K' => Ok(Code::KeyK), 'L' => Ok(Code::KeyL),
                'M' => Ok(Code::KeyM), 'N' => Ok(Code::KeyN), 'O' => Ok(Code::KeyO),
                'P' => Ok(Code::KeyP), 'Q' => Ok(Code::KeyQ), 'R' => Ok(Code::KeyR),
                'S' => Ok(Code::KeyS), 'T' => Ok(Code::KeyT), 'U' => Ok(Code::KeyU),
                'V' => Ok(Code::KeyV), 'W' => Ok(Code::KeyW), 'X' => Ok(Code::KeyX),
                'Y' => Ok(Code::KeyY), 'Z' => Ok(Code::KeyZ),
                _ => Err(format!("unsupported key: {token}")),
            };
        }
        if c.is_ascii_digit() {
            return match c {
                '0' => Ok(Code::Digit0), '1' => Ok(Code::Digit1), '2' => Ok(Code::Digit2),
                '3' => Ok(Code::Digit3), '4' => Ok(Code::Digit4), '5' => Ok(Code::Digit5),
                '6' => Ok(Code::Digit6), '7' => Ok(Code::Digit7), '8' => Ok(Code::Digit8),
                '9' => Ok(Code::Digit9),
                _ => Err(format!("unsupported digit: {token}")),
            };
        }
    }
    // Function keys
    if let Some(rest) = token.strip_prefix('f') {
        if let Ok(n) = rest.parse::<u8>() {
            return match n {
                1 => Ok(Code::F1), 2 => Ok(Code::F2), 3 => Ok(Code::F3),
                4 => Ok(Code::F4), 5 => Ok(Code::F5), 6 => Ok(Code::F6),
                7 => Ok(Code::F7), 8 => Ok(Code::F8), 9 => Ok(Code::F9),
                10 => Ok(Code::F10), 11 => Ok(Code::F11), 12 => Ok(Code::F12),
                _ => Err(format!("F-key out of range: {token}")),
            };
        }
    }
    Err(format!("unknown key token: {token}"))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parses_simple_letter_hotkey() {
        let s = parse("ctrl+i").expect("ctrl+i must parse");
        assert_eq!(s, Shortcut::new(Some(Modifiers::CONTROL), Code::KeyI));
    }

    #[test]
    fn parses_multi_modifier_hotkey() {
        let s = parse("ctrl+shift+f1").expect("ctrl+shift+f1 must parse");
        assert_eq!(
            s,
            Shortcut::new(Some(Modifiers::CONTROL | Modifiers::SHIFT), Code::F1)
        );
    }

    #[test]
    fn case_insensitive() {
        let a = parse("CTRL+I").expect("uppercase parse");
        let b = parse("ctrl+i").expect("lowercase parse");
        assert_eq!(a, b);
    }

    #[test]
    fn ignores_whitespace() {
        let a = parse("  ctrl + i  ").expect("padded parse");
        let b = parse("ctrl+i").expect("unpadded parse");
        assert_eq!(a, b);
    }

    #[test]
    fn parses_alt_and_super_aliases() {
        let s = parse("alt+win+a").expect("alt+win+a must parse");
        assert_eq!(
            s,
            Shortcut::new(Some(Modifiers::ALT | Modifiers::META), Code::KeyA)
        );
    }

    #[test]
    fn parses_digit_and_function_keys() {
        assert!(parse("ctrl+0").is_ok());
        assert!(parse("ctrl+9").is_ok());
        assert!(parse("f12").is_ok());
        assert!(parse("ctrl+f5").is_ok());
    }

    #[test]
    fn rejects_empty() {
        assert!(parse("").is_err());
        assert!(parse("   ").is_err());
    }

    #[test]
    fn rejects_modifier_only() {
        assert!(parse("ctrl").is_err());
        assert!(parse("ctrl+shift").is_err());
    }

    #[test]
    fn rejects_unknown_key() {
        assert!(parse("ctrl+plonk").is_err());
        assert!(parse("ctrl+f99").is_err());
    }
}

/// Spawn the impressions round-trip on a Tokio task. The hotkey handler must
/// return immediately, so we offload the HTTP call + clipboard work.
pub fn run_impressions_flow(app: AppHandle) {
    let app = Arc::new(app);
    tauri::async_runtime::spawn(async move {
        let settings = settings::load(app.as_ref());
        if let Err(e) = do_round_trip(&settings).await {
            log::warn!("impressions flow failed: {e}");
            tray::set_status(app.as_ref(), &format!("Impressions failed: {e}"));
        } else {
            tray::set_status(app.as_ref(), "Impression pasted.");
        }
    });
}

async fn do_round_trip(settings: &Settings) -> Result<(), String> {
    let findings = keyboard::capture_selection()?;
    if findings.trim().is_empty() {
        return Err("no text selected".to_string());
    }
    let impression =
        api::fetch_impression(&settings.api_base, &findings, settings.use_guidelines, &settings.bearer_token)
            .await?;

    if settings.paste_mode == "goto_impression" && !settings.jump_keys.trim().is_empty() {
        keyboard::send_keys(&settings.jump_keys)?;
    }

    let payload = if settings.paste_mode == "after_selection" {
        format!("\r\n\r\nIMPRESSION:\r\n{}", impression.trim_end())
    } else {
        // goto_impression: jumped to the conclusion field, so paste raw
        impression.trim_end().to_string()
    };

    keyboard::paste_block(&payload)?;
    Ok(())
}
