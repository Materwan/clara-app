# Clara app

A small desktop chat for a [Clara server](../clara-server/README.md). It lives in the Windows
notification area: **click the icon to open the window**, click it again (or close the window) to
put it away. The app keeps running in the tray.

- Chat with Clara; the answer streams in and is shown as Markdown.
- **Reminders** (`/remind` in `clara-chat` or the console, or "remind me…" to Clara) pop up as a
  Windows notification even when the window is hidden. The notification holds **the message Clara wrote
  for it** (the reminder's own text if she could not), who set it, and ends with the state of the server:
  *Clara is running*. Clicking it opens the app. What fired while the app was closed arrives when it
  starts, marked *missed*.
- **The state of the server** is always shown: in the window ("● Clara is running", "◐ Clara is
  stopping", "○ Clara is not running"), in the icon's tooltip, and by a notification whenever it
  changes: *Clara is stopping: she finishes what is running and takes nothing new*, *Clara is not
  running*, *Clara is running again*. Asking a question while she is stopping says so.
- Right-click the icon: Open, Settings…, **Start with Windows**, Quit. The icon turns grey when Clara
  is not running.
- Starting the app a second time just shows the window of the one already running.

## Install and run

```powershell
cd clara-app
python -m venv .venv
.venv\Scripts\pip install -e ".[dev]"
.venv\Scripts\clara-app            # or: .venv\Scripts\python -m clara_app
```

The first time, a dialog asks for the server (`http://127.0.0.1:8765` by default), a **token** (one
of the server's `CLARA_TOKENS`), your id and, if you like, your name. *Test connection* checks them.
They are saved in `%APPDATA%\clara-app\config.json` (the token is stored there as plain text, like
the `.env` files of the other clients). `CLARA_URL` and `CLARA_TOKEN` in the environment fill in what
the file leaves empty.

The app speaks to the server as the surface **`app`**. If the server limits the surfaces of its
tokens, add it: `CLARA_CLIENT_SURFACES=...,app=app`. Its conversation is `app:<your id>`; to share
what Clara knows with your other clients, link the accounts (see the server README).

## Using it

| | |
|---|---|
| `Enter` | send (`Shift+Enter` for a new line) |
| **Stop** (the Send button while Clara writes) | stop the answer; what has arrived is kept |
| **New chat** | forget this conversation on the server (what Clara knows about you stays) |
| **Settings** | change server, token, name |
| left click on the icon | show / hide the window |
| double click on the icon | open the window |

"Start with Windows" adds a `ClaraApp` value to `HKCU\Software\Microsoft\Windows\CurrentVersion\Run`
that starts `pythonw -m clara_app --background`: the app then goes straight to the tray without
opening the window. Untick it to remove the value.

Closing the window does not stop the app: use **Quit** in the tray menu.

## What it does not do (yet)

- It does not show the earlier messages of a conversation when it starts (Clara remembers them;
  the window only shows what was said since it was opened).
- It runs no tools on your computer (files, shell...): it is a plain chat. The server's own tools
  (`remember`, `remind`...) work.
- Windows only for the start-up entry; the rest is Qt and should run elsewhere, but the tray is
  only tested on Windows 11.

## Layout

```
src/clara_app/
  __main__.py       entry point: Qt application, single instance, --background
  app.py            ties the tray, the window and the reminder listener together
  tray.py           the notification-area icon, its menu, the notifications
  chat_window.py    the window: input box, send/stop, new chat
  chat_view.py      the conversation: bubbles rendered as Markdown
  settings_dialog.py  server / token / identity, with a connection test
  api.py            the Clara server over HTTP (Qt-free, blocking)
  workers.py        threads: one reply, the reminder / server-state stream, a connection test
  status.py         the words used for the state of the server
  config.py         %APPDATA%\clara-app\config.json
  autostart.py      the "Start with Windows" registry value
  single.py         one running copy per user (a named local socket)
  icon.py           the icon, drawn in code
tests/              Qt runs offscreen, against a small fake Clara server
```

## Development

```powershell
.venv\Scripts\python -m pytest
```
