"""Projects in the app: the API, sending folders, the project of the chats, and the projects dialog."""

from __future__ import annotations

from conftest import wait_until
from PySide6.QtWidgets import QInputDialog, QMessageBox

from clara_app.api import ClaraApi
from clara_app.chat_view import NOTE
from clara_app.chat_window import ChatWindow
from clara_app.projects import BATCH_FILES, Entry, batches, file_entries, folder_entries, left_out, send
from clara_app.projects_dialog import ProjectsDialog

# --- the API ----------------------------------------------------------------------------------------------


def test_projects_through_the_api(config, server):
    _, state = server
    api = ClaraApi(config)
    made = api.create_project("Thesis", "My PhD", "Answer in French")
    assert (made["name"], made["instructions"]) == ("Thesis", "Answer in French")
    assert [p["name"] for p in api.projects()] == ["Thesis"]
    result = api.upload_files(made["id"], [("notes.md", "Chapter 1".encode()), ("empty.txt", b"  ")])
    assert result["added"] == ["notes.md"] and result["skipped"][0]["reason"] == "empty"
    assert state.project_files[made["id"]] == {"notes.md": "Chapter 1"}
    assert api.file(made["id"], "notes.md")["content"] == "Chapter 1"
    assert api.update_project(made["id"], name="Thesis 2")["name"] == "Thesis 2"
    assert api.add_repository(made["id"], "octo/hello")["project"]["sources"][0]["repo"] == "octo/hello"
    assert api.remove_file(made["id"], "notes.md")["project"]["files"] == 1
    api.delete_project(made["id"])
    assert api.projects() == []


def test_chats_and_lists_name_the_project_only_when_asked(config, server):
    _, state = server
    api = ClaraApi(config)
    list(api.chat("hi", "app:tester:1"))
    list(api.chat("hi", "app:tester:2", project=4))
    assert "project" not in state.chat_bodies[0] and state.chat_bodies[1]["project"] == 4
    api.conversations("", "none")
    api.conversations("tea", 4)
    assert state.list_requests[-2]["project"] == "none" and state.list_requests[-1]["project"] == "4"
    api.update_conversation("app:tester:1", project=4)
    api.update_conversation("app:tester:1", project=None)
    api.update_conversation("app:tester:1", pinned=True)
    assert [body.get("project", "kept") for _, body in state.patches] == [4, None, "kept"]


# --- sending files ---------------------------------------------------------------------------------------


def test_a_folder_is_sent_without_its_dependencies_and_binaries(tmp_path):
    root = tmp_path / "site"
    (root / "src").mkdir(parents=True)
    (root / "node_modules" / "x").mkdir(parents=True)
    (root / ".git").mkdir()
    (root / "src" / "app.js").write_text("let a;")
    (root / "README.md").write_text("# Site")
    (root / "logo.png").write_bytes(b"\x89PNG")
    (root / "package-lock.json").write_text("{}")
    (root / "node_modules" / "x" / "index.js").write_text("junk")
    (root / ".git" / "HEAD").write_text("ref")
    entries, skipped = folder_entries(root)
    assert [e.path for e in entries] == ["site/README.md", "site/src/app.js"]
    assert {s["path"]: s["reason"] for s in skipped} == {
        "site/logo.png": "not a text file", "site/package-lock.json": "generated file",
    }  # node_modules and .git are not even walked
    assert left_out("a/.venv/x.py") == "in .venv/" and left_out("a/b.py") == ""
    files, skipped = file_entries([str(root / "README.md"), str(root / "missing.txt")])
    assert [e.path for e in files] == ["README.md"] and skipped[0]["path"] == "missing.txt"


def test_files_are_sent_in_batches(tmp_path, config, server):
    _, state = server
    entries = []
    for number in range(BATCH_FILES + 5):
        path = tmp_path / f"f{number}.txt"
        path.write_text(f"file {number}")
        entries.append(Entry(f"f{number}.txt", path, path.stat().st_size))
    assert [len(group) for group in batches(entries)] == [BATCH_FILES, 5]
    big = [Entry("a", tmp_path, 4_000_000), Entry("b", tmp_path, 4_000_000)]
    assert [len(group) for group in batches(big)] == [1, 1]
    project = state.add_project("P")
    seen = []
    result = send(ClaraApi(config), project["id"], entries, lambda done, total: seen.append((done, total)))
    assert result.added == BATCH_FILES + 5 and not result.error
    assert seen == [(BATCH_FILES, BATCH_FILES + 5), (BATCH_FILES + 5, BATCH_FILES + 5)]
    assert result.project["files"] == BATCH_FILES + 5 and len(state.uploads) == 2


# --- the window ------------------------------------------------------------------------------------------


def make_window(qt, config) -> ChatWindow:
    window = ChatWindow(lambda: config, ClaraApi)
    window.show()
    return window


