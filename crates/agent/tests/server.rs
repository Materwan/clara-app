mod common;

use clara_agent::api::{self, Api, SIGNED_OUT};
use clara_agent::events::Event;
use clara_agent::folders::{LocalFolders, Registry};
use clara_agent::jobs::run_jobs;
use clara_agent::listen::{listen, Signal};
use clara_agent::status::ServerState;
use common::{serve, Reply};
use serde_json::{json, Value};
use std::fs;
use std::sync::atomic::{AtomicBool, AtomicUsize, Ordering};
use std::sync::{mpsc, Arc};
use std::thread;
use std::time::Duration;

const TOKEN: &str = "clu_fake-token";

fn api(fake: &common::Fake) -> Api {
    Api::new(&fake.url, TOKEN, "tester")
}

fn user(name: &str) -> Value {
    json!({"name": name, "is_admin": false})
}

#[test]
fn signing_in_on_the_surface_of_the_app_gives_a_token() {
    let fake = serve(|request| match request.route() {
        "/v1/auth/login" => Reply::Json(200, json!({"token": TOKEN, "user": user("Ana"), "surface": "app"})),
        _ => Reply::Json(404, json!({})),
    });
    let login = api::login(&format!("{}/", fake.url), " ana ", "secret").unwrap();
    assert_eq!((login.token.as_str(), login.user_name.as_str()), (TOKEN, "Ana"));
    let sent = fake.last();
    assert_eq!(sent.method, "POST");
    assert_eq!(
        (sent.body["username"].as_str(), sent.body["password"].as_str(), sent.body["surface"].as_str()),
        (Some("ana"), Some("secret"), Some("app"))
    );
    assert!(sent.body["device"].as_str().is_some_and(|device| !device.is_empty()));
    assert_eq!(sent.web_header, "");
}

#[test]
fn a_wrong_password_says_what_the_server_said() {
    let fake = serve(|_| Reply::Json(401, json!({"detail": "Wrong user name or password"})));
    let error = api::login(&fake.url, "ana", "bad").unwrap_err();
    assert_eq!((error.message.as_str(), error.status), ("Wrong user name or password", Some(401)));
}

#[test]
fn a_server_that_cannot_be_reached_is_said_so() {
    let error = api::login("http://127.0.0.1:1", "ana", "x").unwrap_err();
    assert_eq!(error.message, "Cannot reach the Clara server at http://127.0.0.1:1.");
    assert_eq!(error.status, None);
}

#[test]
fn signing_in_as_the_web_site_gives_its_session_cookie() {
    let fake = serve(|_| {
        Reply::JsonWith(
            200,
            json!({"user": user("Ana")}),
            "Set-Cookie",
            "clara_session=clu_web; Max-Age=3600; Path=/; HttpOnly; SameSite=strict".into(),
        )
    });
    let session = api::login_web(&fake.url, "ana", "secret").unwrap();
    assert_eq!((session.token.as_str(), session.max_age, session.secure), ("clu_web", Some(3600), false));
    let sent = fake.last();
    assert_eq!((sent.web_header.as_str(), sent.body["surface"].as_str()), ("1", Some("web")));
}

#[test]
fn a_site_that_sets_no_cookie_is_an_error_not_a_crash() {
    let fake = serve(|_| Reply::Json(200, json!({"user": user("Ana")})));
    assert!(api::login_web(&fake.url, "ana", "secret").unwrap_err().message.contains("no session"));
}

#[test]
fn signing_out_ends_the_session_and_forgives_one_that_was_over() {
    let fake = serve(|request| {
        if request.authorization == "Bearer clu_old" {
            Reply::Json(401, json!({"detail": "x"}))
        } else {
            Reply::Json(200, json!({"ok": true}))
        }
    });
    api::logout(&fake.url, "clu_a").unwrap();
    assert_eq!(fake.last().authorization, "Bearer clu_a");
    api::logout(&fake.url, "clu_old").unwrap();
}

