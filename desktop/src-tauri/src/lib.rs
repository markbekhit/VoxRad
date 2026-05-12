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
pub(crate) mod updater;

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
fn cmd_get_version() -> &'static str {
    env!("CARGO_PKG_VERSION")
}

#[tauri::command]
fn cmd_show_app(app: AppHandle) {
    show_app_window(&app);
}

pub(crate) fn show_app_window(app: &AppHandle) {
    if let Some(window) = app.get_webview_window("app") {
        let _ = window.show();
        let _ = window.set_focus();
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
        // Must be registered first so duplicate launches are caught before
        // any expensive setup happens. The callback runs in the original
        // (already-running) instance and brings the main app window forward.
        .plugin(tauri_plugin_single_instance::init(|app, _args, _cwd| {
            show_app_window(app);
        }))
        .plugin(tauri_plugin_global_shortcut::Builder::new().build())
        .plugin(tauri_plugin_process::init())
        .plugin(tauri_plugin_opener::init())
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_updater::Builder::new().build())
        .invoke_handler(tauri::generate_handler![
            cmd_get_settings,
            cmd_save_settings,
            cmd_test_api,
            cmd_hide_settings,
            cmd_trigger_now,
            cmd_show_app,
            cmd_get_version,
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

            // Build the full-app window (loads the RadSpeed web app in WebView2).
            // Hidden until the user clicks "Open RadSpeed" in the tray menu.
            let api_base = settings::load(app.handle()).api_base;
            let app_url = url::Url::parse(&api_base)
                .unwrap_or_else(|_| url::Url::parse("https://dictation.markbekhit.com").unwrap());
            let app_window = tauri::WebviewWindowBuilder::new(
                app,
                "app",
                tauri::WebviewUrl::External(app_url),
            )
            .title("RadSpeed")
            .inner_size(1400.0, 900.0)
            .min_inner_size(900.0, 600.0)
            .center()
            .visible(false)
            .build()?;

            // Close button hides; tray menu is the exit path.
            let win = app_window.clone();
            app_window.on_window_event(move |event| {
                if let tauri::WindowEvent::CloseRequested { api, .. } = event {
                    api.prevent_close();
                    let _ = win.hide();
                }
            });

            // Always show the main app window on launch — gives users a
            // visible entry point so the Start Menu / desktop shortcut acts
            // as a real "open RadSpeed" button instead of silently going to
            // the (often hidden) tray. Single-instance plugin handles
            // subsequent launches by bringing this same window forward.
            let _ = app_window.show();
            let _ = app_window.set_focus();

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

            // Background update check after 10 s — lets the tray settle first
            // and avoids a restart in the first seconds of the app's life.
            let app_for_update = app.handle().clone();
            tauri::async_runtime::spawn(async move {
                tokio::time::sleep(std::time::Duration::from_secs(10)).await;
                updater::run(app_for_update);
            });

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
