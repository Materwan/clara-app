"""Integrations in the app: the folders of this computer (and the sandbox that keeps Clara inside them), the jobs the
server hands over, the Integrations page, the permission cards and the connections of a conversation."""

from __future__ import annotations

import os
import subprocess

import pytest
from conftest import USER_TOKEN, wait_until
from PySide6.QtWidgets import QDialog, QLabel

from clara_app.api import ApiError, ClaraApi
from clara_app.app import ClaraApplication, describe_approval
from clara_app.approvals import ApprovalCard, ApprovalsDialog, level_word
from clara_app.chat_window import ChatWindow
from clara_app.config import save
from clara_app.integrations_page import ConnectionsDialog, IntegrationsPage, LevelsDialog, level_badges
from clara_app.local_folders import FolderError, FolderRegistry, LocalFolders, relative, run_jobs

NOW = "2026-10-05T10:00:00+00:00"
FULL = {"read": "allow", "write": "ask", "destructive": "ask"}
APPROVAL = {
    "id": 12, "conversation": "app:tester:talk", "resource": "Docs", "resource_id": 1, "op": "write", "level": "destructive",
    "summary": "overwrite notes.md in Docs (14 characters)", "reason": "you asked me to tidy up", "status": "pending",
    "result": "", "created_at": NOW,
}
REPO = {"id": 1, "kind": "github_repo", "type": "github", "label": "erwan/clara", "account": 1, "locator": {}, "levels": {"write": "allow"},
        "effective": {"read": "allow", "write": "allow", "destructive": "ask"}, "attachments": 2, "created_at": NOW}
OVERVIEW = {
    "types": [{"id": t, "name": t, "enabled": True, "available": True, "ceiling": {}, "defaults": FULL} for t in ("github", "gdrive", "server", "computer")],
    "accounts": [{"id": 1, "kind": "github", "label": "erwan", "status": "ok", "levels": {}, "created_at": NOW}],
    "resources": [REPO], "roots": [], "pending": 1,
    "settings": {"approval_notify_after": None, "default": 60, "expire_after": 86400},
}


def user(config):
    from dataclasses import replace

    return replace(config, token=USER_TOKEN)


def texts(widget) -> list[str]:
    return [label.text() for label in widget.findChildren(QLabel)]


@pytest.fixture
def registry(tmp_path):
    return FolderRegistry.load(tmp_path / "computer-folders.json")


@pytest.fixture
def docs(tmp_path, registry):
    folder = tmp_path / "docs"
    (folder / "src").mkdir(parents=True)
    (folder / "notes.md").write_bytes(b"one\ntwo\nthree\n")
    (folder / "src" / "main.py").write_bytes(b"print('hello')\n")
    return folder, registry.add(folder), LocalFolders(registry)


# ---- the folders of this computer ----------------------------------------------------------------------------------------


def test_a_computer_has_a_stable_id_and_a_list_of_folders(tmp_path):
    first = FolderRegistry.load(tmp_path / "f.json")
    assert first.device and first.name
    (tmp_path / "Work Files").mkdir()
    alias = first.add(tmp_path / "Work Files")
    assert alias == "Work-Files" and first.add(tmp_path / "Work Files") == alias  # the same folder: the same alias
    (tmp_path / "other").mkdir()
    (tmp_path / "other" / "Work Files").mkdir()
    assert first.add(tmp_path / "other" / "Work Files") == "Work-Files-2"
    again = FolderRegistry.load(tmp_path / "f.json")
    assert again.device == first.device and set(again.folders) == {"Work-Files", "Work-Files-2"}
    again.remove("Work-Files")
    assert set(FolderRegistry.load(tmp_path / "f.json").folders) == {"Work-Files-2"}
    with pytest.raises(FolderError):
        again.add(tmp_path / "missing")


