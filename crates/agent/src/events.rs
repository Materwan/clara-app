//! What the server sends on its stream, and the words a notification of the desktop is made of.

use chrono::{DateTime, Local, Utc};
use serde_json::Value;

use crate::status::ServerState;
use crate::APP_NAME;

/// A reminder shown this long after it fired is announced as missed.
pub const LATE_SECONDS: i64 = 120;

/// The time `describe_*` compare with.
pub type Now = DateTime<Utc>;

pub fn now() -> Now {
    Utc::now()
}

#[derive(Debug, Clone, PartialEq)]
pub enum Event {
    /// A reminder of the user's own came due.
    Reminder {
        text: String,
        message: String,
        due_at: String,
        fired_at: String,
    },
    /// From Clara, the server, or another client.
    Notification {
        title: String,
        text: String,
        sent_at: String,
        conversation: Option<String>,
    },
    /// A request for permission nobody answered in time.
    Approval {
        summary: String,
        resource: String,
    },
    ApprovalResolved,
    /// Clara asks something of a folder of this computer: the jobs are to be fetched.
    Job,
    Server(ServerState),
    Other,
}

fn text(event: &Value, name: &str) -> String {
    event.get(name).and_then(Value::as_str).unwrap_or("").to_owned()
}

pub fn parse(event: &Value) -> Event {
    match event.get("type").and_then(Value::as_str).unwrap_or("") {
        "reminder" => Event::Reminder {
            text: text(event, "text"),
            message: text(event, "message"),
            due_at: text(event, "due_at"),
            fired_at: text(event, "fired_at"),
        },
        "notification" => Event::Notification {
            title: text(event, "title"),
            text: text(event, "text"),
            sent_at: text(event, "sent_at"),
            conversation: event.get("conversation").and_then(Value::as_str).filter(|c| !c.is_empty()).map(str::to_owned),
        },
        "approval" => Event::Approval {
            summary: [text(event, "summary"), text(event, "text")].into_iter().find(|s| !s.is_empty()).unwrap_or_default(),
            resource: text(event, "resource"),
        },
        "approval_resolved" => Event::ApprovalResolved,
        "job" => Event::Job,
        "server" => ServerState::from_server(&text(event, "state")).map_or(Event::Other, Event::Server),
        _ => Event::Other,
    }
}

/// A notification of the desktop.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Toast {
    pub title: String,
    pub body: String,
}

impl Toast {
    fn new(title: impl Into<String>, parts: &[&str]) -> Toast {
        Toast { title: title.into(), body: parts.iter().filter(|part| !part.is_empty()).copied().collect::<Vec<_>>().join("\n") }
    }
}

fn parse_time(iso: &str) -> Option<DateTime<Utc>> {
    DateTime::parse_from_rfc3339(iso).ok().map(|time| time.with_timezone(&Utc))
}

fn local(time: DateTime<Utc>) -> String {
    time.with_timezone(&Local).format("%Y-%m-%d %H:%M").to_string()
}

/// The notification of a reminder: what Clara wrote for it (the reminder's own text if she could not), a note when it
/// is late, and the state of the server (a reminder reached us, so it is there).
pub fn describe_reminder(text: &str, message: &str, due_at: &str, fired_at: &str, now: DateTime<Utc>, server: ServerState) -> Toast {
    let missed = parse_time(fired_at).is_some_and(|fired| (now - fired).num_seconds() > LATE_SECONDS);
    let title = format!("{APP_NAME} reminder{}", if missed { " (missed)" } else { "" });
    let detail = match (missed, parse_time(due_at)) {
        (true, Some(due)) => format!("It was due {}.", local(due)),
        _ => String::new(),
    };
    let body = if message.is_empty() { text } else { message };
    Toast::new(title, &[body, &detail, server.status()])
}

/// The notification of what the server or Clara sent.
pub fn describe_notification(title: &str, text: &str, sent_at: &str, now: DateTime<Utc>) -> Toast {
    let detail = match parse_time(sent_at) {
        Some(sent) if (now - sent).num_seconds() > LATE_SECONDS => format!("Sent {}.", local(sent)),
        _ => String::new(),
    };
    Toast::new(if title.is_empty() { APP_NAME } else { title }, &[text, &detail])
}

/// The notification of a request for permission nobody answered in time.
pub fn describe_approval(summary: &str, resource: &str) -> Toast {
    let place = if resource.is_empty() { String::new() } else { format!("On {resource}") };
    Toast::new("Clara needs your permission", &[summary, &place])
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    fn at(iso: &str) -> DateTime<Utc> {
        parse_time(iso).unwrap()
    }

    #[test]
    fn events_are_told_apart() {
        assert_eq!(parse(&json!({"type": "job", "job": 3})), Event::Job);
        assert_eq!(parse(&json!({"type": "server", "state": "stopped"})), Event::Server(ServerState::Down));
        assert_eq!(parse(&json!({"type": "server", "state": "???"})), Event::Other);
        assert_eq!(parse(&json!({"type": "something new"})), Event::Other);
        assert_eq!(
            parse(&json!({"type": "approval", "summary": "Write notes.md", "resource": "docs"})),
            Event::Approval { summary: "Write notes.md".into(), resource: "docs".into() }
        );
        match parse(
            &json!({"type": "notification", "title": "T", "text": "x", "sent_at": "2026-10-07T06:00:00+00:00", "conversation": "app:u:1"}),
        ) {
            Event::Notification { conversation, .. } => assert_eq!(conversation.as_deref(), Some("app:u:1")),
            other => panic!("{other:?}"),
        }
    }

    #[test]
    fn a_reminder_is_what_clara_wrote_and_the_state_of_the_server() {
        let toast = describe_reminder(
            "pay",
            "Time to pay the rent!",
            "2026-10-07T08:00:00+00:00",
            "2026-10-07T08:00:01+00:00",
            at("2026-10-07T08:00:05+00:00"),
            ServerState::Running,
        );
        assert_eq!(toast.title, "Clara reminder");
        assert_eq!(toast.body, "Time to pay the rent!\nClara is running");
    }

    #[test]
    fn without_her_words_the_reminders_own_text_is_used_and_a_late_one_is_marked_missed() {
        let toast = describe_reminder(
            "pay",
            "",
            "2026-10-07T08:00:00+00:00",
            "2026-10-07T08:00:01+00:00",
            at("2026-10-07T09:00:00+00:00"),
            ServerState::Stopping,
        );
        assert_eq!(toast.title, "Clara reminder (missed)");
        let lines: Vec<&str> = toast.body.lines().collect();
        assert_eq!(lines[0], "pay");
        assert!(lines[1].starts_with("It was due 2026-10-07"));
        assert_eq!(lines[2], "Clara is stopping");
    }

    #[test]
    fn a_notification_says_when_it_was_sent_only_if_it_is_late() {
        let now = at("2026-10-07T09:00:00+00:00");
        let fresh = describe_notification("", "Answer ready", "2026-10-07T08:59:30+00:00", now);
        assert_eq!((fresh.title.as_str(), fresh.body.as_str()), ("Clara", "Answer ready"));
        let late = describe_notification("Task", "Done", "2026-10-07T08:00:00+00:00", now);
        assert!(late.body.starts_with("Done\nSent 2026-10-07"));
    }

    #[test]
    fn a_request_for_permission_says_what_and_where() {
        assert_eq!(describe_approval("Replace notes.md", "docs").body, "Replace notes.md\nOn docs");
        assert_eq!(describe_approval("Replace notes.md", "").body, "Replace notes.md");
    }
}