#[test]
fn the_stream_is_asked_for_as_the_app_and_gives_its_events() {
    let fake = serve(|_| Reply::Events(vec![json!({"type": "server", "state": "running"}), json!({"type": "job", "job": 4})], false));
    let events: Vec<Value> = api(&fake).notifications().unwrap().map(Result::unwrap).collect();
    assert_eq!(events.len(), 2);
    assert_eq!(events[1]["type"], "job");
    let sent = fake.last();
    assert_eq!(sent.route(), "/v1/notifications/stream");
    assert_eq!(sent.authorization, format!("Bearer {TOKEN}"));
    assert_eq!((sent.query("surface").as_deref(), sent.query("user_id").as_deref()), (Some("app"), Some("tester")));
}

#[test]
fn a_token_the_server_does_not_know_means_signed_out() {
    let fake = serve(|_| Reply::Json(401, json!({"detail": "Invalid token"})));
    let error = api(&fake).notifications().err().unwrap();
    assert_eq!((error.message.as_str(), error.status), (SIGNED_OUT, Some(401)));
    let shared = Api::new(&fake.url, "shared-client-token", "tester").notifications().err().unwrap();
    assert!(shared.message.starts_with("Clara server: Invalid token (HTTP 401)"), "{}", shared.message);
}

#[test]
fn the_jobs_are_asked_for_as_this_computer_and_answered() {
    let fake = serve(|request| match request.route() {
        "/v1/integrations/jobs" => {
            Reply::Json(200, json!({"jobs": [{"id": 5, "op": "list", "alias": "docs", "args": {"path": "src"}, "created_at": "x"}]}))
        }
        _ => Reply::Json(200, json!({"ok": true})),
    });
    let jobs = api(&fake).computer_jobs("pc-1").unwrap();
    assert_eq!((jobs[0].id, jobs[0].op.as_str(), jobs[0].alias.as_str(), jobs[0].args["path"].as_str()), (5, "list", "docs", Some("src")));
    assert_eq!(fake.last().query("device").as_deref(), Some("pc-1"));
    api(&fake).finish_job(5, true, "done").unwrap();
    let sent = fake.last();
    assert_eq!(sent.route(), "/v1/integrations/jobs/5/result");
    assert_eq!(sent.body, json!({"surface": "app", "user_id": "tester", "ok": true, "text": "done"}));
}

#[test]
fn the_jobs_are_done_and_each_one_is_answered() {
    let dir = tempfile::tempdir().unwrap();
    let docs = dir.path().join("docs");
    fs::create_dir_all(&docs).unwrap();
    fs::write(docs.join("notes.md"), "one\ntwo\nthree\n").unwrap();
    let mut registry = Registry::load(&dir.path().join("f.json"));
    let alias = registry.add(&docs).unwrap();
    let device = registry.device.clone();
    let jobs = json!({"jobs": [
        {"id": 1, "op": "read", "alias": alias, "args": {"path": "notes.md"}},
        {"id": 2, "op": "write", "alias": alias, "args": {"path": "../x", "content": "y", "mode": "create"}},
        {"id": 3, "op": "list", "alias": "never-added", "args": {}},
        {"id": 99, "op": "list", "alias": alias, "args": {}},
    ]});
    let fake = serve(move |request| match (request.method.as_str(), request.route()) {
        ("GET", "/v1/integrations/jobs") => Reply::Json(200, jobs.clone()),
        (_, "/v1/integrations/jobs/99/result") => Reply::Json(409, json!({"detail": "This job is already finished"})),
        _ => Reply::Json(200, json!({"ok": true})),
    });
    assert_eq!(run_jobs(&api(&fake), &LocalFolders::new(registry)).unwrap(), 3); // the one the server no longer wants is not counted
    let sent = fake.seen();
    assert_eq!(sent[0].query("device").as_deref(), Some(device.as_str()));
    let answer = |id: i64| sent.iter().find(|r| r.route() == format!("/v1/integrations/jobs/{id}/result")).unwrap().body.clone();
    assert_eq!(answer(1)["ok"], true);
    assert!(answer(1)["text"].as_str().unwrap().contains("three"));
    assert_eq!((answer(2)["ok"].as_bool(), answer(2)["text"].as_str()), (Some(false), Some("A path may not go up (..).")));
    assert!(!dir.path().join("x").exists());
    assert!(answer(3)["text"].as_str().unwrap().contains("not on this computer"));
}

