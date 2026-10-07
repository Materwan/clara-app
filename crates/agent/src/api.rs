//! The Clara server, seen from the app: the few routes it calls. Blocking: it is meant for worker threads, never for
//! the thread of the window.

use attohttpc::{Method, RequestBuilder, Response};
use serde::Deserialize;
use serde_json::{json, Value};
use std::fmt;
use std::io::{BufRead, BufReader};
use std::time::Duration;

use crate::SURFACE;

const CONNECT_TIMEOUT: Duration = Duration::from_secs(10);
const CALL_TIMEOUT: Duration = Duration::from_secs(30);
/// The server sends a keepalive every 15 s: a stream silent for longer than this is dead.
const STREAM_READ_TIMEOUT: Duration = Duration::from_secs(60);

pub const SIGNED_OUT: &str = "You were signed out of the Clara server. Sign in again.";

/// The server could not be reached, or refused the request. `message` is fit to show; `status` is the HTTP status of a
/// refusal (`None` when the server could not be reached).
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ApiError {
    pub message: String,
    pub status: Option<u16>,
}

impl ApiError {
    fn new(message: impl Into<String>, status: Option<u16>) -> ApiError {
        ApiError { message: message.into(), status }
    }
}

impl fmt::Display for ApiError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(&self.message)
    }
}

impl std::error::Error for ApiError {}

fn transport(base: &str, error: attohttpc::Error) -> ApiError {
    if let attohttpc::ErrorKind::Io(io) = error.kind() {
        if matches!(io.kind(), std::io::ErrorKind::ConnectionRefused | std::io::ErrorKind::NotFound) {
            return ApiError::new(format!("Cannot reach the Clara server at {base}."), None);
        }
    }
    ApiError::new(format!("The Clara server did not answer: {error}"), None)
}

/// What the server said was wrong: its `detail`, or the start of its answer.
fn detail(response: Response) -> String {
    let status = response.status();
    let text = response.text().unwrap_or_default();
    match serde_json::from_str::<Value>(&text) {
        Ok(Value::Object(body)) => body.get("detail").map_or_else(
            || Value::Object(body.clone()).to_string(),
            |d| match d {
                Value::String(text) => text.clone(),
                other => other.to_string(),
            },
        ),
        _ if text.trim().is_empty() => status.canonical_reason().unwrap_or("error").to_owned(),
        _ => text.trim().chars().take(300).collect(),
    }
}

fn base_of(url: &str) -> String {
    url.trim().trim_end_matches('/').to_owned()
}

fn header(builder: RequestBuilder, name: &'static str, value: &str) -> Result<RequestBuilder, ApiError> {
    builder
        .try_header(name, value)
        .map_err(|_| ApiError::new("The sign-in or the address contains a character the server cannot be sent.", None))
}

/// What a sign-in on the surface of the app gives.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Login {
    pub token: String,
    pub user_name: String,
}

/// What a sign-in as the web site gives: the value of its session cookie.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct WebSession {
    pub token: String,
    pub max_age: Option<i64>,
    pub secure: bool,
}

#[derive(Deserialize)]
struct LoginAnswer {
    #[serde(default)]
    token: String,
    user: LoginUser,
}

#[derive(Deserialize)]
struct LoginUser {
    name: String,
}

fn login_request(url: &str, user: &str, password: &str, surface: &str, web: bool) -> Result<(Response, String), ApiError> {
    let base = base_of(url);
    let device = gethostname::gethostname().to_string_lossy().into_owned();
    let body = json!({"username": user.trim(), "password": password, "surface": surface, "device": device});
    let mut request = attohttpc::post(format!("{base}/v1/auth/login")).connect_timeout(CONNECT_TIMEOUT).timeout(CALL_TIMEOUT);
    if web {
        request = header(request, "X-Clara-Web", "1")?; // that is what makes the server answer with a cookie, not a token
    }
    let response =
        request.json(&body).map_err(|error| ApiError::new(error.to_string(), None))?.send().map_err(|error| transport(&base, error))?;
    if !response.is_success() {
        let status = response.status().as_u16();
        return Err(ApiError::new(detail(response), Some(status)));
    }
    Ok((response, base))
}

