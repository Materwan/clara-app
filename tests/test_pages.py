"""The pages of the web site, in the app: Account, Memory, Files, Discord and Admin, against the fake server."""

from __future__ import annotations

from conftest import USER_TOKEN, wait_until
from PySide6.QtWidgets import QDialog, QLabel

from clara_app.api import ClaraApi
from clara_app.chat_window import ChatWindow
from clara_app.config import Config
from clara_app.theme import THEME
from clara_app.widgets import ago, duration, parse_credits

NOW = "2026-10-05T10:00:00+00:00"
USAGE = {"used": 120000, "limit": 500000, "own_limit": 500000, "default_limit": 0, "resets_at": "2026-10-06T00:00:00+00:00", "remaining": 380000}


def make_window(qt, config) -> ChatWindow:
    window = ChatWindow(lambda: config, ClaraApi)
    window.show()
    return window


def user(config) -> Config:
    from dataclasses import replace

    return replace(config, token=USER_TOKEN)


def as_user(state, admin=False) -> None:
    state.routes[("GET", "/v1/auth/me")] = {
        "name": "tester", "is_admin": admin, "person": {"id": 1, "name": "Tess"}, "accounts": ["app:tester", "web:tester"], "usage": USAGE,
    }
    state.routes[("GET", "/v1/auth/sessions")] = {"sessions": [
        {"id": "a", "surface": "app", "device": "", "address": "10.0.0.2", "created_at": NOW, "last_used_at": NOW, "current": True},
        {"id": "b", "surface": "web", "device": "Edge on Windows", "address": "", "created_at": NOW, "last_used_at": NOW, "current": False},
    ]}


def open_page(window, name):
    window.go(name)
    return window.shell.pages[name]


# ---- small things ------------------------------------------------------------------------------------------------------


def test_credits_are_read_as_the_site_reads_them():
    assert [parse_credits(text) for text in ("500000", "500k", "2m", "off", "0", "1.5k", "abc", "1.0001k", "")] == [500000, 500000, 2000000, 0, 0, 1500, None, None, None]
    assert duration(93000) == "1d 1h" and duration(7200) == "2h 00m" and duration(300) == "5m"
    assert ago(None) == "never" and ago("not a date") == "never"


# ---- account ---------------------------------------------------------------------------------------------------------


def test_the_account_page_shows_who_you_are_your_usage_and_your_devices(qt, config, server):
    _, state = server
    as_user(state, admin=True)
    window = make_window(qt, user(config))
    page = open_page(window, "account")
    wait_until(lambda: window.shell.is_admin)
    wait_until(lambda: page.data.get("sessions"))
    assert window.shell.tabs["admin"].isVisibleTo(window.shell) and window.shell.tabs["discord"].isVisibleTo(window.shell)
    texts = [label.text() for label in page.findChildren(QLabel)]
    assert any("Signed in as tester" in text for text in texts)
    assert any("120,000 / 500,000 credits" in text for text in texts)
    assert any("Edge on Windows" in text for text in texts) and any(text == "Desktop app" for text in texts)
    window.quit_for_good()


def test_a_shared_client_token_is_told_what_it_cannot_see(qt, config, server):
    window = make_window(qt, config)  # the client token, not a user's
    page = open_page(window, "account")
    wait_until(lambda: page.data)
    assert page.data["me"] is None and not window.shell.is_admin
    assert any("shared client token" in label.text() for label in page.findChildren(QLabel))
    window.quit_for_good()


def test_the_delay_of_a_finished_task_is_chosen_and_saved(qt, config, server):
    _, state = server
    state.notify_after = 300
    window = make_window(qt, user(config))
    page = open_page(window, "account")
    wait_until(lambda: page.notify_mode is not None)
    assert (page.notify_mode.currentData(), page.notify_seconds.value()) == ("after", 300)
    page.notify_mode.setCurrentIndex(page.notify_mode.findData("never"))
    page.notify_save.click()
    wait_until(lambda: state.notify_after == 0)
    page.notify_mode.setCurrentIndex(page.notify_mode.findData("default"))
    page.notify_save.click()
    wait_until(lambda: state.notify_after is None and page.status.text() == "Saved.")
    page.notify_mode.setCurrentIndex(page.notify_mode.findData("after"))
    page.notify_seconds.setValue(90)
    page.notify_save.click()
    wait_until(lambda: state.notify_after == 90)
    window.quit_for_good()