def test_clara_lists_reads_and_searches_a_folder(docs):
    folder, alias, local = docs
    assert local.run(alias, "list", {}).splitlines() == ["src/", "notes.md (14 bytes)"]
    read = local.run(alias, "read", {"path": "notes.md", "start_line": 2})
    assert "two" in read and "one" not in read
    assert "src/main.py:1: print('hello')" in local.run(alias, "search", {"query": "HELLO"})
    assert "No match" in local.run(alias, "search", {"query": "absent"})


def test_clara_writes_changes_moves_and_deletes_inside_a_folder(docs):
    folder, alias, local = docs
    assert local.run(alias, "write", {"path": "a/b/new.txt", "content": "hi", "mode": "create"}) == "Created a/b/new.txt (2 characters)."
    assert (folder / "a" / "b" / "new.txt").read_text() == "hi"
    with pytest.raises(FolderError, match="already exists"):
        local.run(alias, "write", {"path": "notes.md", "content": "x", "mode": "create"})
    assert local.run(alias, "write", {"path": "notes.md", "content": "!", "mode": "append"}) == "Added to notes.md (1 characters)."
    assert (folder / "notes.md").read_bytes().endswith(b"three\n!")
    assert local.run(alias, "write", {"path": "notes.md", "content": "new", "mode": "overwrite"}).startswith("Replaced")
    local.run(alias, "move", {"path": "notes.md", "dest": "old.md"})
    with pytest.raises(FolderError, match="already exists"):
        local.run(alias, "move", {"path": "old.md", "dest": "src/main.py"})
    local.run(alias, "delete", {"path": "old.md"})
    assert not (folder / "old.md").exists()
    with pytest.raises(FolderError, match="something in it"):
        local.run(alias, "delete", {"path": "src"})
    with pytest.raises(FolderError, match="itself"):
        local.run(alias, "delete", {"path": ""})
    with pytest.raises(FolderError, match="does not do"):
        local.run(alias, "format", {})


@pytest.mark.parametrize("path", ["../secret.txt", "src/../../secret.txt", "/etc/passwd", "C:\\Windows\\win.ini", "..\\x"])
def test_nothing_outside_the_folder_can_be_reached(docs, path):
    folder, alias, local = docs
    (folder.parent / "secret.txt").write_text("nope")
    for op, args in (("read", {"path": path}), ("write", {"path": path, "content": "x", "mode": "overwrite"}), ("delete", {"path": path})):
        with pytest.raises(FolderError):
            local.run(alias, op, args)
    assert (folder.parent / "secret.txt").read_text() == "nope"


def test_a_link_that_leaves_the_folder_is_refused(docs, tmp_path):
    folder, alias, local = docs
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("nope")
    try:
        os.symlink(outside, folder / "link", target_is_directory=True)
    except (OSError, NotImplementedError):
        made = subprocess.run(["cmd", "/c", "mklink", "/J", str(folder / "link"), str(outside)], capture_output=True)
        if os.name != "nt" or made.returncode != 0:
            pytest.skip("this system does not let a test make links")
    with pytest.raises(FolderError, match="outside"):
        local.run(alias, "read", {"path": "link/secret.txt"})


def test_a_folder_that_was_removed_from_the_app_cannot_be_reached_any_more(docs, registry):
    _, alias, local = docs
    registry.remove(alias)
    with pytest.raises(FolderError, match="not on this computer"):
        local.run(alias, "list", {})
    assert relative("./a//b/") == "a/b" and relative("/") == ""


# ---- the jobs of the server ----------------------------------------------------------------------------------------------


class FakeApi:
    def __init__(self, jobs):
        self.jobs, self.finished, self.devices = jobs, [], []

    def computer_jobs(self, device):
        self.devices.append(device)
        return self.jobs

    def finish_job(self, job, ok, text):
        if job == 99:
            raise ApiError("gone")
        self.finished.append((job, ok, text))


