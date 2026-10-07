//! Where the window of the site may go.
//!
//! The window shows the Clara server and the pages of the app itself, nothing else: a link to another site opens in the
//! browser of the computer instead. So a page of the server (or a message Clara wrote with a link in it) can never turn
//! the app's window into a view of some other site.

use url::Url;

/// May the window go to `url`, when the server is `site`?
pub fn allowed(url: &Url, site: Option<&Url>) -> bool {
    site.is_some_and(|site| url.origin() == site.origin())
        || matches!(url.scheme(), "about" | "blob" | "data" | "tauri")
        || url.host_str() == Some("tauri.localhost") // the pages of the app itself, on Windows
}

/// May this address be handed to the browser of the computer? Only what a person would click: no `file:`, no
/// `javascript:`, and not the empty window of `window.open("about:blank")`.
pub fn may_open_outside(url: &Url) -> bool {
    matches!(url.scheme(), "http" | "https" | "mailto")
}

#[cfg(test)]
mod tests {
    use super::*;

    fn url(text: &str) -> Url {
        Url::parse(text).unwrap()
    }

    #[test]
    fn the_server_and_the_app_itself_are_allowed_and_nothing_else() {
        let site = url("https://clara.tail1.ts.net");
        assert!(allowed(&url("https://clara.tail1.ts.net/#/tasks"), Some(&site)));
        assert!(allowed(&url("https://clara.tail1.ts.net/v1/anything"), Some(&site)));
        assert!(allowed(&url("http://tauri.localhost/loading.html"), Some(&site)));
        assert!(allowed(&url("tauri://localhost/index.html"), Some(&site)));
        assert!(!allowed(&url("https://github.com/settings"), Some(&site)));
        assert!(!allowed(&url("https://clara.tail1.ts.net.evil.example/"), Some(&site)));
        assert!(!allowed(&url("http://clara.tail1.ts.net/"), Some(&site)), "another scheme is another origin");
        assert!(!allowed(&url("https://clara.tail1.ts.net:8443/"), Some(&site)), "so is another port");
        assert!(!allowed(&url("file:///C:/Windows/win.ini"), Some(&site)));
        assert!(!allowed(&url("javascript:alert(1)"), Some(&site)));
    }

    #[test]
    fn without_a_server_only_the_pages_of_the_app_are_allowed() {
        assert!(!allowed(&url("http://127.0.0.1:8765/"), None));
        assert!(allowed(&url("http://tauri.localhost/index.html"), None));
    }

    #[test]
    fn only_what_a_person_would_click_goes_to_the_browser() {
        assert!(may_open_outside(&url("https://accounts.google.com/o/oauth2/auth?x=1")));
        assert!(may_open_outside(&url("mailto:a@b.c")));
        assert!(!may_open_outside(&url("about:blank")));
        assert!(!may_open_outside(&url("file:///C:/Windows/win.ini")));
        assert!(!may_open_outside(&url("javascript:alert(1)")));
        assert!(!may_open_outside(&url("ms-msdt:/id")));
    }
}