def test_a_refused_delay_says_so(qt, config, server):
    _, state = server
    window = make_window(qt, user(config))
    page = open_page(window, "account")
    wait_until(lambda: page.notify_mode is not None)
    state.signed_out = True  # the token is refused from now on
    page.notify_save.click()
    wait_until(lambda: page.status.property("tone") == "bad")
    window.quit_for_good()


def test_the_models_on_offer_are_chosen_for_the_app(qt, config, server):
    _, state = server
    state.offered_models = [
        {"ref": "cloud:big", "name": "big", "provider_label": "Ollama API key", "weight": 8.75},
        {"ref": "local:small", "name": "small", "provider_label": "Local host", "weight": 0.125},
    ]
    window = make_window(qt, user(config))
    page = open_page(window, "account")
    wait_until(lambda: page.model_box is not None)
    box = page.model_box
    assert [box.itemData(i) for i in range(box.count())] == [None, "cloud:big", "local:small"]
    assert "fake-model" in box.itemText(0) and "0.4 credits per token" in box.itemText(0)
    assert "8.75 credits per token" in box.itemText(1) and "0.125 credits per token" in box.itemText(2)
    box.setCurrentIndex(1)
    box.activated.emit(1)
    wait_until(lambda: state.model_choice == "cloud:big" and page.status.text() == "Saved.")
    # the chat window's own quiet picker offers the same models, and follows the choice
    wait_until(lambda: window.model_box.count() == 3 and window.model_box.currentData() == "cloud:big")
    state.offered_models = []  # an administrator took it away meanwhile
    box.setCurrentIndex(2)
    box.activated.emit(2)
    wait_until(lambda: page.status.property("tone") == "bad")
    window.quit_for_good()


def test_without_models_on_offer_the_page_says_the_server_answers(qt, config, server):
    window = make_window(qt, user(config))
    page = open_page(window, "account")
    wait_until(lambda: page.data)
    assert page.model_box is None and not window.model_box.isVisibleTo(window)
    assert any("has not offered other models" in label.text() for label in page.findChildren(QLabel))
    window.quit_for_good()


def test_the_theme_is_chosen_on_the_account_page(qt, config, server):
    window = make_window(qt, config)
    chosen = []
    window.theme_chosen.connect(chosen.append)
    page = open_page(window, "account")
    wait_until(lambda: page.data)
    window.set_theme("dark")
    assert chosen == ["dark"] and THEME.mode == "dark" and THEME.t["bg"] == "#0a1a2b"
    window.set_theme("light")
    assert THEME.mode == "light" and THEME.t["bg"] == "#eceFea"
    window.quit_for_good()


def test_signing_out_asks_the_application(qt, config, server, monkeypatch):
    from PySide6.QtWidgets import QMessageBox

    window = make_window(qt, config)
    asked = []
    window.signed_out.connect(lambda: asked.append(True))
    page = open_page(window, "account")
    wait_until(lambda: page.data)
    monkeypatch.setattr(QMessageBox, "exec", lambda self: 0)  # cancelled
    page.sign_out()
    assert asked == []
    monkeypatch.setattr(QMessageBox, "exec", lambda self: self.buttons()[0].click() or 0)
    monkeypatch.setattr(QMessageBox, "clickedButton", lambda self: self.buttons()[0])
    page.sign_out()
    assert asked == [True]
    window.quit_for_good()


# ---- memory ------------------------------------------------------------------------------------------------------------


def test_what_clara_remembers_is_listed_added_and_forgotten(qt, config, server):
    _, state = server
    facts = [{"id": 1, "text": "Likes jazz."}, {"id": 2, "text": "Works on a thesis."}]

    def listed(body, query, path):
        return {"facts": list(facts)}

    def added(body, query, path):
        facts.append({"id": 3, "text": body["text"]})
        return 201, {"id": 3}

    def forgotten(body, query, path):
        facts[:] = [f for f in facts if f["id"] != int(path.rsplit("/", 1)[1])]
        return {"deleted": True}

    state.routes[("GET", "/v1/memory/facts")] = listed
    state.routes[("POST", "/v1/memory/facts")] = added
    state.routes[("DELETE", "/v1/memory/facts/")] = forgotten
    window = make_window(qt, config)
    page = open_page(window, "memory")
    wait_until(lambda: page.loaded)
    assert page.count.text() == "2 things" and page.list_panel.body.count() == 3  # two facts and the rule between
    page.filter.setText("jazz")
    assert page.count.text() == "1 of 2"
    page.filter.clear()
    page.add_field.setText("Has a cat")
    page.add()
    wait_until(lambda: len(page.facts) == 3)
    assert page.add_field.text() == ""
    assert ("POST", "/v1/memory/facts", {"surface": "app", "user_id": "tester", "text": "Has a cat"}) in state.requests
    page.forget(page.facts[0])
    wait_until(lambda: len(page.facts) == 2 and page.status.text() == "Forgotten.")
    assert [f["text"] for f in facts] == ["Works on a thesis.", "Has a cat"]
    window.quit_for_good()


