"""The chat window talking to a (fake) Clara server."""

from __future__ import annotations

from conftest import wait_until
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest

from clara_app.api import ClaraApi
from clara_app.chat_view import CLARA, ERROR, NOTE, USER
from clara_app.chat_window import ChatWindow, greeting, literal
from clara_app.config import Config


def make_window(qt, config) -> ChatWindow:
    window = ChatWindow(lambda: config, ClaraApi)
    window.show()
    return window


def texts(window, role):
    return [text for r, text in window.view.texts() if r == role]


def test_literal_shows_what_was_typed():
    assert literal("2*3 = [x] _y_") == r"2\*3 = \[x\] \_y\_"
    assert literal("a\nb") == "a  \nb"


def test_a_question_gets_a_streamed_markdown_answer(qt, config, server):
    _, state = server
    window = make_window(qt, config)
    window.input.setPlainText("hi *there*")

    QTest.keyClick(window.input, Qt.Key.Key_Return)

    assert window.busy and window.send_button.toolTip() == "Stop the answer"
    wait_until(lambda: not window.busy)
    assert window.send_button.toolTip() == "Send" and window.input.toPlainText() == ""
    assert texts(window, USER) == [literal("hi *there*")]
    assert texts(window, CLARA) == ["Hello **world**"]  # the label renders it as bold
    assert state.chat_bodies[0]["message"] == "hi *there*"
    window.quit_for_good()


def test_shift_enter_makes_a_new_line_and_does_not_send(qt, config):
    window = make_window(qt, config)
    window.input.setFocus()
    QTest.keyClicks(window.input, "one")
    QTest.keyClick(window.input, Qt.Key.Key_Return, Qt.KeyboardModifier.ShiftModifier)
    QTest.keyClicks(window.input, "two")
    assert window.input.toPlainText() == "one\ntwo" and not window.busy
    window.quit_for_good()


def test_an_empty_message_is_not_sent(qt, config, server):
    _, state = server
    window = make_window(qt, config)
    window.input.setPlainText("   \n ")
    window.send()
    assert not window.busy and state.chat_bodies == []
    window.quit_for_good()


def test_a_server_error_is_shown_and_the_empty_reply_removed(qt, config, server):
    _, state = server
    state.chat_error = "The language model failed"
    window = make_window(qt, config)
    window.send("hello")
    wait_until(lambda: not window.busy)
    assert texts(window, ERROR) == ["The language model failed"]
    assert texts(window, CLARA) == []
    window.quit_for_good()


def test_an_unreachable_server_is_explained(qt, config):
    config.url = "http://127.0.0.1:1"
    window = make_window(qt, config)
    window.send("hello")
    wait_until(lambda: not window.busy, timeout=15)
    assert "Cannot reach the Clara server" in texts(window, ERROR)[0]
    window.quit_for_good()


def test_stop_keeps_what_has_arrived(qt, config, server):
    _, state = server
    state.hold.set()  # the answer stops after "Hello " and waits
    window = make_window(qt, config)
    window.send("hello")
    wait_until(lambda: texts(window, CLARA) == ["Hello "] or window._reply_text == "Hello ")
    window.send_button.click()  # Stop
    assert not window.busy and window.send_button.toolTip() == "Send"
    assert texts(window, CLARA) == ["Hello "]
    assert texts(window, ERROR) == []
    window.quit_for_good()


def test_stopping_before_the_first_word_leaves_no_empty_bubble(qt, config, server):
    _, state = server
    state.reply = []
    state.hold.set()
    window = make_window(qt, config)
    window.send("hello")
    window.cancel()
    assert texts(window, CLARA) == [] and not window.busy
    window.quit_for_good()


def test_without_settings_the_window_asks_for_them(qt):
    window = make_window(qt, Config(url="http://x", token="", user_id="u"))
    asked = []
    window.settings_requested.connect(lambda: asked.append(True))
    window.send("hello")
    assert asked == [True] and not window.busy and texts(window, USER) == []
    window.quit_for_good()


def test_new_chat_clears_the_view_and_keeps_the_conversation_on_the_server(qt, config, server):
    _, state = server
    window = make_window(qt, config)
    window.send("hello")
    wait_until(lambda: not window.busy)
    first = window.conversation
    assert first.startswith("app:tester:") and state.chat_bodies[0]["conversation"] == first
    window.new_chat()
    assert window.conversation is None and state.deleted == []
    assert texts(window, USER) == [] and texts(window, CLARA) == [] and texts(window, NOTE) == []
    assert not window.view.welcome.isHidden()  # the greeting shows while nothing is written
    window.send("another")
    wait_until(lambda: not window.busy)
    assert window.conversation not in (None, first)
    assert state.chat_bodies[1]["conversation"] == window.conversation
    window.quit_for_good()


