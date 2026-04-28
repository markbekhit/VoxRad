//! RadSpeed Windows desktop companion — library entry point.
//!
//! The binary in `main.rs` simply calls `run()`. Splitting library / binary
//! lets us keep the door open for tauri::mobile_entry_point and unit tests
//! without the binary getting in the way.

mod api;
mod hotkey;
mod keyboard;
mod settings;
mod tray;

use serde::Deserialize;
use tauri::{AppHandle, Manager};
use tauri_plugin_global_shortcut::{GlobalShortcutExt, ShortcutState};

use crate::settings::Settings;

// ---------- Tauri commands invoked by the settings window frontend ----------

#[tauri::command]
fn cmd_get_settings(app: AppHandle) -> Settings {
    settings::load(&app)
}

#[derive(Debug, Deserialize)]
struct SaveBody {
    settings: Settings,
}

#[tauri::command]
fn cmd_save_settings(app: AppHandle, body: SaveBody) -> Result<(), String> {
    settings::save(&app, &body.settings)?;
    rebind_hotkey(&app, &body.settings.hotkey)?;
    tray::set_status(&app, "Settings saved.");
    Ok(())
}

#[tauri::command]
async fn cmd_test_api(api_base: String) -> Result<(), String> {
    api::ping(&api_base).await
}

#[tauri::command]
fn cmd_hide_settings(app: AppHandle) {
    if let Some(window) = app.get_webview_window("settings") {
        let _ = window.hide();
    }
}

#[tauri::command]
fn cmd_trigger_now(app: AppHandle) {
    hotkey::run_impressions_flow(app);
}

// ---------- Hotkey lifecycle ----------

fn rebind_hotkey(app: &AppHandle, spec: &str) -> Result<(), String> {
    let new_shortcut = hotkey::parse(spec)?;
    let gs = app.global_shortcut();
    let _ = gs.unregister_all();
    let app_clone = app.clone();
    let target_shortcut = new_shortcut.clone();
    gs.on_shortcut(new_shortcut, move |_app, shortcut, event| {
        if shortcut == &target_shortcut && event.state() == ShortcutState::Pressed {
            hotkey::run_impressions_flow(app_clone.clone());
        }
    })
    .map_err(|e| format!("register hotkey: {e}"))?;
    Ok(())
}

// ---------- Entry point ----------

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    let _ = env_logger::try_init();

    tauri::Builder::default()
        .plugin(tauri_plugin_global_shortcut::Builder::new().build())
        .plugin(tauri_plugin_process::init())
        .plugin(tauri_plugin_opener::init())
        .plugin(tauri_plugin_dialog::init())
        .invoke_handler(tauri::generate_handler![
            cmd_get_settings,
            cmd_save_settings,
            cmd_test_api,
            cmd_hide_settings,
            cmd_trigger_now,
        ])
        .setup(|app| {
            tray::build(app)?;

            // Prevent the settings window's close button from quitting the app.
            // Hide instead — tray menu is the canonical app exit path.
            if let Some(window) = app.get_webview_window("settings") {
                let win = window.clone();
                window.on_window_event(move |event| {
                    if let tauri::WindowEvent::CloseRequested { api, .. } = event {
                        api.prevent_close();
                        let _ = win.hide();
                    }
                });
            }

            // Register the configured hotkey at boot.
            let cfg = settings::load(app.handle());
            if let Err(e) = rebind_hotkey(app.handle(), &cfg.hotkey) {
                log::warn!("hotkey bind failed at startup: {e}");
                tray::set_status(app.handle(), &format!("Hotkey error: {e}"));
            } else {
                tray::set_status(
                    app.handle(),
                    &format!("Ready. Hotkey: {}", cfg.hotkey),
                );
            }

            // First-run UX: open the settings window if no config file exists yet.
            if first_run(app.handle()) {
                if let Some(window) = app.get_webview_window("settings") {
                    let _ = window.show();
                    let _ = window.set_focus();
                }
            }

            Ok(())
        })
        .run(tauri::generate_context!())
        .expect("error while running RadSpeed desktop");
}

fn first_run(app: &AppHandle) -> bool {
    let dir = match app.path().app_config_dir() {
        Ok(d) => d,
        Err(_) => return true,
    };
    !dir.join("config.json").exists()
}
