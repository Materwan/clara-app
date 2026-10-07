//! Listening to the server: the stream stays open, and is opened again when it drops.

use std::sync::atomic::{AtomicBool, Ordering};
use std::thread;
use std::time::{Duration, Instant};

use crate::api::{Api, ApiError};
use crate::events::{self, Event};

pub const RECONNECT: Duration = Duration::from_secs(5);
/// The server refuses an address for this long after too many wrong tokens (429).
pub const BLOCKED: Duration = Duration::from_secs(300);

/// What `listen` tells.
#[derive(Debug, Clone, PartialEq)]
pub enum Signal {
    /// The stream is open: the server is there.
    Connected,
    /// The server cannot be reached (or the connection broke without a word).
    Disconnected,
    Event(Event),
    /// The server does not accept the token any more: trying again would only get this address blocked.
    Rejected(String),
}

/// Holds the stream open until `stop` is set (it is looked at each time something arrives, so within the 15 s of the
/// server's keepalive) or the token is refused.
pub fn listen(api: &Api, stop: &AtomicBool, pause: Duration, tell: &mut dyn FnMut(Signal)) {
    while !stop.load(Ordering::Relaxed) {
        let wait = match api.notifications() {
            Ok(stream) => {
                tell(Signal::Connected);
                let mut broke = false;
                for item in stream {
                    if stop.load(Ordering::Relaxed) {
                        return;
                    }
                    match item {
                        Ok(value) => tell(Signal::Event(events::parse(&value))),
                        Err(_) => {
                            broke = true;
                            break;
                        }
                    }
                }
                let _ = broke; // dropped or ended by the server: the same, try again
                pause
            }
            Err(ApiError { status: Some(401), message }) => {
                if !stop.load(Ordering::Relaxed) {
                    tell(Signal::Disconnected);
                    tell(Signal::Rejected(message));
                }
                return;
            }
            Err(ApiError { status, .. }) => {
                if status == Some(429) {
                    BLOCKED.max(pause)
                } else {
                    pause
                }
            }
        };
        if !stop.load(Ordering::Relaxed) {
            tell(Signal::Disconnected);
        }
        sleep_unless(stop, wait);
    }
}

fn sleep_unless(stop: &AtomicBool, total: Duration) {
    let until = Instant::now() + total;
    while !stop.load(Ordering::Relaxed) {
        let left = until.saturating_duration_since(Instant::now());
        if left.is_zero() {
            return;
        }
        thread::sleep(left.min(Duration::from_millis(100)));
    }
}
