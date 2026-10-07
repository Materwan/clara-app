//! What the Clara desktop app does on this computer, apart from showing the web site:
//!
//! * [`config`]: the server address and the sign-in kept in `%APPDATA%\clara-app\config.json`;
//! * [`api`]: the few routes of the Clara server the app calls (sign in, the event stream, the jobs);
//! * [`listen`] and [`events`]: the stream of reminders, notifications and jobs, and the words they become;
//! * [`folders`], [`jobs`] and [`documents`]: the folders of this computer Clara may work in, and the code that does it;
//! * [`navigation`]: where the window of the site may go.
//!
//! No window, no tray, no Tauri here: the shell (`src-tauri`) only wires these to the screen.

pub mod api;
pub mod config;
pub mod documents;
pub mod events;
pub mod folders;
pub mod icon;
pub mod jobs;
pub mod listen;
pub mod navigation;
pub mod status;

/// What this client is called on the server (`CLARA_CLIENT_SURFACES`); the site's own is `web`.
pub const SURFACE: &str = "app";
pub const APP_NAME: &str = "Clara";
