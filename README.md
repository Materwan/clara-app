# Clara app

A small desktop chat for a [Clara server](../clara-server/README.md). It lives in the Windows
notification area: **click the icon to open the window**, click it again (or close the window) to
put it away. The app keeps running in the tray.

- Chat with Clara; the answer streams in and is shown as Markdown.
- **Conversations**, as in other chat apps: ☰ opens the list at the side of the window (which grows to its
  left), pinned ones first, then by day (*Today*, *Yesterday*, *Previous 7 days*...). Click one to read it
  again and go on with it; **New chat** starts another, and the others stay. Clara gives each a short title
  after its first answer (until then it is listed by its first words). Right-click one to **rename**,
  **pin** or **delete** it; the search box looks in titles and messages. When the app starts it shows the
  conversation you used last. The list is kept by the server, so it is the same on any computer where the
  app runs with your id. While Clara writes, the list waits: an answer is kept only once it is complete.
- **Documents**: attach PDF, code (Python, C, C++…), Markdown or any text file with the 📎 button, or
  drop them on the window. They are read **on this computer** (the text of a PDF's pages; a scanned PDF
  has none) and sent with your next message; each shows as a chip above the input box, click it to take
  it off. Clara sees only the files you attach. All the documents of one message can hold about 150 000
  characters (a few dozen pages); the server also refuses a message that takes more than half of the
  model's context window.
- **Projects**: files, folders and GitHub repositories Clara uses in every conversation of the project, with
  instructions of their own. They are kept by the server, so they are the same here and on the web site. The
  **Project** list above the conversation chooses where your chats go: a new chat starts in that project, and ☰
  lists its conversations (*No project*: those in none). **Projects…** opens a dialog to make, rename, describe,
  instruct and delete projects, and fill them: *Add files…* (text, code, PDF, Word, `.zip`), *Add a folder…* (its
  text files, leaving out `node_modules`, `.venv`, `.git`, build output, images…), *GitHub…* (the server downloads
  a repository; *Sync* gets its latest version), and remove a file or a repository; double-click a file to read
  it. *New chat in this project* starts one there. Right-click a conversation in ☰, **Move to a project…**, to put
  it in another project or in none. Files are read **by the server**, not on this computer (unlike the 📎
  attachments of one message).
- **QCM**: Clara can quiz you or collect several answers at once: a form appears in the conversation with
  radio buttons (one answer), check boxes (several) or a text box, and **Send answers** sends them as your next
  message. When Clara gave the right answers the card then shows your score, what was right and wrong, and her
  explanations. A conversation opened again shows its forms, answered ones with your choices.