def test_the_chats_go_in_the_project_chosen(qt, config, server):
    _, state = server
    thesis = state.add_project("Thesis")
    state.add_conversation("app:tester:old", ("Outside", "ok"))
    window = make_window(qt, config)
    window.start_history()
    wait_until(lambda: window.project_box.count() == 2)
    assert window.project_box.itemText(1) == "Thesis"
    wait_until(lambda: state.list_requests and state.list_requests[-1].get("project") == "none")
    window.project_box.setCurrentIndex(1)
    assert window.project == thesis["id"]
    wait_until(lambda: state.list_requests[-1].get("project") == str(thesis["id"]))
    assert "Thesis" in [text for role, text in window.view.texts() if role == NOTE][-1]
    window.send("What is chapter 1 about?")
    wait_until(lambda: not window.busy and state.chat_bodies)
    assert state.chat_bodies[-1]["project"] == thesis["id"]
    assert state.conversations[window.conversation]["project"] == thesis["id"]
    window.quit_for_good()
    window.close()


def test_a_conversation_moves_to_a_project_from_the_list(qt, config, server, monkeypatch):
    _, state = server
    state.add_project("Thesis")
    state.add_conversation("app:tester:a", ("Tea timer", "Done."), updated_at="2026-10-01T10:00:00+00:00")
    state.add_conversation("app:tester:b", ("C pointers", "Addresses."), updated_at="2026-10-02T10:00:00+00:00")
    window = make_window(qt, config)
    window.start_history()
    wait_until(lambda: window.project_box.count() == 2 and window.conversation == "app:tester:b")  # the latest
    monkeypatch.setattr(QInputDialog, "getItem", lambda *args, **kwargs: ("Thesis", True))
    window.move_conversation("app:tester:a")  # not the one shown: it leaves the list of chats in no project
    wait_until(lambda: state.patches and window.history.info("app:tester:a") is None)
    assert state.patches[-1][1]["project"] == 1 and window.project is None
    window.move_conversation("app:tester:b")  # the one shown: the window follows it into the project
    wait_until(lambda: window.project == 1 and window.history.info("app:tester:b") is not None)
    assert window.conversation == "app:tester:b" and window.project_box.currentText() == "Thesis"
    window.quit_for_good()
    window.close()


# --- the dialog --------------------------------------------------------------------------------------------


def make_dialog(qt, config, select=None) -> ProjectsDialog:
    dialog = ProjectsDialog(lambda: config, ClaraApi, select=select)
    dialog.show()
    return dialog


def test_the_dialog_makes_and_fills_a_project(qt, config, server, monkeypatch, tmp_path):
    _, state = server
    dialog = make_dialog(qt, config)
    wait_until(lambda: "No project yet" in dialog.status.text())
    assert not dialog.detail.isEnabled()
    monkeypatch.setattr(QInputDialog, "getText", lambda *args, **kwargs: ("Course", True))
    dialog.create()
    wait_until(lambda: dialog.project is not None and dialog.items() == ["Course"])
    dialog.instructions.setPlainText("Explain like a teacher")
    assert dialog.save_button.isEnabled()
    dialog.save()
    wait_until(lambda: state.projects[1]["instructions"] == "Explain like a teacher")
    wait_until(lambda: not dialog.save_button.isEnabled())
    notes = tmp_path / "notes.md"
    notes.write_text("Lesson 1")
    dialog.upload(lambda: file_entries([str(notes)]))
    wait_until(lambda: dialog._upload is None and dialog.project["files"] == 1)
    assert dialog.status.text() == "1 file added."
    assert [dialog.files.item(row).data(256) for row in range(dialog.files.count())] == ["notes.md"]
    dialog.download("octo/hello")
    wait_until(lambda: dialog.sources.count() == 1 and not dialog.busy)
    assert "octo/hello" in dialog.sources.item(0).text()
    assert "added from octo/hello" in dialog.status.text()
    dialog.files.setCurrentRow(0)
    dialog.remove_file()
    wait_until(lambda: dialog.project["files"] == 1 and "removed" in dialog.status.text())
    monkeypatch.setattr(QMessageBox, "question", lambda *args, **kwargs: QMessageBox.StandardButton.Yes)
    dialog.delete()
    wait_until(lambda: dialog.items() == [] and dialog.project is None)
    assert state.projects == {}
    dialog.done(0)


def test_the_dialog_opens_on_the_project_asked_for_and_starts_a_chat_in_it(qt, config, server):
    _, state = server
    state.add_project("A")
    b = state.add_project("B", **{"x.py": "print(1)"})
    dialog = make_dialog(qt, config, select=b["id"])
    wait_until(lambda: dialog.project is not None)
    assert dialog.project["name"] == "B" and dialog.files.count() == 1
    asked = []
    dialog.chat_requested.connect(asked.append)
    dialog.chat_button.click()
    assert asked == [b["id"]]
    dialog.done(0)