def test_the_jobs_are_done_and_each_one_is_answered(docs, registry):
    folder, alias, local = docs
    api = FakeApi([
        {"id": 1, "op": "read", "alias": alias, "args": {"path": "notes.md"}},
        {"id": 2, "op": "write", "alias": alias, "args": {"path": "../x", "content": "y", "mode": "create"}},
        {"id": 3, "op": "list", "alias": "never-added", "args": {}},
        {"id": 99, "op": "list", "alias": alias, "args": {}},
    ])
    assert run_jobs(api, local) == 3  # the one the server no longer wants is not counted
    assert api.devices == [registry.device]
    by_job = {job: (ok, text) for job, ok, text in api.finished}
    assert by_job[1][0] and "three" in by_job[1][1]
    assert by_job[2] == (False, "A path may not go up (..).") and not (folder.parent / "x").exists()
    assert not by_job[3][0] and "not on this computer" in by_job[3][1]


def test_the_api_asks_for_jobs_as_this_computer(config, server):
    _, state = server
    state.routes[("GET", "/v1/integrations/jobs")] = lambda body, query, path: {"jobs": [{"id": 5, "op": "list", "alias": "docs", "args": {}}]}
    state.routes[("POST", "/v1/integrations/jobs/5/result")] = {"ok": True}
    api = ClaraApi(config)
    assert api.computer_jobs("pc-1")[0]["id"] == 5
    api.finish_job(5, True, "done")
    assert state.requests[-1] == ("POST", "/v1/integrations/jobs/5/result", {"surface": "app", "user_id": "tester", "ok": True, "text": "done"})


# ---- the cards and the badge ----------------------------------------------------------------------------------------


def test_a_card_says_what_where_and_how_far_and_approving_sends_the_answer(qt, config, server):
    _, state = server
    state.routes[("POST", "/v1/approvals/12/decide")] = {**APPROVAL, "status": "done", "result": "Replaced notes.md (3 characters)."}
    card = ApprovalCard(APPROVAL, lambda: config, ClaraApi)
    seen = []
    card.decided.connect(seen.append)
    shown = texts(card)
    assert "overwrite notes.md in Docs (14 characters)" in shown and "Replace or delete" in shown
    assert any("you asked me to tidy up" in text for text in shown) and any("Nothing is done until you approve" in text for text in shown)
    card.approve_button.click()
    wait_until(lambda: seen)
    assert state.requests[-1][2] == {"surface": "app", "user_id": "tester", "approve": True, "remember": ""}
    assert "Approved and done. Replaced notes.md" in card.status.text() and not card.actions.isVisibleTo(card)
    card.shutdown()


def test_denying_and_remembering_are_sent_as_chosen(qt, config, server):
    _, state = server
    state.routes[("POST", "/v1/approvals/12/decide")] = {**APPROVAL, "status": "denied"}
    card = ApprovalCard(APPROVAL, lambda: config, ClaraApi)
    seen = []
    card.decided.connect(seen.append)
    card.deny_button.click()
    wait_until(lambda: seen)
    assert state.requests[-1][2]["approve"] is False and card.status.text() == "Denied. Nothing was done."
    other = ApprovalCard({**APPROVAL, "id": 13}, lambda: config, ClaraApi)
    state.routes[("POST", "/v1/approvals/13/decide")] = {**APPROVAL, "id": 13, "status": "done", "result": "ok"}
    done = []
    other.decided.connect(done.append)
    other._decide(True, "conversation")
    wait_until(lambda: done)
    assert state.requests[-1][2]["remember"] == "conversation"
    card.shutdown()
    other.shutdown()


def test_a_request_answered_elsewhere_is_settled_not_failed(qt, config, server):
    _, state = server
    state.routes[("POST", "/v1/approvals/12/decide")] = (409, {"detail": "This request is already denied."})
    card = ApprovalCard(APPROVAL, lambda: config, ClaraApi)
    seen = []
    card.decided.connect(seen.append)
    card.approve_button.click()
    wait_until(lambda: seen)
    assert card.status.text() == "Already answered." and seen[0]["status"] == "answered"
    card.shutdown()