/// Signs in with a password on the surface of the app: a token bound to this user. The password is not kept.
pub fn login(url: &str, user: &str, password: &str) -> Result<Login, ApiError> {
    let (response, _) = login_request(url, user, password, SURFACE, false)?;
    let answer: LoginAnswer = response
        .json()
        .map_err(|error| ApiError::new(format!("The Clara server sent an answer the app does not understand ({error})."), None))?;
    if answer.token.is_empty() {
        return Err(ApiError::new("The Clara server gave no token.", None));
    }
    Ok(Login { token: answer.token, user_name: answer.user.name })
}

/// Signs in as the web site does, to hand its session to the window: the site then does not ask for the password again.
pub fn login_web(url: &str, user: &str, password: &str) -> Result<WebSession, ApiError> {
    let (response, _) = login_request(url, user, password, "web", true)?;
    for value in response.headers().get_all("set-cookie") {
        if let Some(session) = value.to_str().ok().and_then(parse_session_cookie) {
            return Ok(session);
        }
    }
    Err(ApiError::new("The Clara server gave no session for the web site.", None))
}

pub const COOKIE: &str = "clara_session";

fn parse_session_cookie(line: &str) -> Option<WebSession> {
    let mut parts = line.split(';').map(str::trim);
    let (name, token) = parts.next()?.split_once('=')?;
    if name != COOKIE || token.is_empty() {
        return None;
    }
    let mut session = WebSession { token: token.to_owned(), max_age: None, secure: false };
    for attribute in parts {
        let (key, value) = attribute.split_once('=').unwrap_or((attribute, ""));
        match key.to_ascii_lowercase().as_str() {
            "max-age" => session.max_age = value.parse().ok(),
            "secure" => session.secure = true,
            _ => {}
        }
    }
    Some(session)
}

/// Ends a session of the server (the one `token` belongs to).
pub fn logout(url: &str, token: &str) -> Result<(), ApiError> {
    let base = base_of(url);
    let request = header(attohttpc::post(format!("{base}/v1/auth/logout")), "Authorization", &format!("Bearer {}", token.trim()))?;
    let response = request
        .connect_timeout(CONNECT_TIMEOUT)
        .timeout(CALL_TIMEOUT)
        .json(&json!({}))
        .map_err(|error| ApiError::new(error.to_string(), None))?
        .send()
        .map_err(|error| transport(&base, error))?;
    if response.is_success() || response.status().as_u16() == 401 {
        return Ok(()); // 401: it was over already
    }
    let status = response.status().as_u16();
    Err(ApiError::new(detail(response), Some(status)))
}

/// A job Clara asked of one of this computer's folders.
#[derive(Debug, Clone, PartialEq, Deserialize)]
pub struct Job {
    pub id: i64,
    pub op: String,
    #[serde(default)]
    pub alias: String,
    #[serde(default)]
    pub args: serde_json::Map<String, Value>,
}

pub struct Api {
    base: String,
    token: String,
    user_id: String,
}

impl Api {
    pub fn new(url: &str, token: &str, user_id: &str) -> Api {
        Api { base: base_of(url), token: token.trim().to_owned(), user_id: user_id.to_owned() }
    }

    pub fn from_config(config: &crate::config::Config) -> Api {
        Api::new(&config.url, &config.token, &config.user_id)
    }

    fn user_token(&self) -> bool {
        self.token.starts_with("clu_") // from a password sign-in (else a shared client token)
    }

