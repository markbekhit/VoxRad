//! System tray icon + context menu.

use once_cell::sync::OnceCell;
use std::sync::Mutex;
use tauri::menu::{Menu, MenuEvent, MenuItem, PredefinedMenuItem};
use tauri::tray::{TrayIcon, TrayIconBuilder};
use tauri::{App, AppHandle, Manager};

const ID_OPEN_APP: &str = "open_app";
const ID_OPEN_SETTINGS: &str = "open_settings";
const ID_CHECK_UPDATES: &str = "check_updates";
const ID_OPEN_WEB: &str = "open_web";
const ID_QUIT: &str = "quit";

static TRAY: OnceCell<Mutex<Option<TrayIcon>>> = OnceCell::new();

pub fn build(app: &mut App) -> Result<(), tauri::Error> {
    let handle = app.handle();

    let version_label = format!("RadSpeed v{}", env!("CARGO_PKG_VERSION"));
    let version_item  = MenuItem::with_id(handle, "version", version_label, false, None::<&str>)?;
    let sep0          = PredefinedMenuItem::separator(handle)?;
    let open_app      = MenuItem::with_id(handle, ID_OPEN_APP,      "Open RadSpeed",     true, None::<&str>)?;
    let open_settings = MenuItem::with_id(handle, ID_OPEN_SETTINGS, "Settings…",         true, None::<&str>)?;
    let sep1          = PredefinedMenuItem::separator(handle)?;
    let open_web      = MenuItem::with_id(handle, ID_OPEN_WEB,      "Open in browser",   true, None::<&str>)?;
    let check_updates = MenuItem::with_id(handle, ID_CHECK_UPDATES, "Check for updates", true, None::<&str>)?;
    let sep2          = PredefinedMenuItem::separator(handle)?;
    let quit          = MenuItem::with_id(handle, ID_QUIT,          "Quit RadSpeed",     true, None::<&str>)?;

    let menu = Menu::with_items(
        handle,
        &[&version_item, &sep0, &open_app, &open_settings, &sep1, &open_web, &check_updates, &sep2, &quit],
    )?;

    let icon = app
        .default_window_icon()
        .cloned()
        .ok_or_else(|| tauri::Error::AssetNotFound("default window icon".into()))?;

    let tray = TrayIconBuilder::new()
        .icon(icon)
        .tooltip(format!("RadSpeed v{} — press hotkey to generate an impression", env!("CARGO_PKG_VERSION")))
        .menu(&menu)
        .show_menu_on_left_click(false)
        .on_menu_event(on_menu_event)
        .on_tray_icon_event(|tray, event| {
            if let tauri::tray::TrayIconEvent::Click {
                button: tauri::tray::MouseButton::Left,
                button_state: tauri::tray::MouseButtonState::Up,
                ..
            } = event
            {
                crate::show_app_window(tray.app_handle());
            }
        })
        .build(handle)?;

    let _ = TRAY.set(Mutex::new(Some(tray)));
    Ok(())
}

fn on_menu_event(app: &AppHandle, event: MenuEvent) {
    match event.id.as_ref() {
        ID_OPEN_APP => crate::show_app_window(app),
        ID_OPEN_SETTINGS => show_settings(app),
        ID_OPEN_WEB => {
            let api_base = crate::settings::load(app).api_base;
            if let Err(e) = open_url(app, &api_base) {
                log::warn!("open_url failed: {e}");
            }
        }
        ID_CHECK_UPDATES => { crate::updater::run(app.clone()); }
        ID_QUIT => { app.exit(0); }
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
    let _ = app;
}

fn open_url(app: &AppHandle, url: &str) -> Result<(), String> {
    use tauri_plugin_opener::OpenerExt;
    app.opener()
        .open_url(url, None::<&str>)
        .map_err(|e| format!("{e}"))
}
