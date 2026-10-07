//! The icon in the notification area: click it to open the window, click it again to put it away. It turns grey when
//! Clara is not running.

use clara_agent::icon::grey;
use clara_agent::status::ServerState;
use clara_agent::APP_NAME;
use tauri::image::Image;
use tauri::menu::{CheckMenuItem, CheckMenuItemBuilder, Menu, MenuItemBuilder, PredefinedMenuItem};
use tauri::tray::{MouseButton, MouseButtonState, TrayIcon, TrayIconBuilder, TrayIconEvent};
use tauri::{AppHandle, Manager};
use tauri_plugin_autostart::ManagerExt;

use crate::{runtime, windows};

const ICON: &[u8] = include_bytes!("../icons/icon.png");

pub struct Tray {
    icon: TrayIcon,
    autostart: CheckMenuItem<tauri::Wry>,
    normal: Image<'static>,
    grey: Image<'static>,
}

pub fn build(app: &AppHandle) -> tauri::Result<()> {
    let normal = Image::from_bytes(ICON)?;
    let mut pixels = normal.rgba().to_vec();
    grey(&mut pixels);
    let grey = Image::new_owned(pixels, normal.width(), normal.height());

    let open = MenuItemBuilder::with_id("open", format!("Open {APP_NAME}")).build(app)?;
    let tasks = MenuItemBuilder::with_id("tasks", "Tasks…").build(app)?;
    let settings = MenuItemBuilder::with_id("settings", "Settings and folders…").build(app)?;
    let autostart = CheckMenuItemBuilder::with_id("autostart", "Start with Windows")
        .checked(app.autolaunch().is_enabled().unwrap_or(false))
        .build(app)?;
    let quit = MenuItemBuilder::with_id("quit", "Quit").build(app)?;
    let menu = Menu::with_items(
        app,
        &[&open, &tasks, &PredefinedMenuItem::separator(app)?, &settings, &autostart, &PredefinedMenuItem::separator(app)?, &quit],
    )?;

    let icon = TrayIconBuilder::with_id("clara")
        .icon(normal.clone())
        .tooltip(APP_NAME)
        .menu(&menu)
        .show_menu_on_left_click(false)
        .on_menu_event(|app, event| match event.id().as_ref() {
            "open" => windows::open_main(app),
            "tasks" => windows::open_page(app, "tasks"),
            "settings" => windows::open_settings(app),
            "autostart" => toggle_autostart(app),
            "quit" => quit_app(app),
            _ => {}
        })
        .on_tray_icon_event(|tray, event| match event {
            TrayIconEvent::Click { button: MouseButton::Left, button_state: MouseButtonState::Up, .. } => {
                windows::toggle_main(tray.app_handle());
            }
            TrayIconEvent::DoubleClick { button: MouseButton::Left, .. } => windows::open_main(tray.app_handle()),
            _ => {}
        })
        .build(app)?;
    app.manage(Tray { icon, autostart, normal, grey });
    Ok(())
}

/// The state of the server, in the tooltip and in the colour of the icon (`None`: not known yet).
pub fn show_state(app: &AppHandle, state: Option<ServerState>) {
    let Some(tray) = app.try_state::<Tray>() else { return };
    let tooltip = state.map_or(APP_NAME, ServerState::status);
    let icon = if state == Some(ServerState::Down) { tray.grey.clone() } else { tray.normal.clone() };
    let _ = tray.icon.set_tooltip(Some(tooltip));
    let _ = tray.icon.set_icon(Some(icon));
}

/// The menu's check mark, from what Windows says (the settings page changes it too).
pub fn show_autostart(app: &AppHandle, enabled: bool) {
    if let Some(tray) = app.try_state::<Tray>() {
        let _ = tray.autostart.set_checked(enabled);
    }
}

fn toggle_autostart(app: &AppHandle) {
    let Some(tray) = app.try_state::<Tray>() else { return };
    let wanted = tray.autostart.is_checked().unwrap_or(false); // the item has already flipped itself
    if let Err(error) = set_autostart(app, wanted) {
        let _ = tray.autostart.set_checked(!wanted);
        runtime::notify(
            app,
            &clara_agent::events::Toast { title: APP_NAME.into(), body: format!("Could not change the start-up setting: {error}") },
        );
    }
    windows::tell_settings(app);
}

/// "Start with Windows": a value in `HKCU\Software\Microsoft\Windows\CurrentVersion\Run` that starts the app with
/// `--background`, so that it goes straight to the tray.
pub fn set_autostart(app: &AppHandle, enabled: bool) -> Result<(), String> {
    let manager = app.autolaunch();
    let done = if enabled { manager.enable() } else { manager.disable() };
    done.map_err(|error| error.to_string())?;
    show_autostart(app, enabled);
    Ok(())
}

pub fn quit_app(app: &AppHandle) {
    runtime::stop(app);
    app.exit(0);
}
