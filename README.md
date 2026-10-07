# Clara app

The [Clara server](../clara-server/README.md)'s web site, as a desktop app. It lives in the Windows notification area:
**click the icon to open the window**, click it again (or close the window) to put it away. The app keeps running in
the tray, where reminders and notifications reach you.

The window **is the web site**: chat, projects, tasks, files, memory, account, administration (the same pages, the same
fonts and theme, everything the site does). The app does not draw any of it; what it adds is what only a program on this
computer can do:

- **Notifications.** Reminders, "your answer is ready", requests for permission nobody answered in time, and the server
  stopping or coming back pop up as Windows notifications even when the window is hidden. A reminder shows the message
  Clara wrote for it and the state of the server; one that fired while the app was closed arrives when it starts,
  marked *missed*.
- **Folders of this computer.** Clara can read, search and change files in the folders you add (tray menu, *Settings and
  folders…*), and only in those, only while the app is running. The server never learns the path, only a name for the
  folder and an id for this computer; every path a job names is checked to stay inside the folder (no `..`, no
  absolute path, no link that leaves it). Replacing or deleting asks your permission first, like everywhere else.
- **The tray.** The icon turns grey when Clara is not running; its tooltip says so. Right click: Open, Tasks…,
  Settings and folders…, Start with Windows, Quit. Starting the app a second time just shows the window of the one
  already running.
- **Sign-in once.** You type the server, your user name and your password in the app; it signs in for itself (reminders,
  folders) and for the site, whose session it puts in the window, so the site does not ask again. The password is not kept.

## Why it is built this way

The first version of this app (the `main` branch, Python and Qt) drew every page of the site again in native widgets:
about 10,000 lines that said the same thing as the site's 7,000, and every new feature (sub tasks, connections...) had to
be written twice. This one shows the site itself, and keeps native code only for what a web page cannot do.

```
crates/agent/        clara-agent: the logic, with no window and no Tauri (this is what the tests cover)
  config.rs            the server address and sign-in, in %APPDATA%\clara-app\config.json (same file as before)
  api.rs               the few routes of the server the app calls: sign in (app and web), the event stream, the jobs
  listen.rs, events.rs the stream of reminders / notifications / jobs, kept open; and the words of a notification
  folders.rs           the folders Clara may work in: the registry, the path checks, list / read / search / write / delete / move
  documents.rs         the text of a file (a PDF page by page, text in UTF-8 or Windows-1252)
  jobs.rs              fetching what Clara asked of this computer, doing it, answering; one run at a time
  navigation.rs        where the window may go (the server and the app's own pages; other sites open in the browser)
  status.rs, icon.rs   the states of the server in words; the grey icon
src-tauri/           the Tauri shell: wires the agent to the screen
  windows.rs           the site's window and the settings window
  tray.rs, runtime.rs  the icon and its menu; the thread that listens and shows notifications
  commands.rs          what the settings page can ask for (sign in/out, folders, start-up)
ui/                  the settings page (plain HTML, CSS and JS: no build step, like the site) and the loading page
```

The window of the site is a page of another origin, so Tauri gives it no access to the app's commands: only the
settings page (a page of the app itself) can call them. Links that leave the server (GitHub, Google's consent page...)
open in your browser.

## Build and run

Windows 10/11 with [Rust](https://rustup.rs) (stable) and the WebView2 runtime (already in Windows 11).

```powershell
cargo install tauri-cli --version "^2" --locked   # once
cargo tauri dev                                   # runs the app
cargo tauri build                                 # the installer: target\release\bundle\nsis\
cargo test --workspace                            # the tests
```

The first time, the app asks for the server (`http://127.0.0.1:8765` by default; for a server reached through
Tailscale, its `https://<machine>.<tailnet>.ts.net` address), your user name and your password (the administrator makes
them with `/user add`). The sign-in token is saved in `%APPDATA%\clara-app\config.json` as plain text (like the `.env`
files of the other clients), the folders in `computer-folders.json` next to it. Both files are the ones the earlier
Python app wrote: nothing to sign in or add again after switching. `CLARA_URL` and `CLARA_TOKEN` in the environment still
fill in what the file leaves empty.

The app speaks to the server as the surface **`app`** (`CLARA_CLIENT_SURFACES=...,app=app` if the server limits the
surfaces of its tokens) and the site as **`web`**: the two are the same person, so conversations are the same in both. If
the server stops accepting the app's token (you were signed out, the password changed) the app says so and opens the
settings page to sign in again.

"Start with Windows" adds a value to `HKCU\Software\Microsoft\Windows\CurrentVersion\Run` that starts the app with
`--background`: it then goes straight to the tray without opening the window.

## What it does not do

- **A click on a notification does not open the window** (Windows notifications from Tauri carry no click handler):
  click the tray icon instead.
- **Signing out inside the site** (its own *Sign out*) ends the site's session only; the app keeps listening for
  reminders until you *Sign out* in the app's settings.
- It runs no tools on your computer other than the folder jobs above (no shell).
- A scanned PDF (images of pages) has no text to read: there is no OCR.
- Windows only for the start-up entry and the tray; the rest should run elsewhere but is not tested there.

## Checking a build by hand

The logic is tested (`cargo test --workspace`: a fake server speaks the real protocol). The windows, tray and
notifications need a person at a Windows desktop; after a change to `src-tauri/`:

1. First start: the settings page asks to sign in; after signing in the site opens **without** asking for the password.
2. Close the window: the icon stays. Click the icon: the window comes back. Quit from the menu: the icon goes.
3. Start the app twice: the second start brings the first window forward.
4. Stop the server: a notification says so and the icon turns grey; start it: it says so and the icon is coloured again.
5. Add a folder in the settings, ask Clara to list it in a chat: she answers; ask her to write a file: you are asked first.
6. Ask Clara for a reminder in one minute: a notification arrives with the window hidden.
7. Click a link to another site in a conversation: it opens in the browser and the window stays on Clara.
8. *Start with Windows*, restart the PC: the icon is there and the window is not.
