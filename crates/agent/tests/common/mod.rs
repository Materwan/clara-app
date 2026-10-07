//! A small fake Clara server that speaks the real protocol, over plain http on a free port.
#![allow(dead_code)]

use serde_json::Value;
use std::io::Read;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex};
use std::thread;
use std::time::Duration;
use tiny_http::{Header, Response, Server, StatusCode};

#[derive(Debug, Clone)]
pub struct Recorded {
    pub method: String,
    /// The path with its query.
    pub path: String,
    pub authorization: String,
    pub web_header: String,
    pub body: Value,
}

impl Recorded {
    pub fn route(&self) -> &str {
        self.path.split('?').next().unwrap_or("")
    }

    /// A parameter of the query (not decoded further than `%20` and `+`).
    pub fn query(&self, name: &str) -> Option<String> {
        let query = self.path.split_once('?')?.1;
        query.split('&').find_map(|pair| {
            let (key, value) = pair.split_once('=')?;
            (key == name).then(|| value.replace('+', " ").replace("%20", " ").replace("%3A", ":"))
        })
    }
}

pub enum Reply {
    Json(u16, Value),
    /// A response with a header too (a cookie).
    JsonWith(u16, Value, &'static str, String),
    /// Server-Sent Events: these events, then the connection stays open (`hold`) or ends.
    Events(Vec<Value>, bool),
}

pub struct Fake {
    pub url: String,
    pub requests: Arc<Mutex<Vec<Recorded>>>,
    closed: Arc<AtomicBool>,
}

impl Fake {
    pub fn seen(&self) -> Vec<Recorded> {
        self.requests.lock().unwrap().clone()
    }

    pub fn last(&self) -> Recorded {
        self.seen().pop().expect("a request was made")
    }

    pub fn count(&self, route: &str) -> usize {
        self.seen().iter().filter(|r| r.route() == route).count()
    }
}

impl Drop for Fake {
    fn drop(&mut self) {
        self.closed.store(true, Ordering::Relaxed); // lets the streams held open end
    }
}

struct Stream {
    pending: Vec<u8>,
    position: usize,
    hold: bool,
    closed: Arc<AtomicBool>,
}

impl Read for Stream {
    fn read(&mut self, buffer: &mut [u8]) -> std::io::Result<usize> {
        loop {
            if self.position < self.pending.len() {
                let n = buffer.len().min(self.pending.len() - self.position);
                buffer[..n].copy_from_slice(&self.pending[self.position..self.position + n]);
                self.position += n;
                return Ok(n);
            }
            if !self.hold || self.closed.load(Ordering::Relaxed) {
                return Ok(0);
            }
            thread::sleep(Duration::from_millis(20));
        }
    }
}

fn header(name: &str, value: &str) -> Header {
    Header::from_bytes(name.as_bytes(), value.as_bytes()).unwrap()
}

/// Serves `handler(request) -> reply` until the `Fake` is dropped.
pub fn serve(handler: impl Fn(&Recorded) -> Reply + Send + Sync + 'static) -> Fake {
    let server = Server::http("127.0.0.1:0").expect("a free port");
    let url = format!("http://{}", server.server_addr().to_ip().unwrap());
    let requests = Arc::new(Mutex::new(vec![]));
    let closed = Arc::new(AtomicBool::new(false));
    let handler = Arc::new(handler);
    {
        let (requests, closed) = (requests.clone(), closed.clone());
        thread::spawn(move || {
            let server = Arc::new(server);
            while !closed.load(Ordering::Relaxed) {
                let Ok(Some(mut request)) = server.recv_timeout(Duration::from_millis(50)) else { continue };
                let (requests, closed, handler) = (requests.clone(), closed.clone(), handler.clone());
                thread::spawn(move || {
                    let mut text = String::new();
                    let _ = request.as_reader().read_to_string(&mut text);
                    let find = |name: &str| {
                        request
                            .headers()
                            .iter()
                            .find(|h| h.field.as_str().as_str().eq_ignore_ascii_case(name))
                            .map(|h| h.value.to_string())
                            .unwrap_or_default()
                    };
                    let recorded = Recorded {
                        method: request.method().to_string(),
                        path: request.url().to_owned(),
                        authorization: find("Authorization"),
                        web_header: find("X-Clara-Web"),
                        body: serde_json::from_str(&text).unwrap_or(Value::Null),
                    };
                    requests.lock().unwrap().push(recorded.clone());
                    let _ = match handler(&recorded) {
                        Reply::Json(status, body) => request.respond(
                            Response::from_string(body.to_string())
                                .with_status_code(status)
                                .with_header(header("Content-Type", "application/json")),
                        ),
                        Reply::JsonWith(status, body, name, value) => request.respond(
                            Response::from_string(body.to_string())
                                .with_status_code(status)
                                .with_header(header("Content-Type", "application/json"))
                                .with_header(header(name, &value)),
                        ),
                        Reply::Events(events, hold) => {
                            // tiny_http buffers what it streams until 8 KB are there: the events are followed by a comment
                            // (SSE ignores it) big enough to make it send the headers and the events at once
                            let mut pending = vec![];
                            for event in events {
                                pending.extend(format!("data: {event}\n\n").bytes());
                            }
                            pending.extend(format!(": keepalive {}\n\n", " ".repeat(20_000)).bytes());
                            let stream = Stream { pending, position: 0, hold, closed };
                            request.respond(Response::new(
                                StatusCode(200),
                                vec![header("Content-Type", "text/event-stream")],
                                stream,
                                None,
                                None,
                            ))
                        }
                    };
                });
            }
        });
    }
    Fake { url, requests, closed }
}