- **Formulas**: Clara writes LaTeX (`$x^2$`, `\(x^2\)`, `$$…$$`, `\[…\]`) and the app typesets it with KaTeX
  (the files are in `clara_app/katex/`, the same as the web site's), in her answers and in QCM questions, options
  and explanations. A message that holds a formula is shown by a small embedded web view (Qt WebEngine); the
  others stay ordinary labels, which are lighter. The view loads nothing but the local KaTeX files: no network,
  and raw HTML in an answer is shown as text. While an answer streams in, its formulas show as source; they are
  typeset when it is complete. What you type is not typeset.
- **Reminders** (`/remind` in `clara-chat` or the console, or "remind me…" to Clara) are yours only: they
  pop up as a Windows notification even when the window is hidden, when they were set for every client of
  yours or for the app (`@app`; Clara picks this herself when you say "on my desktop"). The notification
  holds **the message Clara wrote for it** (the reminder's own text if she could not), and ends with the
  state of the server: *Clara is running*. Clicking it opens the app. What fired while the app was closed
  arrives when it starts, marked *missed*.
- **Tasks**: your to-do list, the same on the web site, in the terminal and in every chat with Clara ("add a task:
  send the invoice by Friday"). **Tasks…** (next to *Projects…*, and in the icon's menu) opens a dialog: the list
  with, for each task, how many reminders were sent and the next one; to the right its title, description, deadline
  and the reminders still to come, which you can change, with *Mark as done* / *Reopen* and *Delete*. A task without
  a reminder gets them chosen by Clara, and each time one is sent she looks at the task again and may move the next
  ones, so the dialog reads the list again every 30 seconds. A task reminder pops up like any notification
  (*Task: …*).
- **Notifications** pop up the same way: from Clara (when she has finished a long task you asked about),
  from the server (an answer that took long is ready, the conversation was summarised, the model changed) or
  from another client. One about the conversation you are looking at, while the window is in front, is
  only noted in the conversation.
- **The state of the server** is always shown: in the window ("● Clara is running", "◐ Clara is
  stopping", "○ Clara is not running"), in the icon's tooltip, and by a notification whenever it
  changes: *Clara is stopping: she finishes what is running and takes nothing new*, *Clara is not
  running*, *Clara is running again*. Asking a question while she is stopping says so.
- Right-click the icon: Open, Tasks…, Settings…, **Start with Windows**, Quit. The icon (`clara_app/clara.ico`, the same
  picture as the web site's) turns grey when Clara is not running.
- **The model**: Settings lists the models an administrator lets you choose, with what a token of each costs in
  credits (a bigger model uses your daily credits faster); your choice is for the app only, and Discord's model is the
  administrators'. When none is offered, Clara answers with the server's own model.
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
`%APPDATA%\clara-app\config.json` as plain text (like the `.env` files of the other clients). If the token stops
working (you were signed out, the password changed) the app says so: open Settings and type the password again.
A shared client token (an entry of `CLARA_TOKENS`) still works in the *Sign-in token* field, or `CLARA_TOKEN` in the
environment; `CLARA_URL` fills in the server when the file leaves it empty. For a server reached through Tailscale, type its `https://<machine>.<tailnet>.ts.net`
address (the dialog warns about plain `http://` to another machine).

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
| **Stop** (the Send button while Clara writes) | stop the answer; what has arrived is kept |
| **☰** | show / hide your conversations |
| **New chat** | start a new conversation; the one shown stays in the list |
| right click in the list | rename, pin / unpin, move to a project, delete (erased on the server; what Clara knows about you stays) |
| **Settings** | change server, token, name, the model Clara answers you with in the app (among those an administrator offers, each with its cost in credits per token; kept by the server, separately from your other clients), and when you are notified that a task is done (like the server, never, or after a number of seconds; kept by the server, so every client of yours follows it) |
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
  as their names: their text is not shown again.
- It runs no tools on your computer (files, shell...): Clara reads only the documents you attach, and
  cannot open a file by herself. The server's own tools (`remember`, `remind`, `notify`...) work.
- A scanned PDF (images of pages) has no text to read: there is no OCR.
- Windows only for the start-up entry; the rest is Qt and should run elsewhere, but the tray is
  only tested on Windows 11.

## Layout

```
src/clara_app/
  __main__.py       entry point: Qt application, single instance, --background
  app.py            ties the tray, the window and the reminder listener together
  tray.py           the notification-area icon, its menu, the notifications
  chat_window.py    the window: input box, send/stop, new chat, attached documents (button, drop), and
                    what is done with the list of conversations (open, title, rename, pin, delete)
  history.py        the list of conversations at the side: search, pinned and dated groups, its menu
  documents.py      reading attached files (PDF with pypdf, code and text), putting them in the message,
                    and taking them out of a message shown again
  chat_view.py      the conversation: bubbles rendered as Markdown
  qcm.py            the card of a QCM Clara asks, and the message of its answers
  mathview.py       text with formulas: Markdown to HTML, and the web view that typesets it with KaTeX
  settings_dialog.py  server / token / identity, with a connection test
  tasks_dialog.py   the to-do list: tasks, their reminders, add / change / done / delete
  api.py            the Clara server over HTTP (Qt-free, blocking)
  workers.py        threads: one reply, the reminder / notification / server-state stream, reading
                    documents, a connection test, any other call to the server
  status.py         the words used for the state of the server
  config.py         %APPDATA%\clara-app\config.json
  autostart.py      the "Start with Windows" registry value
  single.py         one running copy per user (a named local socket)
  icon.py           the icon (`clara.ico`, grey when the server is down)
tests/              Qt runs offscreen, against a small fake Clara server
```

## Development

```powershell
.venv\Scripts\python -m pytest
```