def test_a_failure_leaves_the_buttons_to_try_again(qt, config, server):
    _, state = server
    state.routes[("POST", "/v1/approvals/12/decide")] = (502, {"detail": "GitHub could not be reached"})
    card = ApprovalCard(APPROVAL, lambda: config, ClaraApi)
    card.approve_button.click()
    wait_until(lambda: "GitHub could not be reached" in card.status.text())
    assert card.actions.isEnabled()
    card.shutdown()


def test_the_list_of_requests_waiting_opens_from_the_rail(qt, config, server):
    _, state = server
    state.routes[("GET", "/v1/approvals")] = {"approvals": [APPROVAL, {**APPROVAL, "id": 13, "summary": "delete old.md in Docs"}]}
    dialog = ApprovalsDialog(lambda: config, ClaraApi)
    wait_until(lambda: len(dialog.cards) == 2)
    assert [t for t in texts(dialog) if t.startswith("In ")] == ["In app:tester:talk"] * 2
    assert state.requests[-1][0] == "GET"
    dialog.reject()


def test_the_rail_counts_what_waits_and_hides_when_nothing_does(qt, config, server):
    window = ChatWindow(lambda: config, ClaraApi)
    window.show()
    shell = window.shell
    assert not shell.approvals_button.isVisibleTo(shell)
    shell.set_approvals(3)
    assert shell.approvals_button.isVisibleTo(shell) and "3" in shell.approvals_button.text()
    shell.set_approvals(1)
    assert "request from Clara" in shell.approvals_button.toolTip()
    shell.set_approvals(0)
    assert not shell.approvals_button.isVisibleTo(shell)
    window.quit_for_good()


def test_the_words_and_badges_of_permissions(qt):
    assert [level_word(level) for level in ("read", "write", "destructive")] == ["Look", "Add or change", "Replace or delete"]
    row = level_badges({"read": "allow", "write": "ask", "destructive": "deny"})  # kept: the badges are its children
    assert texts(row) == ["Look · allow", "Change · ask me", "Replace · never"]


def test_a_request_reaching_this_device_is_announced_with_what_and_where(qt, config, tmp_path, server):
    path = tmp_path / "config.json"
    save(config, path)
    application = ClaraApplication(qt, config_path=path)
    shown = []
    application.tray.notify = lambda title, text: shown.append((title, text))
    event = {"type": "approval", "approval": 12, "summary": "overwrite notes.md in Docs", "resource": "Docs", "text": "x"}
    assert describe_approval(event) == ("Clara needs your permission", "overwrite notes.md in Docs\nOn Docs")
    _, state = server
    state.routes[("GET", "/v1/approvals")] = {"approvals": [APPROVAL]}
    application.on_approval(event)
    assert shown == [("Clara needs your permission", "overwrite notes.md in Docs\nOn Docs")]
    wait_until(lambda: application.window.shell.approvals_button.isVisibleTo(application.window.shell) or application.window.shell.approvals_button.text().endswith("1"))
    application.quit()


def test_a_click_on_that_notification_opens_the_requests_but_a_later_one_only_the_window(qt, config, tmp_path):
    path = tmp_path / "config.json"
    save(config, path)
    application = ClaraApplication(qt, config_path=path)
    opened = []
    application.window.show_approvals = lambda: opened.append("requests")
    application._notice_clicked()
    assert opened == []  # no request was announced
    application._approval_notice_at = __import__("time").monotonic()
    application._notice_clicked()
    assert opened == ["requests"]
    application.quit()


def test_the_jobs_run_only_when_there_is_a_folder_to_work_in(qt, config, tmp_path, monkeypatch, server):
    path = tmp_path / "config.json"
    save(config, path)
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    application = ClaraApplication(qt, config_path=path)
    _, state = server
    state.routes[("GET", "/v1/integrations/jobs")] = lambda body, query, p: {"jobs": []}
    application.do_jobs()  # no folder yet: nothing is asked of the server
    assert not any(path.startswith("/v1/integrations/jobs") for _m, path, _b in state.requests)
    folder = tmp_path / "work"
    folder.mkdir()
    FolderRegistry.load().add(folder)
    application.do_jobs()
    wait_until(lambda: not application._jobs_running)
    assert any(path.startswith("/v1/integrations/jobs") for _m, path, _b in state.requests)
    application.quit()