def test_an_empty_memory_invites_to_talk(qt, config, server):
    _, state = server
    state.routes[("GET", "/v1/memory/facts")] = {"facts": []}
    window = make_window(qt, config)
    page = open_page(window, "memory")
    wait_until(lambda: page.loaded)
    assert page.count.text() == "0 things"
    window.quit_for_good()


# ---- files -------------------------------------------------------------------------------------------------------------


def test_the_files_clara_wrote_are_read_and_deleted(qt, config, server, monkeypatch):
    _, state = server
    files = [{"id": 1, "name": "notes.md", "size": 20, "updated_at": NOW}]
    state.routes[("GET", "/v1/markdown-files")] = lambda b, q, p: {"files": list(files)}
    state.routes[("GET", "/v1/markdown-files/")] = {"id": 1, "name": "notes.md", "size": 20, "updated_at": NOW, "text": "# Notes\n\n**bold**"}
    state.routes[("DELETE", "/v1/markdown-files/")] = lambda b, q, p: (files.clear() or {"deleted": True})
    window = make_window(qt, config)
    page = open_page(window, "files")
    wait_until(lambda: page.files)
    page.open_file(1)  # a click on its name
    wait_until(lambda: page.file is not None)
    assert page.name.text() == "notes.md" and "Notes" in page.preview.toPlainText() and "# Notes" in page.source.toPlainText()
    page.mode.select("source")
    page._mode("source")
    assert page.stack.currentWidget() is page.source
    monkeypatch.setattr("clara_app.files_page.confirm", lambda *args, **kwargs: True)
    page.delete()
    wait_until(lambda: not page.files and not page.empty.isHidden())
    window.quit_for_good()


# ---- discord and admin ------------------------------------------------------------------------------------------------


def discord_payload(state_name="running"):
    return {
        "bot": {"state": state_name, "available": True, "token_set": True, "auto_start": True, "user": "Clara#0001", "guilds": 1, "latency_ms": 40,
                "uptime_seconds": 600, "invite_url": "https://discord.com/x", "last_error": ""},
        "default_chime": False,
        "spaces": [{"id": "discord:guild:1", "name": "Friends", "chime": None, "present": True}],
        "accounts": [{"user_id": "42", "discord_name": "lea#42", "user": "lea", "person": "Lea"}],
    }


def test_the_discord_page_shows_the_bot_the_servers_and_the_accounts(qt, config, server, monkeypatch):
    _, state = server
    as_user(state, admin=True)
    state.routes[("GET", "/v1/admin/discord")] = lambda b, q, p: discord_payload()
    state.routes[("POST", "/v1/admin/discord/restart")] = {"output": "Discord bot: restarting"}
    state.routes[("PATCH", "/v1/admin/spaces/")] = {"ok": True}
    window = make_window(qt, user(config))
    window.shell.set_admin(True)
    page = open_page(window, "discord")
    wait_until(lambda: page.found)
    texts = [label.text() for label in page.findChildren(QLabel)]
    assert "Running" in texts and "Clara#0001" in texts and "Friends" in texts and "lea#42" in texts
    page._act("restart")
    wait_until(lambda: ("POST", "/v1/admin/discord/restart", {}) in state.requests)
    page._save(lambda api: api.discord_space_chime("discord:guild:1", True))
    wait_until(lambda: ("PATCH", "/v1/admin/spaces/discord%3Aguild%3A1", {"chime": True}) in state.requests)
    window.quit_for_good()


def test_the_discord_page_tells_an_unreachable_admin_endpoint(qt, config, server):
    _, state = server
    window = make_window(qt, user(config))
    window.shell.set_admin(True)
    page = open_page(window, "discord")
    wait_until(lambda: page.status.text() != "")
    assert page.status.property("tone") == "bad"
    window.quit_for_good()


