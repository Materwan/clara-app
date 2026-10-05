# Clara app

The [Clara server](../clara-server/README.md)'s web site, as a desktop app. It lives in the Windows notification area:
**click the icon to open the window**, click it again (or close the window) to put it away. The app keeps running in
the tray, where reminders and notifications reach you.

The window is the web site's: a rail of Prussian blue on the left with Clara's portrait, a **New chat** button, the
pages you work in (*Chat, Projects, Tasks, Files*), your conversations, and you at the bottom; the page beside it. It
follows Windows' light or dark setting (or your choice, on the Account page) and uses the site's two fonts. Below
about 780 pixels wide the rail slides in over the page, behind a menu button, so the window can also be a narrow one.

- **Chat**: your messages in a violet-tinted bubble on the right, Clara's answers (Markdown, formulas) on the left
  beside her portrait, with a quiet line under a reply that says what she used (*Set a reminder*, *Looked in memory
  for…*). The box you write in is as wide as the conversation; **Enter** sends, **Shift+Enter** starts a new line.
  The 📎 button (or dropping files on the window) attaches PDF, code, Markdown or any text file: they are read **on this
  computer** and sent with your next message. When an administrator offers several models, a quiet picker in the box
  chooses the one Clara answers you with here, with what a token costs in credits (see the Account page).