# ---- the page --------------------------------------------------------------------------------------------------------------


def as_user(state, admin=False):
    state.routes[("GET", "/v1/auth/me")] = {"name": "tester", "is_admin": admin, "person": {"id": 1, "name": "Tess"}, "accounts": [], "usage": {}}
    state.routes[("GET", "/v1/integrations")] = OVERVIEW
    state.routes[("GET", "/v1/approvals")] = {"approvals": [APPROVAL]}


def open_page(window) -> IntegrationsPage:
    window.go("integrations")
    return window.shell.pages["integrations"]


def test_the_page_lists_accounts_repositories_and_what_waits(qt, config, server):
    _, state = server
    as_user(state)
    window = ChatWindow(lambda: user(config), ClaraApi)
    window.show()
    page = open_page(window)
    wait_until(lambda: page.data)
    shown = texts(page)
    assert "erwan" in shown and "Connected" in shown and "erwan/clara" in shown
    assert any("GitHub repository" in text and "used in 2 places" in text for text in shown)
    assert "Look · allow" in shown and "Change · allow" in shown and "Replace · ask me" in shown
    assert page.approvals_panel.isVisibleTo(page) and "overwrite notes.md in Docs (14 characters)" in shown
    assert not page.admin_panel.isVisibleTo(page)  # not an administrator
    assert window.shell.tabs["integrations"].isVisibleTo(window.shell)
    window.quit_for_good()


def test_an_account_that_must_be_connected_again_says_so(qt, config, server):
    _, state = server
    as_user(state)
    state.routes[("GET", "/v1/integrations")] = {**OVERVIEW, "accounts": [{**OVERVIEW["accounts"][0], "status": "needs_reconnect"}]}
    window = ChatWindow(lambda: user(config), ClaraApi)
    window.show()
    page = open_page(window)
    wait_until(lambda: page.data)
    assert "Connect it again" in texts(page)
    window.quit_for_good()


def test_google_drive_not_set_up_on_the_server_is_explained(qt, config, server):
    _, state = server
    as_user(state)
    types = [{**t, "available": t["id"] != "gdrive"} for t in OVERVIEW["types"]]
    state.routes[("GET", "/v1/integrations")] = {**OVERVIEW, "types": types}
    window = ChatWindow(lambda: user(config), ClaraApi)
    window.show()
    page = open_page(window)
    wait_until(lambda: page.data)
    assert any("GOOGLE_CLIENT_ID" in text for text in texts(page)) and not page.connect_google_button.isVisibleTo(page)
    assert page.connect_github_button.isVisibleTo(page)
    window.quit_for_good()


def test_an_administrator_sees_the_switches_and_the_log(qt, config, server):
    _, state = server
    as_user(state, admin=True)
    state.routes[("GET", "/v1/admin/integrations")] = {
        "enabled": {"github": True, "gdrive": True, "server": False, "computer": True}, "disabled_users": {}, "roots": ["/srv/clara-files"],
        "ceiling": {"github": {}, "gdrive": {"destructive": "ask"}, "server": {}, "computer": {}},
        "types": [{"id": t["id"], "name": t["name"], "available": True} for t in OVERVIEW["types"]],
    }
    state.routes[("GET", "/v1/admin/integrations/log")] = {"entries": [
        {"id": 1, "at": NOW, "person": "Tess", "conversation": "c", "resource": "Docs", "op": "write", "level": "destructive",
         "summary": "overwrite notes.md in Docs", "outcome": "done", "approval": 12}]}
    window = ChatWindow(lambda: user(config), ClaraApi)
    window.show()
    page = open_page(window)
    wait_until(lambda: page.admin_panel.isVisibleTo(page) and any("/srv/clara-files" in t for t in texts(page)))
    assert any("Tess" in text and "overwrite notes.md in Docs" in text and "done" in text for text in texts(page))
    window.quit_for_good()


