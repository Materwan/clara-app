//! Clara's desktop app: the web site of the Clara server in a window, a tray icon, and what only a program on this
//! computer can do (notifications, the folders Clara may work in). See `clara-agent` for the logic.

#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

mod commands;
mod runtime;
mod state;
mod tray;
mod windows;

use clara_agent::config::Config;
use tauri::RunEvent;
use tauri_plugin_autostart::MacosLauncher;

/// Windows starts the app with this at login: it goes straight to the tray, without opening the window.
const BACKGROUND: &str = "--background";

fn main() {
    let background = std::env::args().any(|argument| argument == BACKGROUND);
    tauri::Builder::default()
        // a second start only brings the window of the first one forward
        .plugin(tauri_plugin_single_instance::init(|app, _arguments, _directory| windows::open_main(app)))
        .plugin(tauri_plugin_autostart::init(MacosLauncher::LaunchAgent, Some(vec![BACKGROUND])))
        .plugin(tauri_plugin_notification::init())
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_opener::init())
        .manage(state::AppState::new(Config::load_default()))
        .invoke_handler(tauri::generate_handler![
            commands::get_state,
            commands::url_advice,
            commands::sign_in,
            commands::sign_out,
            commands::add_folder,
            commands::remove_folder,
            commands::set_autostart,
            commands::open_site,
        ])
        .setup(move |app| {
            let handle = app.handle();
            tray::build(handle)?;
            windows::start_main(handle, !background);
            runtime::start(handle);
            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("could not start Clara")
        .run(|_app, event| {
            // closing the last window does not stop the app: it lives in the tray ("Quit" there ends it)
            if let RunEvent::ExitRequested { code: None, api, .. } = event {
                api.prevent_exit();
            }
        });
}
