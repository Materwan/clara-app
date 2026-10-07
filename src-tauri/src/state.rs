//! What the app shares between its windows, its tray and the thread that listens to the server.

use clara_agent::config::Config;
use clara_agent::jobs::Gate;
use clara_agent::status::ServerState;
use std::sync::atomic::AtomicBool;
use std::sync::{Arc, Mutex, MutexGuard};

/// A lock that a panic elsewhere does not make unusable: the app lives in the tray for days.
pub fn lock<T>(mutex: &Mutex<T>) -> MutexGuard<'_, T> {
    mutex.lock().unwrap_or_else(|poisoned| poisoned.into_inner())
}

pub struct AppState {
    config: Mutex<Config>,
    /// Stops the thread that listens to the server (each listener has its own flag).
    pub stop: Mutex<Option<Arc<AtomicBool>>>,
    /// What the server says it is doing; `None` until it says.
    pub server: Mutex<Option<ServerState>>,
    /// Why the app asks to sign in again (shown on the settings page).
    pub notice: Mutex<Option<String>>,
    /// One run of the folder jobs at a time.
    pub gate: Arc<Gate>,
}

impl AppState {
    pub fn new(config: Config) -> AppState {
        AppState {
            config: Mutex::new(config),
            stop: Mutex::new(None),
            server: Mutex::new(None),
            notice: Mutex::new(None),
            gate: Arc::new(Gate::default()),
        }
    }

    pub fn config(&self) -> Config {
        lock(&self.config).clone()
    }

    /// Keeps the settings, here and on the disk.
    pub fn set_config(&self, config: Config) -> std::io::Result<()> {
        config.save_default()?;
        *lock(&self.config) = config;
        Ok(())
    }
}
