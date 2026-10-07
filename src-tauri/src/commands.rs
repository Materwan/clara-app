//! What the settings page asks of the app. Only that page (a page of the app itself) can call these: the window of the
//! site is a page of another origin, which Tauri gives no access to them.

use clara_agent::api::{self, COOKIE};
use clara_agent::config::{normalize_url, url_hint, Config};
use clara_agent::folders::Registry;
use serde::Serialize;
use tauri::{AppHandle, Manager, Url};
use tauri_plugin_dialog::DialogExt;

use crate::state::{lock, AppState};
use crate::{runtime, tray, windows};

#[derive(Serialize)]
pub struct Folder {
    alias: String,
    path: String,
    /// The folder is not on the disk any more.
    missing: bool,
}

#[derive(Serialize)]
pub struct Snapshot {
    signed_in: bool,
    url: String,
    user_name: String,
    /// Why the app asks to sign in again.
    notice: Option<String>,
    /// "Clara is running"...; none until the server has said.
    server: Option<&'static str>,
    computer: String,
    folders: Vec<Folder>,
    autostart: bool,
    version: &'static str,
}

fn snapshot(app: &AppHandle) -> Snapshot {
    use tauri_plugin_autostart::ManagerExt;
    let state = app.state::<AppState>();
    let config = state.config();
    let registry = Registry::load_default();
    let notice = lock(&state.notice).clone();
    let server = (*lock(&state.server)).map(|server| server.status());
    Snapshot {
        signed_in: config.ready(),
        url: config.url.clone(),
        user_name: config.user_name.clone(),
        notice,
        server,
        computer: registry.name.clone(),
        folders: registry
            .folders
            .iter()
            .map(|(alias, path)| Folder { alias: alias.clone(), path: path.clone(), missing: !std::path::Path::new(path).is_dir() })
            .collect(),
        autostart: app.autolaunch().is_enabled().unwrap_or(false),
        version: env!("CARGO_PKG_VERSION"),
    }
}

#[tauri::command]
pub async fn get_state(app: AppHandle) -> Snapshot {
    snapshot(&app)
}

/// A word of advice about the address being typed (plain http to another machine, a Tailscale address...).
#[tauri::command]
pub fn url_advice(url: String) -> Option<&'static str> {
    url_hint(&url)
}

/// Signs in: for the app (its token reaches the reminders and the folders) and, so that the site does not ask again,
/// for the web site, whose session is put in the window.
#[tauri::command]
pub async fn sign_in(app: AppHandle, url: String, user: String, password: String) -> Result<Snapshot, String> {
    let url = normalize_url(&url)?;
    let site = Url::parse(&url).map_err(|_| "That does not look like an address.".to_owned())?;
    if user.trim().is_empty() || password.is_empty() {
        return Err("Type your user name and your password.".into());
    }
    let (login, web) = {
        let (url, user) = (url.clone(), user.clone());
        tauri::async_runtime::spawn_blocking(move || {
            let login = api::login(&url, &user, &password).map_err(|error| error.message)?;
            // the site's own session is a bonus: without it the site shows its sign-in page, that is all
            let web = api::login_web(&url, &user, &password).ok();
            Ok::<_, String>((login, web))
        })
        .await
        .map_err(|error| error.to_string())??
    };
    let state = app.state::<AppState>();
    let config = Config { url, token: login.token, user_id: login.user_name.clone(), user_name: login.user_name };
    state.set_config(config).map_err(|error| format!("Could not save the settings: {error}"))?;
    *lock(&state.notice) = None;

    match windows::prepare_main(&app) {
        Ok(window) => {
            if let Some(session) = &web {
                if let Err(error) = windows::put_session(&window, &site, session) {
                    eprintln!("could not hand the session to the window: {error}");
                }
            }
            let _ = window.navigate(site);
        }
        Err(error) => eprintln!("could not prepare the window: {error}"),
    }
    runtime::start(&app);
    Ok(snapshot(&app))
}

/// Signs out of the app and of the site: both sessions end on the server, and the window is closed.
#[tauri::command]
pub async fn sign_out(app: AppHandle) -> Result<Snapshot, String> {
    let state = app.state::<AppState>();
    let config = state.config();
    runtime::stop(&app);

    let mut web_token = None;
    if let (Some(window), Ok(site)) = (app.get_webview_window(windows::MAIN), Url::parse(&config.url)) {
        // reading cookies must not be done on the thread of the window: this command runs on another
        let cookies = window.cookies_for_url(site).unwrap_or_default();
        web_token = cookies.iter().find(|cookie| cookie.name() == COOKIE).map(|cookie| cookie.value().to_owned());
        for cookie in cookies {
            let _ = window.delete_cookie(cookie);
        }
        let _ = window.destroy();
    }
    let (url, token) = (config.url.clone(), config.token.clone());
    let _ = tauri::async_runtime::spawn_blocking(move || {
        let _ = api::logout(&url, &token);
        if let Some(web) = web_token {
            let _ = api::logout(&url, &web);
        }
    })
    .await;

    state.set_config(Config { token: String::new(), ..config }).map_err(|error| format!("Could not save the settings: {error}"))?;
    runtime::set_server_state(&app, None);
    Ok(snapshot(&app))
}

/// Lets Clara work in a folder of this computer (chosen in the system's dialog).
#[tauri::command]
pub async fn add_folder(app: AppHandle) -> Result<Snapshot, String> {
    let mut dialog = app.dialog().file().set_title("Choose a folder Clara may work in");
    if let Some(window) = app.get_webview_window(windows::SETTINGS) {
        dialog = dialog.set_parent(&window);
    }
    if let Some(picked) = dialog.blocking_pick_folder() {
        let path = picked.into_path().map_err(|error| error.to_string())?;
        Registry::load_default().add(&path).map_err(|error| error.0)?;
    }
    Ok(snapshot(&app))
}

#[tauri::command]
pub async fn remove_folder(app: AppHandle, alias: String) -> Result<Snapshot, String> {
    Registry::load_default().remove(&alias).map_err(|error| format!("Could not save the folders: {error}"))?;
    Ok(snapshot(&app))
}

#[tauri::command]
pub async fn set_autostart(app: AppHandle, enabled: bool) -> Result<Snapshot, String> {
    tray::set_autostart(&app, enabled)?;
    Ok(snapshot(&app))
}

/// Goes to the site: its window comes forward and the settings page is put away.
#[tauri::command]
pub async fn open_site(app: AppHandle) {
    windows::open_main(&app);
    windows::close_settings(&app);
}