- **Conversations**, in the rail: pinned ones first, then by day (*Today*, *Yesterday*, *Previous 7 days*...) for the
  chats that belong to no project, then **one group for each project**, named after it, with its conversations newest
  first (click the project's name to open it). Click a conversation to read it again and go on with it; right-click it
  (or use the "…" of the page header) to **rename**, **pin**, **move to a project** or **delete** it. The search box
  looks in titles and messages. Clara titles a conversation after its first answer. While she writes, the list waits:
  an answer is kept only once it is complete.
- **Projects**: files, folders and GitHub repositories Clara uses in every conversation of the project, with
  instructions of their own, kept by the server (the same here and on the web site). *New project*, then *Add files…*
  (text, code, PDF, Word, `.zip`), *Add a folder…*, *GitHub…* (the server downloads a repository; *Sync* gets its
  latest version); double-click a file to read it. **New chat in this project** starts a chat there: that is how a
  chat gets into a project (or move one from the rail).
- **Tasks**: your to-do list, the same on the web site, in the terminal and in every chat with Clara ("add a task: send
  the invoice by Friday"). Each task shows how many reminders were sent and the next one; a task without a reminder gets
  them chosen by Clara, who moves the next ones each time one is sent, so the page reads the list again every 30 seconds.
- **Files**: the Markdown files Clara wrote for you, to read (rendered or as Markdown), copy, save or delete.
- **QCM**: a quiz or a questionnaire from Clara appears in the conversation with radio buttons, check boxes or a text
  box; **Send answers** sends them as your next message and a graded form then shows your score and her explanations.
- **Formulas**: Clara writes LaTeX (`$x^2$`, `\(x^2\)`, `$$…$$`, `\[…\]`) and the app typesets it with KaTeX (the files
  are in `clara_app/katex/`, the same as the web site's). A message that holds a formula is shown by a small embedded
  web view (Qt WebEngine); the others stay ordinary labels, which are lighter. The view loads nothing but the local
  KaTeX files, and raw HTML in an answer is shown as text.
- **Your settings** are reached by clicking you at the bottom of the rail, and share a bar of tabs at the top:
  - **Memory**: what Clara remembers about you, to add to or take away from.
  - **Account**: who you are, the theme (*Auto, Light, Dark*), your usage today in credits, the model Clara answers
    you with in the app, when a finished task notifies you, your password, the devices you are signed in on, and the
    connection to the server. *Sign out* forgets the sign-in on this computer.
  - **Discord** and **Admin** (administrators only): the bot built into the server, its servers and signed-in accounts;
    and users (add, password, limits, sign out), the models people may choose and what they cost, the server's status and
    model, what Clara knows about each person, and the server's console. The same as on the web site.
- **Reminders** (`/remind` in `clara-chat` or the console, or "remind me…" to Clara) are yours only: they pop up as a
  Windows notification even when the window is hidden, when they were set for every client of yours or for the app
  (`@app`; Clara picks this herself when you say "on my desktop"). The notification holds **the message Clara wrote for
  it** (the reminder's own text if she could not), and ends with the state of the server. Clicking it opens the app. What
  fired while the app was closed arrives when it starts, marked *missed*.
- **Notifications** pop up the same way: from Clara (when she has finished a long task you asked about), from the server
  (an answer that took long is ready, the conversation was summarised, the model changed) or from another client. One
  about the conversation you are looking at, while the window is in front, is only noted in the conversation.
- **The state of the server** is under your name in the rail ("Clara is running", "stopping", "not running"), in the
  icon's tooltip, and by a notification whenever it changes. The icon (`clara_app/clara.ico`, the same portrait as the
  web site's) turns grey when Clara is not running.
- Right-click the icon: Open, Tasks…, Settings… (the Account page), **Start with Windows**, Quit.
- Starting the app a second time just shows the window of the one already running.

## Install and run

```powershell
cd clara-app
python -m venv .venv
.venv\Scripts\pip install -e ".[dev]"
.venv\Scripts\clara-app            # or: .venv\Scripts\python -m clara_app
```

The first time, a dialog asks for the server (`http://127.0.0.1:8765` by default), your **user name and
password** (the administrator makes them with `/user add`) and, if you like, your name. *Test connection* signs
in and checks. The password is used once and **not kept**: the server gives the app a sign-in token, saved in
`%APPDATA%\clara-app\config.json` as plain text (like the `.env` files of the other clients), next to the theme you
chose. If the token stops working (you were signed out, the password changed) the app says so: sign in again from the
Account page. A shared client token (an entry of `CLARA_TOKENS`) still works in the *Sign-in token* field, or
`CLARA_TOKEN` in the environment (the Account, Admin and Discord pages are for users, so they say what they cannot show
with it); `CLARA_URL` fills in the server when the file leaves it empty. For a server reached through Tailscale, type
its `https://<machine>.<tailnet>.ts.net` address (the dialog warns about plain `http://` to another machine).

The app speaks to the server as the surface **`app`**. If the server limits the surfaces of its
tokens, add it: `CLARA_CLIENT_SURFACES=...,app=app`. Its account is `app:<your id>`, and its conversations
are `app:<your id>:<random>`; to share
what Clara knows with your other clients, link the accounts (see the server README). Linking matters
for reminders and notifications too: they reach the app only when `app:<your id>` is the same person
as the account they were set from.

## Using it

| | |
|---|---|
| `Enter` | send (`Shift+Enter` for a new line) |
| **Stop** (the send button while Clara writes) | stop the answer; what has arrived is kept |
| **New chat** | start a new conversation; the one shown stays in the list |
| right click in the list, or "…" | rename, pin / unpin, move to a project, delete (erased on the server; what Clara knows about you stays) |
| click you, bottom of the rail | Memory, Account, Discord, Admin |
| left click on the icon | show / hide the window |
| double click on the icon | open the window |

"Start with Windows" adds a `ClaraApp` value to `HKCU\Software\Microsoft\Windows\CurrentVersion\Run`
that starts `pythonw -m clara_app --background`: the app then goes straight to the tray without
opening the window. Untick it to remove the value.

Closing the window does not stop the app: use **Quit** in the tray menu.

## What it does not do (yet)

- The list shows the conversations started in the app, not those of `clara-chat`, the console or Discord.
  The single conversation of earlier versions (`app:<your id>`) is not in it; its messages stay on the server.
- A conversation shows its last 200 messages. When older ones were deleted after a summary
  (`CLARA_PURGE_SUMMARISED` on the server), the summary is shown in their place. Attached documents show
  as their names: their text is not shown again. What Clara used to answer (the line under a reply) is shown
  for the answers written in this session only: the server does not keep the tool calls with the messages.
- It runs no tools on your computer (files, shell...): Clara reads only the documents you attach, and
  cannot open a file by herself. The server's own tools (`remember`, `remind`, `notify`...) work.
- The Tasks page is a list with its form, not the web site's month calendar; Projects is a list with its detail, not
  cards. A scanned PDF (images of pages) has no text to read: there is no OCR.
- Windows only for the start-up entry; the rest is Qt and should run elsewhere, but the tray is
  only tested on Windows 11.

## Layout

```
src/clara_app/
  __main__.py       entry point: Qt application, single instance, --background
  app.py            ties the tray, the window and the reminder listener together; saves the theme
  theme.py          the site's colours as a palette and a style sheet, light and dark; the fonts
  icons.py          the site's line icons, drawn in the colour of the theme, and Clara's portrait
  widgets.py        panels, badges, notices, tables, dialogs, and `Calls` (how a page talks to the server)
  shell.py          the frame: the rail, the page header, the settings tabs, the rail as a drawer
  chat_window.py    the window: the chat page (box, send/stop, documents, model picker) and what the rail asks of it
  chat_view.py      the conversation: bubbles, Clara's portrait, the line of what she used, the greeting
  history.py        the conversations in the rail: search, days, projects, menu
  projects_page.py  Projects          tasks_page.py     Tasks            files_page.py    Files
  memory_page.py    Memory            account_page.py   Account          discord_page.py  Discord
  admin_page.py     Admin: users, models, server, people & memory, console
  qcm.py            the card of a QCM Clara asks, and the message of its answers
  mathview.py       text with formulas: Markdown to HTML, and the web view that typesets it with KaTeX
  settings_dialog.py  server / token / identity, with a connection test
  documents.py      reading attached files (PDF with pypdf, code and text), putting them in the message,
                    and taking them out of a message shown again
  api.py            the Clara server over HTTP (Qt-free, blocking)
  workers.py        threads: one reply, the reminder / notification / server-state stream, reading
                    documents, a connection test, any other call to the server
  status.py, config.py, autostart.py, single.py, tray.py, icon.py   the state words, %APPDATA%\clara-app\config.json,
                    the "Start with Windows" value, one copy per user, the notification-area icon
  fonts/, clara.png, clara.ico   Hanken Grotesk and Epilogue (static copies of the site's), Clara's portrait
tests/              Qt runs offscreen, against a small fake Clara server
```

## Development

```powershell
.venv\Scripts\python -m pytest
```