    fn identity(&self) -> [(&'static str, String); 2] {
        [("surface", SURFACE.to_owned()), ("user_id", self.user_id.clone())]
    }

    fn request(&self, method: Method, path: &str, read: Duration) -> Result<RequestBuilder, ApiError> {
        let request = RequestBuilder::try_new(method, format!("{}{path}", self.base))
            .map_err(|error| ApiError::new(format!("Not a usable server address: {error}"), None))?;
        Ok(header(request, "Authorization", &format!("Bearer {}", self.token))?.connect_timeout(CONNECT_TIMEOUT).read_timeout(read))
    }

    fn refused(&self, response: Response) -> ApiError {
        let status = response.status().as_u16();
        if status == 401 && self.user_token() {
            return ApiError::new(SIGNED_OUT, Some(401));
        }
        ApiError::new(format!("Clara server: {} (HTTP {status})", detail(response)), Some(status))
    }

    fn send<B: attohttpc::body::Body>(&self, request: RequestBuilder<B>) -> Result<Response, ApiError> {
        let response = request.send().map_err(|error| transport(&self.base, error))?;
        if response.is_success() {
            Ok(response)
        } else {
            Err(self.refused(response))
        }
    }

    /// The server's own account of itself: `{"status", "provider", "model"}` (no sign-in needed).
    pub fn health(&self) -> Result<Value, ApiError> {
        let request = attohttpc::get(format!("{}/health", self.base)).connect_timeout(CONNECT_TIMEOUT).timeout(CALL_TIMEOUT);
        let response = request.send().map_err(|error| transport(&self.base, error))?;
        if !response.is_success() {
            return Err(self.refused(response));
        }
        response.json().map_err(|error| ApiError::new(error.to_string(), None))
    }

    /// The user's reminders as they come due and the notifications as they are sent (the ones missed since this
    /// client last connected first), and what the server is doing.
    pub fn notifications(&self) -> Result<EventStream, ApiError> {
        let request = self.request(Method::GET, "/v1/notifications/stream", STREAM_READ_TIMEOUT)?.params(self.identity());
        let response = self.send(request)?;
        Ok(EventStream { lines: BufReader::new(response).lines() })
    }

    /// What Clara asked of this computer's folders (each job is given out once).
    pub fn computer_jobs(&self, device: &str) -> Result<Vec<Job>, ApiError> {
        let request = self.request(Method::GET, "/v1/integrations/jobs", CALL_TIMEOUT)?.params(self.identity()).param("device", device);
        let answer: Value = self.send(request)?.json().map_err(|error| ApiError::new(error.to_string(), None))?;
        serde_json::from_value(answer.get("jobs").cloned().unwrap_or(Value::Array(vec![])))
            .map_err(|error| ApiError::new(format!("The Clara server sent jobs the app does not understand ({error})."), None))
    }

    /// Says how a job went; `text` goes back to Clara as it is.
    pub fn finish_job(&self, job: i64, ok: bool, text: &str) -> Result<(), ApiError> {
        let body = json!({"surface": SURFACE, "user_id": self.user_id, "ok": ok, "text": text});
        let request = self.request(Method::POST, &format!("/v1/integrations/jobs/{job}/result"), Duration::from_secs(60))?;
        self.send(request.json(&body).map_err(|error| ApiError::new(error.to_string(), None))?)?;
        Ok(())
    }
}

/// The Server-Sent Events of a response: iterating yields the decoded events.
pub struct EventStream {
    lines: std::io::Lines<BufReader<Response>>,
}

impl Iterator for EventStream {
    type Item = Result<Value, ApiError>;

    fn next(&mut self) -> Option<Self::Item> {
        loop {
            match self.lines.next()? {
                Ok(line) => {
                    let Some(data) = line.strip_prefix("data:") else { continue }; // keepalives and blank lines
                    if let Ok(event) = serde_json::from_str(data.trim_start()) {
                        return Some(Ok(event));
                    }
                }
                Err(error) => {
                    return Some(Err(ApiError::new(format!("The connection to the Clara server broke: {error}"), None)));
                }
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_session_cookie_is_found_with_its_attributes() {
        let session = parse_session_cookie("clara_session=clu_abc; Max-Age=86400; Path=/; HttpOnly; SameSite=strict; Secure").unwrap();
        assert_eq!(session, WebSession { token: "clu_abc".into(), max_age: Some(86400), secure: true });
        assert!(parse_session_cookie("other=1; Path=/").is_none());
        assert!(parse_session_cookie("clara_session=; Path=/").is_none());
    }
}
