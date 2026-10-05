"""QCM: the card for a form of Clara's, the answers it sends, and the form shown again with its answers."""

from __future__ import annotations

from conftest import wait_until
from PySide6.QtWidgets import QCheckBox, QFrame, QLabel, QPlainTextEdit, QRadioButton

from clara_app.api import ClaraApi
from clara_app.chat_view import CLARA, USER
from clara_app.chat_window import ChatWindow
from clara_app.qcm import QcmCard, display_answers, format_answers, grade

FORM = {
    "ref": "0a1b2c3d",
    "title": "Geo",
    "graded": True,
    "questions": [
        {"text": "Capital of France?", "type": "single", "options": ["Paris", "Lyon"], "correct": [0],
         "explanation": "", "answer": ""},
        {"text": "Primes?", "type": "multiple", "options": ["2", "4", "5"], "correct": [0, 2],
         "explanation": "4 = 2 x 2", "answer": ""},
        {"text": "Why?", "type": "text", "options": [], "correct": None, "explanation": "", "answer": "Because."},
    ],
}


def make_window(qt, config) -> ChatWindow:
    window = ChatWindow(lambda: config, ClaraApi)
    window.show()
    return window


def texts(window, role):
    return [text for r, text in window.view.texts() if r == role]


def test_the_answers_are_written_as_the_server_reads_them():
    message = format_answers(FORM, [[1], [0, 2], " Because "])
    assert message == (
        "[QCM answers 0a1b2c3d] Geo\n"
        "\n1. Capital of France?\nAnswer: B. Lyon"
        "\n\n2. Primes?\nAnswer: A. 2; C. 5"
        "\n\n3. Why?\nAnswer: Because"
    )
    assert format_answers(FORM, [[], [], ""]).endswith("Answer: (no answer)")
    assert display_answers(message).startswith("QCM answers: Geo\n\n1.")
    assert display_answers("hello") == "hello"
    assert display_answers("[QCM answers 0a1b2c3d]\n\n1. q") == "QCM answers\n\n1. q"


def test_grading_counts_the_choice_questions_that_name_their_answer():
    assert grade(FORM, [[0], [0, 2], ""]) == (2, 2)
    assert grade(FORM, [[1], [0], "x"]) == (0, 2)
    assert grade({"questions": [FORM["questions"][2]]}, [""]) == (0, 0)


def test_a_card_collects_the_answers_and_sends_them(qt):
    card = QcmCard(FORM)
    card.show()
    sent = []
    card.submitted.connect(sent.append)
    assert not card._send.isEnabled() and card._progress.text() == "0 of 3 answered"

    radios = card.findChildren(QRadioButton)
    boxes = card.findChildren(QCheckBox)
    assert len(radios) == 2 and len(boxes) == 3
    radios[1].setChecked(True)
    radios[0].setChecked(True)  # one only: this replaces the other
    boxes[0].setChecked(True)
    boxes[2].setChecked(True)
    boxes[1].setChecked(True)
    boxes[1].setChecked(False)
    card.findChild(QPlainTextEdit).setPlainText("Because")
    assert card._progress.text() == "3 of 3 answered" and card._send.isEnabled()

    card._send.click()
    assert sent == [format_answers(FORM, [[0], [0, 2], "Because"])]


def test_an_answered_card_shows_the_corrections_and_cannot_be_changed(qt):
    card = QcmCard(FORM, [[1], [0, 2], "Because"])
    card.show()
    assert card._send is None
    assert all(not b.isEnabled() for b in card.findChildren(QRadioButton) + card.findChildren(QCheckBox))
    rows = {}
    for row in card.findChildren(QFrame):
        if row.objectName() == "qcm-option":
            rows.setdefault(row.property("state"), []).append(row)
    assert {k: len(v) for k, v in rows.items()} == {"missed": 1, "wrong": 1, "right": 2, "": 1}
    labels = [label.text() for label in card.findChildren(QLabel)]
    assert "1 / 2 correct answers" in labels and "✗ Not quite" in labels and "✓ Correct" in labels
    assert "4 = 2 x 2" in labels and "Expected: Because." in labels


def test_a_form_without_correct_answers_is_not_scored(qt):
    form = {**FORM, "graded": False, "questions": [{**FORM["questions"][0], "correct": None}]}
    card = QcmCard(form, [[1]])
    labels = [label.text() for label in card.findChildren(QLabel)]
    assert "Answers sent to Clara." in labels


def test_a_form_the_model_asks_appears_and_its_answers_go_back(qt, config, server):
    _, state = server
    state.reply = ["Here is a quiz."]
    state.extra_events = [{"type": "qcm", "form": FORM}]
    window = make_window(qt, config)
    window.send("quiz me")
    wait_until(lambda: not window.busy)
    assert texts(window, CLARA) == ["Here is a quiz."]
    assert len(window.view.cards) == 1
    card = window.view.cards[0]

    card.findChildren(QRadioButton)[0].setChecked(True)
    card._send.click()
    assert window.busy
    wait_until(lambda: not window.busy)
    assert state.chat_bodies[1]["message"] == format_answers(FORM, [[0], [], ""])
    assert texts(window, USER)[-1].startswith("QCM answers: Geo")
    assert card.answers == [[0], [], ""] and card._send is None  # answered, with its corrections
    window.quit_for_good()


def test_answers_wait_while_clara_is_still_writing(qt, config, server):
    _, state = server
    state.reply = ["one ", "two"]
    state.hold.set()
    window = make_window(qt, config)
    card = QcmCard(FORM)
    window.view.add_card(card)
    window.send("hello")
    wait_until(lambda: window.busy)
    card.findChildren(QRadioButton)[0].setChecked(True)
    window._submit_qcm(card, format_answers(FORM, [[0], [], ""]))
    assert card.answers is None and "Wait for Clara" in card._progress.text()
    assert len(state.chat_bodies) <= 1
    window.cancel()
    window.quit_for_good()


def test_a_conversation_shows_its_form_again_with_the_answers_given(qt, config, server):
    _, state = server
    answered = {**FORM, "answers": [[1], [0, 2], "Because"]}
    open_form = {**FORM, "ref": "ffffffff", "title": "Later", "answers": None}
    state.add_conversation("app:tester:q", ("quiz me", ""), title="Quiz")
    state.messages["app:tester:q"] = [
        {"role": "user", "content": "quiz me"},
        {"role": "assistant", "content": "Here it is.", "qcm": [answered]},
        {"role": "user", "content": format_answers(FORM, [[1], [0, 2], "Because"])},
        {"role": "assistant", "content": "", "qcm": [open_form]},
    ]
    window = make_window(qt, config)
    window.open_conversation("app:tester:q")
    wait_until(lambda: window.conversation == "app:tester:q")
    first, second = window.view.cards
    assert first.answers == [[1], [0, 2], "Because"] and first._send is None
    assert second.answers is None and second._send is not None  # still to answer
    assert texts(window, CLARA) == ["Here it is."]  # the answer that only asked a form has no bubble
    assert texts(window, USER)[1].startswith("QCM answers: Geo")
    window.view.clear()
    assert window.view.cards == []
    window.quit_for_good()
