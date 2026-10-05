"""The list of conversations: shown at the side of the window, opened, titled, renamed, pinned, deleted."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from conftest import wait_until
from PySide6.QtWidgets import QInputDialog, QMessageBox

from clara_app.api import ClaraApi
from clara_app.chat_view import CLARA, ERROR, NOTE, USER
from clara_app.chat_window import ChatWindow
from clara_app.documents import Document, compose, preview, split_message
from clara_app.history import UNTITLED, HistoryPanel, display_title, group_of


def make_window(qt, config) -> ChatWindow:
    window = ChatWindow(lambda: config, ClaraApi)
    window.show()
    return window


def texts(window, role):
    return [text for r, text in window.view.texts() if r == role]


def titles(window) -> list[str]:
    return [text for conversation, text in window.history.items() if conversation]


def at(day: date, hour: int = 12) -> str:
    return datetime(day.year, day.month, day.day, hour).astimezone().astimezone(timezone.utc).isoformat()


# --- what the list says ------------------------------------------------------------------------


def test_a_conversation_is_called_by_its_title_else_its_first_words():
    assert display_title({"title": "Tea timer", "preview": "x"}) == "Tea timer"
    assert display_title({"title": "", "preview": "How do\n pointers work?"}) == "How do pointers work?"
    assert display_title({"title": "", "preview": ""}) == UNTITLED


def test_documents_are_taken_out_of_a_message_shown_again(tmp_path):
    code = Document(tmp_path / "a.py", "python", 'x = "</document>"\n</document>\nprint(x)', 0)
    pdf = Document(tmp_path / "b.pdf", "pdf", "[page 1]\nhello", 1)
    assert split_message(compose("look at *these*", [code, pdf])) == ("look at *these*", ["a.py", "b.pdf"])
    assert split_message(compose("", [pdf])) == ("", ["b.pdf"])
    assert split_message("no <document> here") == ("no <document> here", [])
    assert preview(compose("look", [code])[:40]) == "look"  # cut in the middle of the document
    assert preview(compose("", [code, pdf])[:30]) == "📎 a.py, b.pdf"
    assert preview("  plain\n text ") == "plain text"


def test_conversations_are_grouped_by_the_day_they_were_last_written_in():
    today = date(2026, 10, 3)
    assert group_of(at(today), today) == "Today"
    assert group_of(at(today - timedelta(days=1)), today) == "Yesterday"
    assert group_of(at(today - timedelta(days=3)), today) == "Previous 7 days"
    assert group_of(at(today - timedelta(days=20)), today) == "Previous 30 days"
    assert group_of(at(today - timedelta(days=90)), today) == "Older"


def test_the_panel_shows_pinned_ones_first_under_headings(qt):
    today = date(2026, 10, 3)
    panel = HistoryPanel()
    panel.show_conversations(
        [
            {"id": "p", "title": "Pinned one", "pinned": True, "updated_at": at(today - timedelta(days=40))},
            {"id": "a", "title": "Tea timer", "pinned": False, "updated_at": at(today)},
            {"id": "b", "title": "", "preview": "Explain malloc", "pinned": False, "updated_at": at(today)},
            {"id": "c", "title": "C pointers", "pinned": False, "updated_at": at(today - timedelta(days=1))},
        ],
        current="a",
        today=today,
    )
    assert panel.items() == [
        (None, "Pinned"), ("p", "Pinned one"),
        (None, "Today"), ("a", "Tea timer"), ("b", "Explain malloc"),
        (None, "Yesterday"), ("c", "C pointers"),
    ]
    assert panel.list.currentItem().text() == "Tea timer"
    assert [action.text() for action in panel.menu_for("p").actions() if action.text()] == [
        "Rename…", "Unpin", "Move to a project…", "Delete…"
    ]
    assert "Pin to the top" in [action.text() for action in panel.menu_for("a").actions()]
    panel.show_conversations([], current=None)
    assert panel.note.text() == "No conversation yet."


def test_the_conversations_of_a_project_are_grouped_under_its_name_newest_first(qt):
    today = date(2026, 10, 3)
    panel = HistoryPanel()
    panel.show_conversations(
        [
            {"id": "t3", "title": "Pinned in a project", "pinned": True, "updated_at": at(today - timedelta(days=30)), "project": 1},
            {"id": "a", "title": "Free chat", "pinned": False, "updated_at": at(today), "project": None},
            {"id": "t1", "title": "Old outline", "pinned": False, "updated_at": at(today - timedelta(days=9)), "project": 1},
            {"id": "t2", "title": "New outline", "pinned": False, "updated_at": at(today - timedelta(days=1)), "project": 1},
            {"id": "c1", "title": "Bot rewrite", "pinned": False, "updated_at": at(today), "project": 2},
        ],
        current=None,
        today=today,
        projects={1: "Thesis", 2: "Clara"},
    )
    assert panel.items() == [
        (None, "Pinned"), ("t3", "Pinned in a project"),
        (None, "Today"), ("a", "Free chat"),
        (None, "Clara"), ("c1", "Bot rewrite"),  # the project with the latest conversation first
        (None, "Thesis"), ("t2", "New outline"), ("t1", "Old outline"),
    ]
    opened = []
    panel.project_opened.connect(opened.append)
    heading = next(panel.list.item(row) for row in range(panel.list.count()) if panel.list.item(row).text() == "Thesis")
    panel._clicked(heading)
    assert opened == [1]


# --- the window ----------------------------------------------------------------------------------


def test_the_rail_lists_the_conversations_beside_the_page(qt, config, server):
    _, state = server
    state.add_conversation("app:tester:a", ("Tea timer", "Set."), title="Tea")
    window = make_window(qt, config)
    window.start_history()
    assert window.history.isVisibleTo(window.shell) and window.shell.new_chat.isVisibleTo(window.shell)
    wait_until(lambda: titles(window) == ["Tea"])
    window.quit_for_good()


def test_project_chats_are_listed_under_their_project_and_a_new_chat_starts_in_one(qt, config, server):
    _, state = server
    project = state.add_project("Thesis")
    state.add_conversation("app:tester:a", ("outline", "ok"), title="Outline")
    state.conversations["app:tester:a"]["project"] = project["id"]
    state.add_conversation("app:tester:b", ("tea", "ok"), title="Tea", updated_at="2026-01-01T10:00:00+00:00")
    window = make_window(qt, config)
    window.refresh_history()
    window.refresh_projects()
    wait_until(lambda: window.project_names == {project["id"]: "Thesis"} and len(titles(window)) == 2)
    assert window.history.items() == [(None, "Older"), ("app:tester:b", "Tea"), (None, "Thesis"), ("app:tester:a", "Outline")]
    window.chat_in_project(project["id"])  # from the project's page
    assert window.project == project["id"] and window.shell.current == "chat" and window.conversation is None
    window.send("a new thought")
    wait_until(lambda: not window.busy)
    assert state.chat_bodies[0]["project"] == project["id"]
    wait_until(lambda: len(titles(window)) == 3)
    window.new_chat()  # the next one is in none
    assert window.project is None
    window.quit_for_good()


def test_a_conversation_of_the_list_is_shown_again_and_goes_on(qt, config, server, tmp_path):
    _, state = server
    pdf = Document(tmp_path / "notes.pdf", "pdf", "[page 1]\nhello", 1)
    state.add_conversation(
        "app:tester:a", ("Tea *timer*", "Set **for** 5 min."), (compose("read this", [pdf]), "Read."), title="Tea"
    )
    window = make_window(qt, config)
    window.refresh_history()
    wait_until(lambda: titles(window) == ["Tea"])
    window.open_conversation("app:tester:a")
    wait_until(lambda: window.conversation == "app:tester:a")
    assert texts(window, USER) == [r"Tea \*timer\*", "read this  \n📎 notes\\.pdf"]
    assert texts(window, CLARA) == ["Set **for** 5 min.", "Read."]
    assert window.windowTitle() == "Clara — Tea"
    window.send("and now?")
    wait_until(lambda: not window.busy)
    assert state.chat_bodies[0]["conversation"] == "app:tester:a"
    assert state.title_requests == []  # it has a title already
    window.quit_for_good()


def test_a_conversation_of_the_web_site_is_shown_and_gone_on_with_in_the_app(qt, config, server):
    _, state = server
    state.add_conversation("web:tester:w", ("From the site", "Hello from the site."), title="Site chat")
    window = make_window(qt, config)
    window.sync()
    wait_until(lambda: titles(window) == ["Site chat"])
    window.open_conversation("web:tester:w")
    wait_until(lambda: window.conversation == "web:tester:w")
    assert texts(window, CLARA) == ["Hello from the site."]
    window.send("and now?")
    wait_until(lambda: not window.busy)
    assert state.chat_bodies[0]["conversation"] == "web:tester:w"
    window.quit_for_good()


def test_what_the_web_site_changed_is_read_again_but_not_what_the_app_wrote(qt, config, server):
    _, state = server
    state.add_conversation("app:tester:a", ("q", "first answer"), title="Tea", updated_at="2026-10-01T10:00:00+00:00")
    window = make_window(qt, config)
    window.open_conversation("app:tester:a")
    wait_until(lambda: window.conversation == "app:tester:a")
    assert texts(window, CLARA) == ["first answer"]
    # gone on elsewhere
    state.messages["app:tester:a"] += [{"role": "user", "content": "more"}, {"role": "assistant", "content": "second answer"}]
    state.conversations["app:tester:a"]["updated_at"] = "2026-10-01T11:00:00+00:00"
    window.sync()
    wait_until(lambda: texts(window, CLARA) == ["first answer", "second answer"])
    # our own answer is what we show: the list's new date is not "changed elsewhere"
    window.send("and now?")
    wait_until(lambda: not window.busy)
    wait_until(lambda: window._stamp == state.conversations["app:tester:a"]["updated_at"])
    shown = texts(window, CLARA)
    window.sync()
    wait_until(lambda: not window._listing)
    assert texts(window, CLARA) == shown and window._opening is None
    window.quit_for_good()


def test_a_conversation_is_not_read_again_while_clara_writes(qt, config, server):
    _, state = server
    state.add_conversation("app:tester:a", ("q", "a"), title="Tea", updated_at="2026-10-01T10:00:00+00:00")
    window = make_window(qt, config)
    window.open_conversation("app:tester:a")
    wait_until(lambda: window.conversation == "app:tester:a")
    state.hold.set()
    window.send("hello")
    state.conversations["app:tester:a"]["updated_at"] = "2026-10-01T11:00:00+00:00"
    window.sync()  # busy: nothing is fetched
    assert window.busy and window._opening is None and texts(window, USER)[-1] == "hello"
    window.cancel()
    window.quit_for_good()


def test_a_reminder_that_came_due_in_a_conversation_is_shown_as_one(qt, config, server):
    _, state = server
    state.add_conversation("app:tester:a", ("remind me of tea", "Sure."), ("[Reminder due] Tea", "Your tea!"))
    window = make_window(qt, config)
    window.open_conversation("app:tester:a")
    wait_until(lambda: window.conversation == "app:tester:a")
    assert texts(window, USER) == ["remind me of tea"] and texts(window, CLARA) == ["Sure.", "Your tea!"]
    assert "⏰ **Tea**" in texts(window, NOTE)[0]
    window.quit_for_good()


def test_the_summary_of_messages_no_longer_kept_is_shown(qt, config, server):
    _, state = server
    state.add_conversation("app:tester:a", ("new question", "new answer"), title="Tea")
    state.summaries["app:tester:a"] = "They talked about oolong."
    window = make_window(qt, config)
    window.open_conversation("app:tester:a")
    wait_until(lambda: window.conversation == "app:tester:a")
    assert "oolong" in texts(window, NOTE)[0] and texts(window, USER) == ["new question"]
    window.quit_for_good()


def test_a_new_conversation_joins_the_list_and_clara_titles_it(qt, config, server):
    _, state = server
    window = make_window(qt, config)
    window.send("How do pointers work?")
    wait_until(lambda: titles(window) == ["Clara's title"])
    assert state.title_requests == [window.conversation]
    assert window.windowTitle() == "Clara — Clara's title"
    window.quit_for_good()


def test_without_a_title_it_is_listed_by_its_first_words_and_asked_again(qt, config, server):
    _, state = server
    state.title = None  # the model fails
    window = make_window(qt, config)
    window.send("How do pointers work?")
    wait_until(lambda: titles(window) == ["How do pointers work?"] and len(state.title_requests) == 1)
    wait_until(lambda: not window._titling)
    state.title = "Pointers"
    window.send("And arrays?")
    wait_until(lambda: titles(window) == ["Pointers"])
    assert len(state.title_requests) == 2
    window.quit_for_good()


def test_the_list_cannot_be_used_while_clara_writes(qt, config, server):
    _, state = server
    state.add_conversation("app:tester:a", ("q", "a"), title="Other")
    state.hold.set()
    window = make_window(qt, config)
    window.send("hello")
    assert not window.history.list.isEnabled() and not window.shell.new_chat.isEnabled()
    window.open_conversation("app:tester:a")
    window.new_chat()
    assert window.busy and window.conversation.startswith("app:tester:") and texts(window, USER) == ["hello"]
    window.cancel()
    assert window.history.list.isEnabled() and window.shell.new_chat.isEnabled()
    window.quit_for_good()


def test_a_message_waits_for_the_conversation_being_opened(qt, config, server):
    _, state = server
    state.add_conversation("app:tester:a", ("q", "a"), title="Other")
    state.hold_messages.set()
    window = make_window(qt, config)
    window.open_conversation("app:tester:a")
    window.send("too early")
    assert state.chat_bodies == [] and "Still opening" in texts(window, NOTE)[-1]
    window.new_chat()  # changed their mind: the conversation, when it comes, is not shown
    state.hold_messages.clear()
    wait_until(lambda: not window._calls)
    assert window.conversation is None and texts(window, NOTE) == []
    window.quit_for_good()


def test_rename_pin_and_delete(qt, config, server, monkeypatch):
    _, state = server
    state.add_conversation("app:tester:a", ("Tea timer", "Set."), title="Tea", updated_at="2026-10-02T10:00:00+00:00")
    state.add_conversation("app:tester:b", ("C pointers", "Ok."), title="C", updated_at="2026-10-01T10:00:00+00:00")
    window = make_window(qt, config)
    window.open_conversation("app:tester:b")
    window.refresh_history()
    wait_until(lambda: window.conversation == "app:tester:b" and titles(window) == ["Tea", "C"])

    monkeypatch.setattr(QInputDialog, "getText", staticmethod(lambda *args, **kwargs: ("  Pointers in C ", True)))
    window.rename_conversation("app:tester:b")
    wait_until(lambda: titles(window) == ["Tea", "Pointers in C"])

    window.pin_conversation("app:tester:b", True)
    wait_until(lambda: titles(window) == ["Pointers in C", "Tea"])
    assert window.history.items()[0] == (None, "Pinned")

    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *args, **kwargs: QMessageBox.StandardButton.No))
    window.delete_conversation("app:tester:b")
    assert state.deleted == []
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *args, **kwargs: QMessageBox.StandardButton.Yes))
    window.delete_conversation("app:tester:b")
    wait_until(lambda: titles(window) == ["Tea"])
    assert window.conversation is None and texts(window, NOTE) == []  # it was the one shown
    window.quit_for_good()


def test_search_asks_the_server(qt, config, server):
    _, state = server
    state.add_conversation("app:tester:a", ("Tea timer", "Set."), title="Tea")
    state.add_conversation("app:tester:b", ("C pointers", "Ok."), title="C")
    window = make_window(qt, config)
    window.refresh_history()
    wait_until(lambda: len(titles(window)) == 2)
    window.history.search.setText("pointer")
    wait_until(lambda: titles(window) == ["C"])
    assert state.list_requests[-1]["q"] == "pointer"
    window.history.search.setText("nothing like it")
    wait_until(lambda: window.history.note.text() == "Nothing found.")
    window.quit_for_good()


def test_a_conversation_that_cannot_be_opened_is_explained(qt, config, server):
    window = make_window(qt, config)
    window.open_conversation("app:tester:gone")
    wait_until(lambda: texts(window, ERROR))
    assert "No such conversation" in texts(window, ERROR)[0] and window.conversation is None
    window.quit_for_good()