def test_closing_the_window_hides_it_instead_of_quitting(qt, config):
    window = make_window(qt, config)
    window.close()
    assert not window.isVisible()
    window.quit_for_good()
    window.show()
    window.close()
    assert not window.isVisible()


def test_toggle_shows_then_hides(qt, config):
    window = make_window(qt, config)
    window.hide()
    window.toggle()
    assert window.isVisible()
    window.hide()
    assert not window.isVisible()
    window.quit_for_good()


def test_a_reminder_is_noted_in_the_conversation(qt, config):
    window = make_window(qt, config)
    window.add_reminder("Call *mum*", "Set by Alice.")
    note = texts(window, NOTE)[-1]
    assert "⏰" in note and r"Call \*mum\*" in note and "Set by Alice" in note
    window.quit_for_good()


def test_the_status_line_follows_the_connection(qt, config):
    window = make_window(qt, config)
    status = window.shell._me_status
    window.set_state("running")
    assert status.text() == "Clara is running"
    window.set_state("stopping")
    assert status.text() == "Clara is stopping"
    window.set_state("down")
    assert status.text() == "Clara is not running"
    window.set_state(None)
    assert status.text() == ""
    window.quit_for_good()


def test_the_greeting_names_the_part_of_the_day():
    from datetime import datetime

    assert greeting(datetime(2026, 10, 5, 3)) == "Hello" and greeting(datetime(2026, 10, 5, 9)) == "Good morning"
    assert greeting(datetime(2026, 10, 5, 15)) == "Good afternoon" and greeting(datetime(2026, 10, 5, 21)) == "Good evening"


def test_the_pages_are_opened_from_the_rail_and_the_settings_bar(qt, config):
    window = make_window(qt, config)
    assert window.shell.current == "chat" and window.shell.nav_buttons["chat"].isChecked()
    window.shell.nav_buttons["projects"].click()
    assert window.shell.current == "projects" and window.shell.title.text() == "Projects"
    window.shell.me.click()  # you: the settings
    assert window.shell.current == "account" and window.shell.tabs["account"].isChecked() and window.shell.me.isChecked()
    assert not window.shell.settings_bar.isHidden() and window.shell.tabs["discord"].isHidden()  # not an administrator
    window.shell.tabs["memory"].click()
    assert window.shell.current == "memory"
    window.go("admin")  # a page for administrators only
    assert window.shell.current == "account"
    window.shell.set_admin(True)
    window.go("admin")
    assert window.shell.current == "admin" and not window.shell.tabs["admin"].isHidden()
    window.go("chat")
    assert window.shell.settings_bar.isHidden() and window.shell.nav_buttons["chat"].isChecked()
    window.quit_for_good()


def test_the_rail_becomes_a_drawer_when_the_window_is_narrow(qt, config):
    window = make_window(qt, config)
    window.resize(1100, 700)
    qt.processEvents()
    assert window.shell.rail.isVisibleTo(window.shell) and not window.shell.menu_button.isVisibleTo(window.shell)
    window.resize(560, 700)
    qt.processEvents()
    assert window.shell.narrow and not window.shell.rail.isVisible() and window.shell.menu_button.isVisibleTo(window.shell)
    window.shell.menu_button.click()
    wait_until(lambda: window.shell.rail.isVisible() and window.shell.rail.x() == 0)
    window.shell.set_rail_open(False)
    wait_until(lambda: not window.shell.rail.isVisible())
    window.quit_for_good()


def test_a_tool_that_ran_leaves_a_note_under_the_reply(qt, config, server):
    _, state = server
    state.extra_events = [{"type": "tool", "name": "remind", "arguments": {"text": "Call mum", "when": "tomorrow"}, "result": "ok"},
                          {"type": "tool", "name": "adjust_relation", "arguments": {"delta": 3}, "result": "ok"}]
    window = make_window(qt, config)
    window.send("remind me")
    wait_until(lambda: not window.busy)
    assert window.view.bubbles[-1].notes == [("Set a reminder", "Call mum")]  # the relationship is not shown
    window.quit_for_good()


def test_a_long_input_grows_then_scrolls(qt, config):
    window = make_window(qt, config)
    one = window.input.height()
    window.input.setPlainText("\n".join(["line"] * 3))
    three = window.input.height()
    window.input.setPlainText("\n".join(["line"] * 30))
    assert one < three < window.input.height() <= 6 * window.input.fontMetrics().lineSpacing() + 60
    window.quit_for_good()
