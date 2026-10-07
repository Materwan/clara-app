//! The two windows: the site of the Clara server (the app's real window) and the settings page of the app itself
//! (signing in, the folders of this computer, starting with Windows).

use clara_agent::api::{WebSession, COOKIE};
use clara_agent::navigation;
use tauri::webview::cookie::{time::Duration, Cookie, SameSite};
use tauri::webview::NewWindowResponse;
use tauri::{AppHandle, Emitter, Manager, Url, WebviewUrl, WebviewWindow, WebviewWindowBuilder, WindowEvent};
use tauri_plugin_opener::OpenerExt;

use crate::state::AppState;

pub const MAIN: &str = "main";
pub const SETTINGS: &str = "settings";

/// The server's address as it is now (it can change when the user signs in on another server).
fn site(app: &AppHandle) -> Option<Url> {
    Url::parse(app.state::<AppState>().config().url.trim()).ok()
}

/// Opens an address in the browser of the computer.
fn open_outside(app: &AppHandle, url: &Url) {
    if navigation::may_open_outside(url) {
        let _ = app.opener().open_url(url.as_str(), None::<&str>);
    }
}

fn bring_forward(window: &WebviewWindow) {
    let _ = window.unminimize();
    let _ = window.show();
    let _ = window.set_focus();
}

fn site_window(app: &AppHandle, url: WebviewUrl, visible: bool) -> tauri::Result<WebviewWindow> {
    let (for_navigation, for_new_window) = (app.clone(), app.clone());
    let window = WebviewWindowBuilder::new(app, MAIN, url)
        .title("Clara")
        .inner_size(1100.0, 760.0)
        .min_inner_size(380.0, 520.0)
        .center()
        .visible(visible)
        .on_navigation(move |url| {
            if navigation::allowed(url, site(&for_navigation).as_ref()) {
                return true;
            }
            open_outside(&for_navigation, url);
            false
        })
        .on_new_window(move |url, _features| {
            open_outside(&for_new_window, &url);
            NewWindowResponse::Deny // a window.open that is not allowed leaves the page to show its own link instead
        })
        .build()?;
    // closing the window puts it away: the app goes on in the tray, where reminders reach you
    let hidden = window.clone();
    window.on_window_event(move |event| {
        if let WindowEvent::CloseRequested { api, .. } = event {
            api.prevent_close();
            let _ = hidden.hide();
        }
    });
    Ok(window)
}

/// Creates the window of the site (hidden, on a page that waits) so that a session can be put in it before it loads.
pub fn prepare_main(app: &AppHandle) -> tauri::Result<WebviewWindow> {
    match app.get_webview_window(MAIN) {
        Some(window) => Ok(window),
        None => site_window(app, WebviewUrl::App("loading.html".into()), false),
    }
}

/// Puts the session of the web site in the window, so that the site does not ask for the password again.
pub fn put_session(window: &WebviewWindow, site: &Url, session: &WebSession) -> tauri::Result<()> {
    let mut cookie = Cookie::build((COOKIE, session.token.clone()))
        .path("/")
        .http_only(true)
        .secure(session.secure || site.scheme() == "https")
        .same_site(SameSite::Strict);
    if let Some(host) = site.host_str() {
        cookie = cookie.domain(host.to_owned());
    }
    if let Some(seconds) = session.max_age {
        cookie = cookie.max_age(Duration::seconds(seconds));
    }
    window.set_cookie(cookie.build())
}

/// Shows the site, starting its window if there is none. Without a sign-in, the settings page asks for one.
pub fn open_main(app: &AppHandle) {
    let config = app.state::<AppState>().config();
    let Some(site) = site(app).filter(|_| config.ready()) else { return open_settings(app) };
    match app.get_webview_window(MAIN) {
        Some(window) => bring_forward(&window),
        None => {
            if let Err(error) = site_window(app, WebviewUrl::External(site), true) {
                eprintln!("could not open the window: {error}");
            }
        }
    }
}

/// Starts the window of the site when the app starts; hidden when Windows started the app at login.
pub fn start_main(app: &AppHandle, visible: bool) {
    let config = app.state::<AppState>().config();
    match site(app).filter(|_| config.ready()) {
        Some(site) => {
            if let Err(error) = site_window(app, WebviewUrl::External(site), visible) {
                eprintln!("could not open the window: {error}");
            }
        }
        None => open_settings(app),
    }
}

/// A left click on the icon: show the window, or put it away if it is in front.
pub fn toggle_main(app: &AppHandle) {
    match app.get_webview_window(MAIN) {
        Some(window) if window.is_visible().unwrap_or(false) && !window.is_minimized().unwrap_or(false) => {
            let _ = window.hide();
        }
        _ => open_main(app),
    }
}

/// One of the pages of the site (`tasks`...): the site's own address (`#/tasks`) says which.
pub fn open_page(app: &AppHandle, page: &str) {
    open_main(app);
    if let Some(window) = app.get_webview_window(MAIN) {
        let _ = window.eval(format!("if (location.hash !== '#/{page}') location.hash = '#/{page}';"));
    }
}

/// Is the window of the site visible and the one the user is working in?
pub fn main_is_in_front(app: &AppHandle) -> bool {
    app.get_webview_window(MAIN).is_some_and(|w| w.is_visible().unwrap_or(false) && w.is_focused().unwrap_or(false))
}

pub fn open_settings(app: &AppHandle) {
    if let Some(window) = app.get_webview_window(SETTINGS) {
        bring_forward(&window);
        tell_settings(app);
        return;
    }
    let built = WebviewWindowBuilder::new(app, SETTINGS, WebviewUrl::App("index.html".into()))
        .title("Clara settings")
        .inner_size(560.0, 700.0)
        .min_inner_size(420.0, 520.0)
        .center()
        .build();
    match built {
        Ok(window) => bring_forward(&window),
        Err(error) => eprintln!("could not open the settings: {error}"),
    }
}

pub fn close_settings(app: &AppHandle) {
    if let Some(window) = app.get_webview_window(SETTINGS) {
        let _ = window.destroy();
    }
}

/// The settings page reads its state again (the server changed state, a sign-in ended...).
pub fn tell_settings(app: &AppHandle) {
    let _ = app.emit_to(SETTINGS, "state-changed", ());
}
