"""What the app says about the server, in its notifications, tooltip and window."""

from __future__ import annotations

STATUS = {
    "running": "Clara is running",
    "stopping": "Clara is stopping",
    "down": "Clara is not running",
}

# The notification shown when the server changes to that state
CHANGED = {
    "stopping": "Clara is stopping: she finishes what is running and takes nothing new.",
    "down": "Clara is not running.",
    "running": "Clara is running again.",
}


def from_server(state: str) -> str:
    """The app's word for what the server says ("stopped" means it is not running any more)."""
    return "down" if state == "stopped" else state
