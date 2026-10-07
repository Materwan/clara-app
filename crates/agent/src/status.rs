//! What the app says about the server, in its notifications and tooltip.

/// The state of the server as the app shows it.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ServerState {
    Running,
    Stopping,
    Down,
}

impl ServerState {
    /// The app's word for what the server says (`stopped` means it is not running any more).
    pub fn from_server(word: &str) -> Option<ServerState> {
        match word {
            "running" => Some(ServerState::Running),
            "stopping" => Some(ServerState::Stopping),
            "stopped" | "down" => Some(ServerState::Down),
            _ => None,
        }
    }

    /// The state in the tooltip of the icon, and at the end of a reminder.
    pub fn status(self) -> &'static str {
        match self {
            ServerState::Running => "Clara is running",
            ServerState::Stopping => "Clara is stopping",
            ServerState::Down => "Clara is not running",
        }
    }

    /// The notification shown when the server changes to this state.
    pub fn changed(self) -> &'static str {
        match self {
            ServerState::Running => "Clara is running again.",
            ServerState::Stopping => "Clara is stopping: she finishes what is running and takes nothing new.",
            ServerState::Down => "Clara is not running.",
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn stopped_is_down_and_unknown_words_are_ignored() {
        assert_eq!(ServerState::from_server("stopped"), Some(ServerState::Down));
        assert_eq!(ServerState::from_server("running"), Some(ServerState::Running));
        assert_eq!(ServerState::from_server("restarting?"), None);
    }
}
