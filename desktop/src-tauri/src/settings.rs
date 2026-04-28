//! User-editable persisted settings.
//!
//! Stored as JSON at the platform's app-config dir
//! (Windows: `%APPDATA%\com.radspeed.app\config.json`).

use serde::{Deserialize, Serialize};
use std::path::PathBuf;
use tauri::{AppHandle, Manager};

const FILE_NAME: &str = "config.json";

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Settings {
    /// Base URL of the RadSpeed cloud instance.
    #[serde(default = "default_api_base")]
    pub api_base: String,

    /// Hotkey string ("ctrl+i", "ctrl+shift+i", etc.) parsed by hotkey.rs.
    #[serde(default = "default_hotkey")]
    pub hotkey: String,

    /// Whether to apply guideline-aware reasoning (Fleischner / BIRADS / etc.).
    #[serde(default = "default_true")]
    pub use_guidelines: bool,

    /// What to do after the impression returns:
    ///   "after_selection" — paste a blank-line + "IMPRESSION:" + impression
    ///                       at the cursor (no jump)
    ///   "goto_impression" — send `jump_keys` first (Tab navigates to the
    ///                       PowerScribe IMPRESSION section by default), then paste
    #[serde(default = "default_paste_mode")]
    pub paste_mode: String,

    /// Keystroke sequence used to navigate from FINDINGS to IMPRESSION when
    /// paste_mode = "goto_impression". Default is a single Tab (PowerScribe One).
    #[serde(default = "default_jump_keys")]
    pub jump_keys: String,

    /// Optional bearer token for an authenticated RadSpeed deployment.
    #[serde(default)]
    pub bearer_token: String,
}

fn default_api_base() -> String {
    "https://dictation.markbekhit.com".to_string()
}
fn default_hotkey() -> String {
    "ctrl+i".to_string()
}
fn default_true() -> bool {
    true
}
fn default_paste_mode() -> String {
    "goto_impression".to_string()
}
fn default_jump_keys() -> String {
    "tab".to_string()
}

impl Default for Settings {
    fn default() -> Self {
        Self {
            api_base: default_api_base(),
            hotkey: default_hotkey(),
            use_guidelines: true,
            paste_mode: default_paste_mode(),
            jump_keys: default_jump_keys(),
            bearer_token: String::new(),
        }
    }
}

fn config_dir(app: &AppHandle) -> Result<PathBuf, String> {
    app.path()
        .app_config_dir()
        .map_err(|e| format!("config dir resolution failed: {e}"))
}

fn config_path(app: &AppHandle) -> Result<PathBuf, String> {
    Ok(config_dir(app)?.join(FILE_NAME))
}

pub fn load(app: &AppHandle) -> Settings {
    let path = match config_path(app) {
        Ok(p) => p,
        Err(e) => {
            log::warn!("settings: {e}, using defaults");
            return Settings::default();
        }
    };
    if !path.exists() {
        return Settings::default();
    }
    match std::fs::read_to_string(&path) {
        Ok(text) => serde_json::from_str::<Settings>(&text).unwrap_or_else(|e| {
            log::warn!("settings: parse error ({e}), using defaults");
            Settings::default()
        }),
        Err(e) => {
            log::warn!("settings: read error ({e}), using defaults");
            Settings::default()
        }
    }
}

pub fn save(app: &AppHandle, settings: &Settings) -> Result<(), String> {
    let dir = config_dir(app)?;
    if !dir.exists() {
        std::fs::create_dir_all(&dir).map_err(|e| format!("mkdir failed: {e}"))?;
    }
    let path = dir.join(FILE_NAME);
    let text = serde_json::to_string_pretty(settings).map_err(|e| format!("serialise: {e}"))?;
    std::fs::write(&path, text).map_err(|e| format!("write failed: {e}"))?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn defaults_are_safe() {
        let s = Settings::default();
        assert_eq!(s.api_base, "https://dictation.markbekhit.com");
        assert_eq!(s.hotkey, "ctrl+i");
        assert!(s.use_guidelines);
        assert_eq!(s.paste_mode, "goto_impression");
        assert_eq!(s.jump_keys, "tab");
        assert!(s.bearer_token.is_empty());
    }

    #[test]
    fn json_round_trip_preserves_fields() {
        let s = Settings {
            api_base: "https://example.com".to_string(),
            hotkey: "ctrl+shift+f5".to_string(),
            use_guidelines: false,
            paste_mode: "after_selection".to_string(),
            jump_keys: "tab tab".to_string(),
            bearer_token: "secret".to_string(),
        };
        let json = serde_json::to_string(&s).expect("serialize");
        let back: Settings = serde_json::from_str(&json).expect("deserialize");
        assert_eq!(back.api_base, s.api_base);
        assert_eq!(back.hotkey, s.hotkey);
        assert_eq!(back.use_guidelines, s.use_guidelines);
        assert_eq!(back.paste_mode, s.paste_mode);
        assert_eq!(back.jump_keys, s.jump_keys);
        assert_eq!(back.bearer_token, s.bearer_token);
    }

    #[test]
    fn missing_fields_get_defaults() {
        // A user that hand-edited config.json and removed fields shouldn't
        // crash the app — serde defaults cover the gaps.
        let partial = r#"{"api_base":"https://x.test"}"#;
        let s: Settings = serde_json::from_str(partial).expect("parse partial");
        assert_eq!(s.api_base, "https://x.test");
        assert_eq!(s.hotkey, "ctrl+i");
        assert_eq!(s.paste_mode, "goto_impression");
        assert!(s.use_guidelines);
    }

    #[test]
    fn unknown_fields_are_tolerated() {
        // Forward-compat: a future config.json with extra fields shouldn't
        // break older builds.
        let extra = r#"{"api_base":"https://x.test","future_field":42}"#;
        let s: Settings = serde_json::from_str(extra).expect("parse with extras");
        assert_eq!(s.api_base, "https://x.test");
    }
}
