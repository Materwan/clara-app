//! The settings of the app, saved in `%APPDATA%\clara-app\config.json` (the same file, and the same fields, as the
//! earlier Python app: signing in again is not needed after an update).
//!
//! `CLARA_URL` and `CLARA_TOKEN` in the environment fill in what the file does not say.

use serde::{Deserialize, Serialize};
use std::fs;
use std::io;
use std::path::{Path, PathBuf};

pub const DEFAULT_URL: &str = "http://127.0.0.1:8765";

/// The folder of the app's files: `%APPDATA%\clara-app` (or `$XDG_CONFIG_HOME/clara-app`, `~/.config/clara-app`).
pub fn config_dir() -> PathBuf {
    let base = non_empty_env("APPDATA")
        .or_else(|| non_empty_env("XDG_CONFIG_HOME"))
        .map(PathBuf::from)
        .or_else(|| non_empty_env("HOME").map(|home| PathBuf::from(home).join(".config")))
        .unwrap_or_else(|| PathBuf::from("."));
    base.join("clara-app")
}

fn non_empty_env(name: &str) -> Option<String> {
    std::env::var(name).ok().filter(|value| !value.trim().is_empty())
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(default)]
pub struct Config {
    pub url: String,
    /// What the server gave when the password was used (kept as plain text, like the `.env` files of the other clients).
    pub token: String,
    pub user_id: String,
    pub user_name: String,
}

impl Default for Config {
    fn default() -> Self {
        Config { url: DEFAULT_URL.into(), token: String::new(), user_id: String::new(), user_name: String::new() }
    }
}

impl Config {
    /// Enough to talk to a server.
    pub fn ready(&self) -> bool {
        !self.url.trim().is_empty() && !self.token.trim().is_empty() && !self.user_id.trim().is_empty()
    }

    /// The saved settings; the environment supplies a URL or a token the file leaves empty.
    /// A file that is missing or unreadable gives the defaults (the app then asks to sign in).
    pub fn load(path: &Path) -> Config {
        let saved: serde_json::Value =
            fs::read_to_string(path).ok().and_then(|text| serde_json::from_str(&text).ok()).unwrap_or(serde_json::Value::Null);
        let mut config = Config::default();
        let field = |name: &str| saved.get(name).and_then(|value| value.as_str()).map(str::to_owned);
        let saved_url = field("url").filter(|url| !url.trim().is_empty());
        if let Some(url) = &saved_url {
            config.url = url.clone();
        }
        config.token = field("token").unwrap_or_default();
        config.user_id = field("user_id").unwrap_or_default();
        config.user_name = field("user_name").unwrap_or_default();
        if saved_url.is_none() {
            if let Some(url) = non_empty_env("CLARA_URL") {
                config.url = url.trim().to_owned();
            }
        }
        if config.token.is_empty() {
            if let Some(token) = non_empty_env("CLARA_TOKEN") {
                config.token = token.trim().to_owned();
            }
        }
        if config.user_id.is_empty() {
            config.user_id = default_user();
        }
        config
    }

    pub fn load_default() -> Config {
        Config::load(&config_dir().join("config.json"))
    }

    pub fn save(&self, path: &Path) -> io::Result<()> {
        write_private(path, &serde_json::to_string_pretty(self).map_err(io::Error::other)?)
    }

    pub fn save_default(&self) -> io::Result<()> {
        self.save(&config_dir().join("config.json"))
    }
}

/// Writes a file that holds a secret (or a list the server must not learn): whole or not at all, readable by this user only.
pub fn write_private(path: &Path, text: &str) -> io::Result<()> {
    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent)?;
    }
    let temporary = path.with_extension("tmp");
    fs::write(&temporary, text)?;
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        fs::set_permissions(&temporary, fs::Permissions::from_mode(0o600))?;
    }
    fs::rename(&temporary, path)
}

fn default_user() -> String {
    non_empty_env("USERNAME").or_else(|| non_empty_env("USER")).unwrap_or_default()
}

