//! Auto-update: check GitHub Releases for a newer version, download,
//! install, and restart. Uses tauri-plugin-updater with Ed25519 signature
//! verification — the public key is baked into tauri.conf.json at build time.
//!
//! Called on startup (delayed 10 s so the tray is visible first) and when
//! the user clicks "Check for updates" in the tray menu.

use tauri::AppHandle;
use tauri_plugin_process::AppHandleExt;
use tauri_plugin_updater::UpdaterExt;

use crate::tray;

/// Spawn the update check as a background Tauri async task. Returns immediately.
pub fn run(app: AppHandle) {
    tauri::async_runtime::spawn(async move {
        if let Err(e) = check_and_apply(app).await {
            log::warn!("updater: {e}");
        }
    });
}

async fn check_and_apply(app: AppHandle) -> Result<(), Box<dyn std::error::Error>> {
    let updater = match app.updater() {
        Ok(u) => u,
        Err(e) => {
            log::warn!("updater unavailable: {e}");
            return Ok(());
        }
    };

    tray::set_status(&app, "Checking for updates...");

    match updater.check().await? {
        None => {
            tray::set_status(&app, &format!("Ready. RadSpeed v{}", env!("CARGO_PKG_VERSION")));
        }
        Some(update) => {
            let version = update.version.clone();
            tray::set_status(&app, &format!("Downloading update {}…", version));

            let app2 = app.clone();
            update
                .download_and_install(
                    |_chunk, _total| {},
                    move || {
                        tray::set_status(&app2, "Update ready — restarting…");
                    },
                )
                .await?;

            app.restart();
        }
    }

    Ok(())
}