def test_saving_the_delay_before_a_request_is_pushed(qt, config, server):
    _, state = server
    as_user(state)
    state.routes[("PUT", "/v1/integrations/settings")] = {"approval_notify_after": 300}
    window = ChatWindow(lambda: user(config), ClaraApi)
    window.show()
    page = open_page(window)
    wait_until(lambda: page.data)
    page.notify_box.setCurrentIndex(page.notify_box.findData("300"))
    page.notify_box.activated.emit(page.notify_box.currentIndex())
    wait_until(lambda: any(p == "/v1/integrations/settings" for _m, p, _b in state.requests))
    assert [b for _m, p, b in state.requests if p == "/v1/integrations/settings"][-1]["approval_notify_after"] == 300
    window.quit_for_good()


def test_the_permission_dialog_offers_inheritance_and_respects_the_ceiling(qt):
    dialog = LevelsDialog("Permissions", "", {"write": "allow"}, "Same as the account", {"destructive": "ask"})
    assert dialog.levels() == {"write": "allow"}  # the other levels follow the account
    box = dialog.boxes["destructive"]
    allow = box.model().item(box.findData("allow"))
    assert not allow.isEnabled() and "does not allow" in allow.text()
    box.setCurrentIndex(box.findData("deny"))
    assert dialog.levels() == {"write": "allow", "destructive": "deny"}
    own = LevelsDialog("Permissions", "", {}, "")
    assert own.levels() == {"read": "allow", "write": "ask", "destructive": "ask"}  # the defaults, made visible


# ---- connections of a conversation ---------------------------------------------------------------------------------


def attachments_routes(state, attached=None, inherited=None):
    state.routes[("GET", "/v1/integrations/attachments")] = lambda body, query, path: {
        "attachments": attached if attached is not None else [], "inherited": inherited or []}
    state.routes[("GET", "/v1/integrations")] = OVERVIEW


def test_the_connections_of_a_conversation_show_what_it_inherits_and_what_is_its_own(qt, config, server):
    _, state = server
    own = {"attachment": 5, "scope": "conversation", "levels": {}, "effective": FULL, "resource": REPO}
    inherited = {"attachment": 6, "scope": "project", "levels": {}, "effective": {**FULL, "write": "deny"}, "resource": {**REPO, "id": 2, "label": "notes", "kind": "server_path", "type": "server"}}
    attachments_routes(state, [own], [inherited])
    dialog = ConnectionsDialog({"conversation": "app:tester:talk"}, lambda: config, ClaraApi)
    wait_until(lambda: "erwan/clara" in texts(dialog))
    shown = texts(dialog)
    assert "notes" in shown and "From the project" in shown and "Change · never" in shown
    dialog.reject()


def test_detaching_and_attaching_go_to_the_server(qt, config, server):
    _, state = server
    own = {"attachment": 5, "scope": "conversation", "levels": {}, "effective": FULL, "resource": REPO}
    attachments_routes(state, [own])
    state.routes[("DELETE", "/v1/integrations/attachments/5")] = {"ok": True}
    dialog = ConnectionsDialog({"project": 3}, lambda: config, ClaraApi)
    wait_until(lambda: "erwan/clara" in texts(dialog))
    dialog._detach(own)
    wait_until(lambda: ("DELETE", "/v1/integrations/attachments/5") in [(m, p) for m, p, _b in state.requests])
    dialog.reject()


def test_the_conversation_gets_a_name_before_its_first_message_so_that_things_can_be_attached(qt, config, server, monkeypatch):
    _, state = server
    attachments_routes(state)
    window = ChatWindow(lambda: config, ClaraApi)
    window.show()
    opened = []
    monkeypatch.setattr(ConnectionsDialog, "exec", lambda self: opened.append(self.target) or QDialog.DialogCode.Rejected)
    assert window.conversation is None
    window.show_connections()
    assert window.conversation and window.conversation.startswith("app:tester:")
    assert opened == [{"conversation": window.conversation}]
    window.quit_for_good()