/// The server address as typed, made usable: a scheme is added when it is missing (`http` for this machine and the
/// local network, `https` for anything else) and a trailing `/` is dropped.
pub fn normalize_url(typed: &str) -> Result<String, String> {
    let mut typed = typed.trim();
    while typed.ends_with('/') && !typed.ends_with("://") {
        typed = &typed[..typed.len() - 1];
    }
    if typed.is_empty() {
        return Err("Type the address of the Clara server.".into());
    }
    let url = if typed.contains("://") {
        typed.to_owned()
    } else if is_local(host_of(typed)) {
        format!("http://{typed}")
    } else {
        format!("https://{typed}")
    };
    let (scheme, _) = url.split_once("://").unwrap_or(("", ""));
    if scheme != "http" && scheme != "https" {
        return Err("The address of the server starts with http:// or https://.".into());
    }
    if host_of(&url).is_empty() || url.chars().any(char::is_whitespace) {
        return Err("That does not look like an address.".into());
    }
    Ok(url)
}

/// `localhost` of `http://localhost:8765/x`; an IPv6 address comes without its brackets.
fn host_of(url: &str) -> &str {
    let rest = url.split_once("://").map_or(url, |(_, rest)| rest);
    let authority = rest.split(['/', '?', '#']).next().unwrap_or("");
    let authority = authority.rsplit_once('@').map_or(authority, |(_, host)| host);
    if let Some(inner) = authority.strip_prefix('[') {
        return inner.split(']').next().unwrap_or("");
    }
    authority.split(':').next().unwrap_or("")
}

fn is_local(host: &str) -> bool {
    let host = host.to_ascii_lowercase();
    host == "localhost"
        || host == "::1"
        || host.starts_with("127.")
        || host.starts_with("10.")
        || host.starts_with("192.168.")
        || (16..=31).any(|n| host.starts_with(&format!("172.{n}.")))
}

/// A word of advice about the server address typed, or `None` when it looks fine.
pub fn url_hint(url: &str) -> Option<&'static str> {
    let url = url.trim();
    if !url.to_ascii_lowercase().starts_with("http://") {
        return None;
    }
    let host = host_of(url).to_ascii_lowercase();
    if host.is_empty() {
        return None;
    }
    if host.ends_with(".ts.net") {
        return Some("A Tailscale address is served over HTTPS: use https:// (the address clara-server shows in /status).");
    }
    if !(host == "localhost" || host == "::1" || host.starts_with("127.")) {
        return Some("Plain http: the sign-in crosses the network unencrypted. Prefer an https:// address (Tailscale, a proxy).");
    }
    None
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_missing_file_gives_the_defaults() {
        let dir = tempfile::tempdir().unwrap();
        let config = Config::load(&dir.path().join("none.json"));
        assert_eq!(config.url, DEFAULT_URL);
        assert!(!config.ready());
    }

    #[test]
    fn the_file_of_the_python_app_is_read_and_its_theme_ignored() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("config.json");
        fs::write(&path, r#"{"url": "https://clara.example", "token": "clu_x", "user_id": "ana", "user_name": "Ana", "theme": "dark"}"#)
            .unwrap();
        let config = Config::load(&path);
        assert_eq!(config.url, "https://clara.example");
        assert_eq!(config.user_name, "Ana");
        assert!(config.ready());
    }

    #[test]
    fn saving_and_loading_give_the_same_settings() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("sub").join("config.json");
        let config = Config { url: "http://a:1".into(), token: "clu_t".into(), user_id: "u".into(), user_name: "U".into() };
        config.save(&path).unwrap();
        assert_eq!(Config::load(&path), config);
        assert!(!path.with_extension("tmp").exists());
    }

    #[test]
    fn addresses_are_completed_and_cleaned() {
        assert_eq!(normalize_url(" localhost:8765/ ").unwrap(), "http://localhost:8765");
        assert_eq!(normalize_url("192.168.1.20:8765").unwrap(), "http://192.168.1.20:8765");
        assert_eq!(normalize_url("clara.tail1234.ts.net").unwrap(), "https://clara.tail1234.ts.net");
        assert_eq!(normalize_url("https://a.b/c/").unwrap(), "https://a.b/c");
        assert!(normalize_url("").is_err());
        assert!(normalize_url("ftp://a.b").is_err());
        assert!(normalize_url("http://").is_err());
    }

    #[test]
    fn advice_on_plain_http_to_another_machine() {
        assert!(url_hint("http://127.0.0.1:8765").is_none());
        assert!(url_hint("http://[::1]:8765").is_none());
        assert!(url_hint("https://clara.ts.net").is_none());
        assert!(url_hint("http://clara.tail1.ts.net").unwrap().contains("Tailscale"));
        assert!(url_hint("http://192.168.1.4:8765").unwrap().contains("unencrypted"));
    }
}
