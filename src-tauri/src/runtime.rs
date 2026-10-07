//! The part of the app that never sleeps: it listens to the server, and turns what it hears into notifications and
//! work on this computer's folders.

use clara_agent::api::Api;
use clara_agent::events::{self, Event, Toast};
use clara_agent::folders::{LocalFolders, Registry};
use clara_agent::jobs::run_jobs;
use clara_agent::listen::{listen, Signal, RECONNECT};
use clara_agent::status::ServerState;
use clara_agent::APP_NAME;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Arc;
use std::thread;
use tauri::{AppHandle, Manager};
use tauri_plugin_notification::NotificationExt;

use crate::state::{lock, AppState};
use crate::{tray, windows};

/// Starts listening to the server as the signed-in user (and stops the listener of before, if any).
pub fn start(app: &AppHandle) {
    stop(app);
    let state = app.state::<AppState>();
    set_server_state(app, None);
    let config = state.config();
    if !config.ready() {
        return;
    }
    let stop_flag = Arc::new(AtomicBool::new(false));
    *lock(&state.stop) = Some(stop_flag.clone());
    let app = app.clone();
    thread::spawn(move || {
        let api = Api::from_config(&config);
        listen(&api, &stop_flag, RECONNECT, &mut |signal| heard(&app, signal));
    });
}

pub fn stop(app: &AppHandle) {
    if let Some(flag) = lock(&app.state::<AppState>().stop).take() {
        flag.store(true, Ordering::Relaxed);
    }
}

fn heard(app: &AppHandle, signal: Signal) {
    match signal {
        Signal::Connected => do_jobs(app), // what was asked while the app was off is waiting
        Signal::Disconnected => set_server_state(app, Some(ServerState::Down)),
        Signal::Rejected(reason) => rejected(app, &reason),
        Signal::Event(event) => match event {
            Event::Server(state) => set_server_state(app, Some(state)),
            Event::Job => do_jobs(app),
            Event::Reminder { text, message, due_at, fired_at } => {
                // a reminder reached us, so the server is there
                let server = (*lock(&app.state::<AppState>().server)).unwrap_or(ServerState::Running);
                notify(app, &events::describe_reminder(&text, &message, &due_at, &fired_at, events::now(), server));
            }
            Event::Notification { title, text, sent_at, conversation } => {
                // one about a conversation, while the window is in front, is for the page to show, not a pop-up
                if conversation.is_none() || !windows::main_is_in_front(app) {
                    notify(app, &events::describe_notification(&title, &text, &sent_at, events::now()));
                }
            }
            Event::Approval { summary, resource } => notify(app, &events::describe_approval(&summary, &resource)),
            Event::ApprovalResolved | Event::Other => {}
        },
    }
}

/// Shows the state of the server everywhere, and says so when it changes.
pub fn set_server_state(app: &AppHandle, new: Option<ServerState>) {
    let state = app.state::<AppState>();
    let before = std::mem::replace(&mut *lock(&state.server), new);
    if before == new {
        return;
    }
    tray::show_state(app, new);
    windows::tell_settings(app);
    if let Some(now) = new {
        if !(now == ServerState::Running && before.is_none()) {
            // nothing to say when the app finds all well
            notify(app, &Toast { title: APP_NAME.into(), body: now.changed().into() });
        }
    }
}

/// The server no longer knows this sign-in: ask for the password instead of retrying with it.
fn rejected(app: &AppHandle, reason: &str) {
    let state = app.state::<AppState>();
    let mut config = state.config();
    config.token.clear();
    if let Err(error) = state.set_config(config) {
        eprintln!("could not save the settings: {error}");
    }
    *lock(&state.notice) = Some(reason.to_owned());
    stop(app);
    set_server_state(app, None);
    notify(app, &Toast { title: APP_NAME.into(), body: reason.to_owned() });
    windows::open_settings(app);
}

/// Clara asked something of a folder of this computer: do it here, and say how it went.
fn do_jobs(app: &AppHandle) {
    let app = app.clone();
    thread::spawn(move || {
        let state = app.state::<AppState>();
        let gate = state.gate.clone();
        gate.run(|| {
            let registry = Registry::load_default();
            if registry.folders.is_empty() {
                return;
            }
            let api = Api::from_config(&state.config());
            if let Err(error) = run_jobs(&api, &LocalFolders::new(registry)) {
                eprintln!("the jobs of the folders: {error}");
            }
        });
    });
}

pub fn notify(app: &AppHandle, toast: &Toast) {
    if let Err(error) = app.notification().builder().title(&toast.title).body(&toast.body).show() {
        eprintln!("could not show a notification: {error}");
    }
}