def test_a_request_asked_in_a_turn_shows_in_the_conversation_with_its_buttons(qt, config, server):
    _, state = server
    state.routes[("GET", "/v1/approvals")] = {"approvals": []}
    state.extra_events = [{"type": "approval", "approval": APPROVAL}]
    window = ChatWindow(lambda: config, ClaraApi)
    window.show()
    window.send("Please replace notes.md")
    wait_until(lambda: not window.busy)
    cards = [card for card in window.view.cards if isinstance(card, ApprovalCard)]
    assert len(cards) == 1 and cards[0].approval["id"] == 12
    window._add_approval(APPROVAL)  # the same request is not shown twice
    assert len([c for c in window.view.cards if isinstance(c, ApprovalCard)]) == 1
    window.quit_for_good()


def test_the_requests_still_waiting_come_back_with_the_conversation(qt, config, server):
    _, state = server
    state.add_conversation("app:tester:talk", ("hi", "hello"), title="Talk")
    state.routes[("GET", "/v1/approvals")] = {"approvals": [{**APPROVAL, "conversation": "app:tester:talk"}]}
    window = ChatWindow(lambda: config, ClaraApi)
    window.show()
    window.open_conversation("app:tester:talk")
    wait_until(lambda: any(isinstance(card, ApprovalCard) for card in window.view.cards))
    window.quit_for_good()


def test_the_api_methods_speak_the_servers_language(config, server):
    _, state = server
    for method, path, answer in (
        ("GET", "/v1/integrations", OVERVIEW), ("POST", "/v1/integrations/github", {"id": 1, "label": "erwan"}),
        ("POST", "/v1/integrations/google/start", {"url": "https://accounts.google.com/x"}),
        ("POST", "/v1/integrations/resources", REPO), ("PUT", "/v1/integrations/attachments", {}),
        ("PATCH", "/v1/integrations/resources/1", REPO), ("DELETE", "/v1/integrations/accounts/2", {"ok": True}),
        ("PUT", "/v1/admin/integrations", {}), ("PATCH", "/v1/integrations/accounts/1", {"id": 1}),
    ):
        state.routes[(method, path)] = answer
    api = ClaraApi(config)
    assert api.integrations()["accounts"][0]["label"] == "erwan"
    assert api.connect_github("ghp_x")["label"] == "erwan" and api.google_start()["url"].startswith("https://accounts.google.com")
    api.add_resource("computer_path", device="pc-1", alias="docs", label="Docs")
    assert state.requests[-1][2] == {"surface": "app", "user_id": "tester", "kind": "computer_path", "device": "pc-1", "alias": "docs", "label": "Docs"}
    api.attach(1, conversation="app:tester:c", levels={"write": "allow"})
    assert state.requests[-1][2] == {"surface": "app", "user_id": "tester", "resource": 1, "conversation": "app:tester:c", "levels": {"write": "allow"}}
    api.attach(1, project=4)
    assert state.requests[-1][2]["project"] == 4 and "levels" not in state.requests[-1][2]
    api.update_resource(1, levels={"read": "deny"})
    api.disconnect_account(2)
    api.set_account_levels(1, {"read": "ask"})
    api.admin_set_integrations(roots=["/srv"])
    assert state.requests[-1][2] == {"roots": ["/srv"]}


def test_a_project_has_its_own_connections(qt, config, server, monkeypatch):
    _, state = server
    attachments_routes(state)
    project = state.add_project("Clara")
    window = ChatWindow(lambda: config, ClaraApi)
    window.show()
    window.go("projects")
    page = window.shell.pages["projects"]
    opened = []
    monkeypatch.setattr(ConnectionsDialog, "exec", lambda self: opened.append(self.target) or QDialog.DialogCode.Rejected)
    page.show_connections()  # no project shown yet: nothing to attach to
    assert opened == []
    wait_until(lambda: page.list.count() > 0)
    window.open_project(project["id"])
    wait_until(lambda: page.project is not None)
    page.connections_button.click()
    assert opened == [{"project": project["id"]}]
    window.quit_for_good()