fn collect(api: Api, pause: Duration) -> (mpsc::Receiver<Signal>, Arc<AtomicBool>, thread::JoinHandle<()>) {
    let (tx, rx) = mpsc::channel();
    let stop = Arc::new(AtomicBool::new(false));
    let handle = {
        let stop = stop.clone();
        thread::spawn(move || listen(&api, &stop, pause, &mut |signal| drop(tx.send(signal))))
    };
    (rx, stop, handle)
}

fn next(rx: &mpsc::Receiver<Signal>) -> Signal {
    rx.recv_timeout(Duration::from_secs(5)).expect("a signal in time")
}

#[test]
fn the_listener_tells_what_arrives_and_opens_the_stream_again_when_it_drops() {
    let connections = Arc::new(AtomicUsize::new(0));
    let fake = {
        let connections = connections.clone();
        serve(move |_| match connections.fetch_add(1, Ordering::SeqCst) {
            0 => Reply::Events(
                vec![
                    json!({"type": "reminder", "text": "pay", "message": "Pay!", "due_at": "a", "fired_at": "b"}),
                    json!({"type": "server", "state": "stopping"}),
                ],
                false,
            ),
            _ => Reply::Events(vec![json!({"type": "job", "job": 1})], true),
        })
    };
    let (rx, stop, handle) = collect(api(&fake), Duration::from_millis(30));
    assert_eq!(next(&rx), Signal::Connected);
    assert!(matches!(next(&rx), Signal::Event(Event::Reminder { message, .. }) if message == "Pay!"));
    assert_eq!(next(&rx), Signal::Event(Event::Server(ServerState::Stopping)));
    assert_eq!(next(&rx), Signal::Disconnected); // the server ended the stream...
    assert_eq!(next(&rx), Signal::Connected); // ...and the listener came back
    assert_eq!(next(&rx), Signal::Event(Event::Job));
    stop.store(true, Ordering::Relaxed);
    drop(fake); // ends the stream that is held open
    handle.join().unwrap();
}

#[test]
fn a_server_that_is_down_is_tried_again_until_it_answers() {
    let attempts = Arc::new(AtomicUsize::new(0));
    let fake = {
        let attempts = attempts.clone();
        serve(move |_| {
            if attempts.fetch_add(1, Ordering::SeqCst) < 2 {
                Reply::Json(503, json!({"detail": "starting"}))
            } else {
                Reply::Events(vec![], true)
            }
        })
    };
    let (rx, stop, handle) = collect(api(&fake), Duration::from_millis(30));
    assert_eq!(next(&rx), Signal::Disconnected);
    assert_eq!(next(&rx), Signal::Disconnected);
    assert_eq!(next(&rx), Signal::Connected);
    stop.store(true, Ordering::Relaxed);
    drop(fake);
    handle.join().unwrap();
}

#[test]
fn a_token_that_is_refused_ends_the_listener_instead_of_hammering_the_server() {
    let fake = serve(|_| Reply::Json(401, json!({"detail": "Invalid token"})));
    let (rx, _stop, handle) = collect(api(&fake), Duration::from_millis(30));
    assert_eq!(next(&rx), Signal::Disconnected);
    assert_eq!(next(&rx), Signal::Rejected(SIGNED_OUT.into()));
    handle.join().unwrap(); // it ended by itself
    assert_eq!(fake.count("/v1/notifications/stream"), 1);
}