def test_the_admin_page_lists_users_models_and_runs_console_commands(qt, config, server, monkeypatch):
    _, state = server
    as_user(state, admin=True)
    state.routes[("GET", "/v1/admin/limits")] = {"default": 0}
    state.routes[("GET", "/v1/admin/users")] = {"users": [
        {"name": "tester", "is_admin": True, "disabled": False, "person": {"id": 1, "name": "Tess"}, "surfaces": ["app"], "signed_in_accounts": [], "sessions": 1,
         "usage": {"used": 10, "limit": None, "own_limit": None, "default_limit": 0}, "last_login_at": NOW, "discord_accounts": []},
        {"name": "lea", "is_admin": False, "disabled": True, "person": None, "surfaces": [], "signed_in_accounts": [], "sessions": 0,
         "usage": {"used": 410000, "limit": 500000, "own_limit": 500000, "default_limit": 0}, "last_login_at": None, "discord_accounts": []},
    ]}
    state.routes[("GET", "/v1/admin/catalog")] = {
        "default": "local:fake-model", "discord": None, "reference_b": 8, "problems": {},
        "providers": [{"id": "local", "label": "Local host", "usable": True}],
        "models": [{"ref": "local:fake-model", "name": "fake-model", "provider": "local", "provider_label": "Local host", "enabled": False,
                    "weight": 0.4, "size_b": 3.2, "auto_weight": True, "listed": True}],
    }
    state.routes[("PATCH", "/v1/admin/catalog")] = lambda body, q, p: {
        "default": "local:fake-model", "discord": None, "reference_b": 8, "problems": {},
        "providers": [{"id": "local", "label": "Local host", "usable": True}],
        "models": [{"ref": "local:fake-model", "name": "fake-model", "provider": "local", "provider_label": "Local host", "enabled": body["enabled"],
                    "weight": 0.4, "size_b": 3.2, "auto_weight": True, "listed": True}],
    }
    state.routes[("POST", "/v1/admin/command")] = lambda body, q, p: {"output": f"ran {body['line']}", "quit": False}
    window = make_window(qt, user(config))
    window.shell.set_admin(True)
    page = open_page(window, "admin")
    users = page.tabs["users"]
    wait_until(lambda: len(users.users) == 2)
    assert users.count.text() == "2 people can sign in" and users.default_button.text() == "Default limit: none"
    assert users._usage_text(users.users[1]) == "410,000 / 500,000"
    page.show_tab("models")
    models = page.tabs["models"]
    wait_until(lambda: models.data is not None)
    assert models.count.text() == "0 of 1 selected"
    models._change(["local:fake-model"], enabled=True)
    wait_until(lambda: models.count.text() == "1 of 1 selected")
    page.show_tab("console")
    console = page.tabs["console"]
    console.input.setText("/status")
    console.run()
    wait_until(lambda: "ran /status" in console.out.toPlainText())
    assert "> /status" in console.out.toPlainText()
    window.quit_for_good()


def test_the_server_tab_shows_the_status_and_switches_the_model(qt, config, server):
    _, state = server
    state.routes[("GET", "/v1/admin/status")] = {
        "uptime_seconds": 7200, "stopping": False, "turns": {"running": 0, "since_start": 4}, "tokens": {"prompt": 1000, "completion": 200},
        "people": 3, "facts": 20, "listen": "127.0.0.1:8765", "tailscale": {"mode": "off"}, "model": "a",
        "provider": {"id": "local", "label": "Local host"}, "providers": [{"id": "local", "label": "Local host", "usable": True}],
    }
    state.routes[("GET", "/v1/admin/models")] = {"models": ["a", "b"], "model": "a"}
    state.routes[("POST", "/v1/admin/command")] = lambda body, q, p: {"output": "Model: b", "quit": False}
    window = make_window(qt, user(config))
    window.shell.set_admin(True)
    page = open_page(window, "admin")
    page.show_tab("server")
    server_tab = page.tabs["server"]
    wait_until(lambda: server_tab.model.count() == 2)
    server_tab.model.setCurrentIndex(1)
    server_tab._run(f"/model {server_tab.model.currentData()}")
    wait_until(lambda: server_tab.status.text() == "Model: b")
    assert ("POST", "/v1/admin/command", {"line": "/model b"}) in state.requests
    page.hide()
    window.quit_for_good()


