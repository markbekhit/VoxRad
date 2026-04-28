//! System tray icon + context menu.
//!
//! Status messages are surfaced as tooltip text on the tray icon — there's
//! no native toast in Tauri 2 without an extra plugin, and Windows users
//! don't really love toasts anyway.

use once_cell::sync::OnceCell;
use std::sync::Mutex;
use tauri::menu::{Menu, MenuEvent, MenuItem};
use tauri::tray::{TrayIcon, TrayIconBuilder};
use tauri::{App, AppHandle, Manager};

const ID_OPEN_SETTINGS: &str = "open_settings";
const ID_CHECK_UPDATES: &str = "check_updates";
const ID_OPEN_WEB: &str = "open_web";
const ID_QUIT: &str = "quit";

static TRAY: OnceCell<Mutex<Option<TrayIcon>>> = OnceCell::new();

pub fn build(app: &mut App) -> Result<(), tauri::Error> {
    let handle = app.handle();

    let open_settings = MenuItem::with_id(handle, ID_OPEN_SETTINGS, "Settings…", true, None::<&str>)?;
    let open_web = MenuItem::with_id(handle, ID_OPEN_WEB, "Open RadSpeed in browser", true, None::<&str>)?;
    let check_updates = MenuItem::with_id(handle, ID_CHECK_UPDATES, "Check for updates", true, None::<&str>)?;
    let quit = MenuItem::with_id(handle, ID_QUIT, "Quit RadSpeed", true, None::<&str>)?;

    let menu = Menu::with_items(
        handle,
        &[&open_settings, &open_web, &check_updates, &quit],
    )?;

    let icon = app
        .default_window_icon()
        .cloned()
        .ok_or_else(|| tauri::Error::AssetNotFound("default window icon".into()))?;

    let tray = TrayIconBuilder::new()
        .icon(icon)
        .tooltip("RadSpeed — press hotkey to generate an impression")
        .menu(&menu)
        .show_menu_on_left_click(true)
        .on_menu_event(on_menu_event)
        .build(handle)?;

    let _ = TRAY.set(Mutex::new(Some(tray)));
    Ok(())
}

fn on_menu_event(app: &AppHandle, event: MenuEvent) {
    match event.id.as_ref() {
        ID_OPEN_SETTINGS => show_settings(app),
        ID_OPEN_WEB => {
            let api_base = crate::settings::load(app).api_base;
            if let Err(e) = open_url(app, &api_base) {
                log::warn!("open_url failed: {e}");
            }
        }
        ID_CHECK_UPDATES => {
            // Phase 3.1: wire to tauri-plugin-updater once signing keys are configured.
            set_status(app, "Updates: configure signing keys (see README).");
        }
        ID_QUIT => {
            app.exit(0);
        }
        _ => {}
    }
}

fn show_settings(app: &AppHandle) {
    if let Some(window) = app.get_webview_window("settings") {
        let _ = window.show();
        let _ = window.set_focus();
    }
}

pub fn set_status(app: &AppHandle, msg: &str) {
    log::info!("tray status: {msg}");
    if let Some(slot) = TRAY.get() {
        if let Ok(guard) = slot.lock() {
            if let Some(tray) = guard.as_ref() {
                let _ = tray.set_tooltip(Some(msg));
            }
        }
    }
    let _ = app; // keep API symmetric for future enhancements
}

fn open_url(app: &AppHandle, url: &str) -> Result<(), String> {
    use tauri_plugin_opener::OpenerExt;
    app.opener()
        .open_url(url, None::<&str>)
        .map_err(|e| format!("{e}"))
}