def test_a_dialog_signs_a_discord_account_in_as_a_user(qt, config, server):
    from clara_app.discord_page import SignInDialog

    _, state = server
    state.routes[("GET", "/v1/admin/discord/members")] = {"running": True, "members": [{"user_id": "7", "name": "bob", "display_name": "Bob", "user": None}]}
    dialog = SignInDialog(lambda: config, ClaraApi, [{"name": "tester", "person": {"name": "Tess"}}])
    dialog.query.setText("Bo")
    wait_until(lambda: dialog.results.count() == 1)
    dialog._pick(dialog.results.item(0))
    assert dialog.chosen["user_id"] == "7" and dialog.sign_in.isEnabled()
    dialog.query.setText("<@1234>")  # a pasted id is taken as it is
    dialog._search()
    assert dialog.chosen == {"user_id": "1234", "display_name": "Discord id 1234"}
    dialog.done(QDialog.DialogCode.Rejected)


# ---- the work pages, as on the web site ----------------------------------------------------------------------------------


def test_the_conversations_stay_in_the_rail_on_the_work_pages_but_not_on_the_settings_pages(qt, config, server):
    _, state = server
    state.add_conversation("app:tester:a", ("hello", "hi"), title="Tea")
    window = make_window(qt, config)
    window.start_history()
    for name in ("chat", "projects", "tasks", "files"):
        window.go(name)
        assert not window.history.isHidden(), name
    for name in ("memory", "account"):
        window.go(name)
        assert window.history.isHidden(), name
    window.go("chat")
    assert not window.history.isHidden()
    window.quit_for_good()


def test_projects_are_cards_that_open_their_own_page_with_their_chats(qt, config, server):
    _, state = server
    thesis = state.add_project("Thesis", **{"notes.md": "chapters"})
    state.add_project("Clara")
    state.add_conversation("app:tester:a", ("outline", "ok"), title="Outline")
    state.conversations["app:tester:a"]["project"] = thesis["id"]
    window = make_window(qt, config)
    window.start_history()
    page = open_page(window, "projects")
    wait_until(lambda: len(page._cards) == 2 and page.stack.currentWidget() is page.grid_page)
    wait_until(lambda: window.history.conversations)
    page._cards[0].clicked.emit()  # a click on the card of Thesis
    wait_until(lambda: page.stack.currentWidget() is page.detail and page.project is not None)
    assert page.project_title.text() == "Thesis" and page.files.count() == 1
    from PySide6.QtWidgets import QPushButton

    assert [b.text().split("  ")[0] for b in page.chats_panel.findChildren(QPushButton)] == ["Outline"]  # the project's chats, on its page
    page.close_project()
    assert page.stack.currentWidget() is page.grid_page
    window.quit_for_good()


def test_the_calendar_places_deadlines_and_reminders_on_their_days():
    from clara_app.calendar_view import events_of

    tasks = [
        {"id": 1, "title": "Invoice", "status": "open", "due_at": "2026-10-09T09:00:00+00:00", "reminders": ["2026-10-08T17:00:00+00:00", "2026-10-09T08:00:00+00:00"]},
        {"id": 2, "title": "Done one", "status": "done", "due_at": "2026-10-09T10:00:00+00:00", "reminders": ["2026-10-09T07:00:00+00:00"]},
    ]
    found = events_of(tasks)
    days = {day.isoformat(): [(e["title"], e["kind"]) for e in events] for day, events in found.items()}
    assert sorted(days["2026-10-09"]) == [("Done one", "due"), ("Invoice", "due"), ("Invoice", "reminder")]
    assert ("Invoice", "reminder") in days["2026-10-08"]  # a done task keeps its deadline but has no reminder to come
    assert all(not (title == "Done one" and kind == "reminder") for events in days.values() for title, kind in events)


def test_a_task_is_ticked_from_its_row_and_opened_in_a_dialog(qt, config, server):
    _, state = server
    state.add_task("Taxes", due="2026-10-30T09:00:00+00:00")
    window = make_window(qt, config)
    page = open_page(window, "tasks")
    wait_until(lambda: page.list.count() == 1)
    page.edit_task(page.tasks[0]["id"])
    assert page.form_dialog.isVisible() and page.title.text() == "Taxes"
    page.form_dialog.hide()
    page.views.select("calendar")
    page._view("calendar")
    assert page.stack.currentIndex() == 1
    window.quit_for_good()
