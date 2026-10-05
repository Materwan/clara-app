"""QCM: a form Clara asks through her `qcm` tool, shown as a card in the conversation.

A question is `single` (radio buttons), `multiple` (check boxes) or `text` (a box to type in). The answers go back
to Clara as the user's next message, written exactly as the server's `clara/qcm.py` writes them (`format_answers`),
so that the server can show the form answered when the conversation is opened again: keep the two in step.
"""

from __future__ import annotations

import re

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractButton,
    QButtonGroup,
    QCheckBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QRadioButton,
    QVBoxLayout,
    QWidget,
)

from . import mathview

LETTERS = "ABCDEFGHIJ"
NO_ANSWER = "(no answer)"
_MARK = re.compile(r"^\[QCM answers [0-9a-f]{8}\] ?([^\n]*)")
TEXT_HEIGHT = 64


def empty_answer(question: dict):
    return "" if question["type"] == "text" else []


def format_answers(form: dict, answers: list) -> str:
    """The message the user sends for a form: a choice question's answer is a list of option numbers, a
    text question's a string."""
    title = f" {form['title']}" if form.get("title") else ""
    lines = [f"[QCM answers {form['ref']}]{title}"]
    for number, (question, answer) in enumerate(zip(form["questions"], answers), 1):
        if question["type"] == "text":
            given = str(answer or "").strip()
        else:
            given = "; ".join(f"{LETTERS[i]}. {question['options'][i]}" for i in sorted(answer or []))
        lines.append(f"\n{number}. {question['text']}\nAnswer: {given or NO_ANSWER}")
    return "\n".join(lines)


def display_answers(message: str) -> str:
    """How the answers message is shown in the conversation: without the reference the server recognises it by."""
    return _MARK.sub(lambda found: f"QCM answers: {found.group(1)}" if found.group(1) else "QCM answers", message, count=1)


def grade(form: dict, answers: list) -> tuple[int, int]:
    """`(right, asked)` over the choice questions that name their correct options."""
    right = asked = 0
    for question, answer in zip(form["questions"], answers):
        if question["type"] == "text" or question.get("correct") is None:
            continue
        asked += 1
        right += sorted(answer or []) == question["correct"]
    return right, asked


STYLE = """
QLabel { color: palette(text); }
QFrame#qcm { background: palette(base); border: 1px solid palette(mid); border-radius: 12px; }
QLabel#qcm-title { font-weight: 700; font-size: 14px; }
QLabel#qcm-question { font-weight: 600; }
QLabel#qcm-letter { color: palette(placeholder-text); font-weight: 700; }
QLabel#qcm-hint, QLabel#qcm-progress, QLabel#qcm-score { color: palette(placeholder-text); }
QLabel#qcm-score { color: palette(text); font-weight: 600; }
QLabel#qcm-explain { background: palette(alternate-base); border-radius: 6px; padding: 5px 8px; }
QFrame#qcm-explain { background: palette(alternate-base); border-radius: 6px; }
QFrame#qcm-option { border: 1px solid palette(midlight); border-radius: 8px; }
QFrame#qcm-option[picked="true"] { border-color: #3d5afe; }
QFrame#qcm-option[state="right"] { background: #e3f1e4; border-color: #5fa564; }
QFrame#qcm-option[state="wrong"] { background: #fdecea; border-color: #e0877d; }
QFrame#qcm-option[state="missed"] { border: 1px dashed #5fa564; }
QFrame#qcm-option[state="right"] QLabel, QFrame#qcm-option[state="wrong"] QLabel { color: #1f2a20; }
QLabel#qcm-verdict[ok="true"] { color: #2f7a39; font-weight: 600; }
QLabel#qcm-verdict[ok="false"] { color: #b2382b; font-weight: 600; }
"""


class _OptionRow(QFrame):
    """An option: its button and its text (which wraps, as a radio button's own text does not). A click anywhere on
    the row selects it, whether the text is a label or a web view with formulas."""

    def __init__(self, button: QAbstractButton):
        super().__init__()
        self.button = button
        self.setObjectName("qcm-option")
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def mousePressEvent(self, event) -> None:
        if self.button.isEnabled():
            self.button.click()
        super().mousePressEvent(event)


class QcmCard(QFrame):
    """One form. `answers` (one entry per question) shows it answered, with the corrections when it is graded;
    without, the user fills it in and sends it: `submitted(text)` carries the message to send. The window then
    calls `lock()` if it took it, or `warn()` if it could not (Clara is still writing)."""

    submitted = Signal(str)

    def __init__(self, form: dict, answers: list | None = None, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("qcm")
        self.setStyleSheet(STYLE)
        self.form = form
        self.answers = answers
        self._draft: list = [empty_answer(q) for q in form["questions"]]
        self._buttons: list[list[QAbstractButton]] = []
        self._send: QPushButton | None = None
        self._progress: QLabel | None = None
        self._column = QVBoxLayout(self)
        self._column.setContentsMargins(12, 10, 12, 10)
        self._column.setSpacing(8)
        self._build()

    # -- building ----------------------------------------------------------------------- #

    def _build(self) -> None:
        total = len(self.form["questions"])
        title = self.form.get("title") or ("A question" if total == 1 else f"{total} questions")
        self._column.addWidget(self._rich(f"QCM · {title}", "qcm-title", extra="font-weight: 700; font-size: 14px"))
        shown = self.answers if self.answers is not None else self._draft
        for index, question in enumerate(self.form["questions"]):
            self._column.addWidget(self._question(index, question, shown[index]))
        self._column.addLayout(self._footer())

    def _label(self, text: str, name: str) -> QLabel:
        label = QLabel(text)
        label.setObjectName(name)
        label.setWordWrap(True)
        label.setTextFormat(Qt.TextFormat.PlainText)
        label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        return label

    def _rich(self, text: str, name: str, color: str | None = None, extra: str = "", markdown: str | None = None) -> QWidget:
        """Text that may hold LaTeX formulas ($x^2$): a web view typesets it; without one, an ordinary label.
        `markdown`: the text as the web view reads it (Markdown), when that needs escaping that a label must not have."""
        if mathview.available() and mathview.has_math(text):
            view = mathview.MathView(markdown or text, color=color, extra=extra)
            view.setObjectName(name)
            return view
        return self._label(text, name)

    def _explanation(self, text: str, lead: str = "") -> QWidget:
        """A note under a question (the explanation, the expected answer), on a tinted background."""
        if mathview.available() and mathview.has_math(text):
            box = QFrame()
            box.setObjectName("qcm-explain")
            inside = QVBoxLayout(box)
            inside.setContentsMargins(8, 5, 8, 5)
            inside.addWidget(mathview.MathView(f"**{lead.strip()}** {text}" if lead else text))
            return box
        return self._label(lead + text, "qcm-explain")

    def _question(self, index: int, question: dict, given) -> QWidget:
        done = self.answers is not None
        box = QWidget()
        layout = QVBoxLayout(box)
        layout.setContentsMargins(0, 2, 0, 2)
        layout.setSpacing(4)
        number = f"{index + 1}. "
        layout.addWidget(self._rich(
            number + question["text"], "qcm-question", extra="font-weight: 600",
            markdown=f"{index + 1}\\. " + question["text"],  # "1. " would be a list for Markdown
        ))
        correct = question.get("correct")
        known = done and correct is not None and question["type"] != "text"
        if question["type"] == "multiple":
            layout.addWidget(self._label("Select all that apply.", "qcm-hint"))
        if question["type"] == "text":
            layout.addWidget(self._text_box(index, given, done))
        else:
            group = QButtonGroup(box)
            group.setExclusive(question["type"] == "single")
            buttons = []
            for at, option in enumerate(question["options"]):
                row, button = self._option(index, question, at, given, done)
                group.addButton(button)
                buttons.append(button)
                layout.addWidget(row)
            self._buttons.append(buttons)
        if known:
            right = sorted(given) == correct
            verdict = self._label("✓ Correct" if right else "✗ Not quite", "qcm-verdict")
            verdict.setProperty("ok", "true" if right else "false")
            layout.addWidget(verdict)
        if done and question.get("explanation"):
            layout.addWidget(self._explanation(question["explanation"]))
        if done and question["type"] == "text" and question.get("answer"):
            layout.addWidget(self._explanation(question["answer"], "Expected: "))
        return box

    def _option(self, index: int, question: dict, at: int, given: list, done: bool) -> tuple[QFrame, QAbstractButton]:
        button: QAbstractButton = QRadioButton() if question["type"] == "single" else QCheckBox()
        picked = at in given
        button.setChecked(picked)
        button.setEnabled(not done)
        button.toggled.connect(lambda on, i=index, o=at: self._toggled(i, o, on))
        row = _OptionRow(button)
        row.setProperty("picked", "true" if picked else "false")
        correct = question.get("correct")
        if done and correct is not None:
            right = at in correct
            row.setProperty("state", ("right" if picked else "missed") if right else ("wrong" if picked else ""))
        layout = QHBoxLayout(row)
        layout.setContentsMargins(8, 5, 8, 5)
        layout.addWidget(button)
        letter = QLabel(f"{LETTERS[at]}.")
        letter.setObjectName("qcm-letter")
        layout.addWidget(letter)
        shown = question["options"][at]
        if mathview.available() and mathview.has_math(shown):
            dark = row.property("state") in ("right", "wrong")  # on a tinted row the text is dark in any theme
            text: QWidget = mathview.MathView(shown, color="#1f2a20" if dark else None)
            text.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)  # the row takes the click
        else:
            text = QLabel(shown)
            text.setWordWrap(True)
            text.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(text, 1)
        if done and correct is not None and row.property("state") == "missed":
            layout.addWidget(QLabel("correct answer"))
        return row, button

    def _text_box(self, index: int, given: str, done: bool) -> QPlainTextEdit:
        box = QPlainTextEdit()
        box.setPlaceholderText("Your answer")
        box.setPlainText(given)
        box.setReadOnly(done)
        box.setFixedHeight(TEXT_HEIGHT)
        box.textChanged.connect(lambda i=index, b=box: self._typed(i, b.toPlainText()))
        return box

    def _footer(self) -> QHBoxLayout:
        footer = QHBoxLayout()
        if self.answers is not None:
            right, asked = grade(self.form, self.answers) if self.form.get("graded") else (0, 0)
            footer.addWidget(self._label(
                f"{right} / {asked} correct answer{'' if asked == 1 else 's'}" if asked else "Answers sent to Clara.", "qcm-score"
            ), 1)
            return footer
        self._progress = QLabel()
        self._progress.setObjectName("qcm-progress")
        self._send = QPushButton("Send answers")
        self._send.clicked.connect(self._on_send)
        footer.addWidget(self._progress, 1)
        footer.addWidget(self._send)
        self._refresh()
        return footer

    # -- answering ---------------------------------------------------------------------- #

    def _toggled(self, index: int, option: int, on: bool) -> None:
        if self.answers is not None:
            return
        chosen = [n for n in self._draft[index] if n != option]
        self._draft[index] = ([option] if self.form["questions"][index]["type"] == "single" else chosen + [option]) if on else chosen
        for button in self._buttons_of(index):
            button.parentWidget().setProperty("picked", "true" if button.isChecked() else "false")
            button.parentWidget().style().unpolish(button.parentWidget())
            button.parentWidget().style().polish(button.parentWidget())
        self._refresh()

    def _buttons_of(self, index: int) -> list[QAbstractButton]:
        position = sum(1 for q in self.form["questions"][:index] if q["type"] != "text")
        return self._buttons[position] if self.form["questions"][index]["type"] != "text" else []

    def _typed(self, index: int, text: str) -> None:
        self._draft[index] = text
        self._refresh()

    def given(self) -> int:
        return sum(1 for a in self._draft if (a.strip() if isinstance(a, str) else a))

    def _refresh(self) -> None:
        if self._send is None or self._progress is None:
            return
        count = self.given()
        self._progress.setText(f"{count} of {len(self.form['questions'])} answered")
        self._send.setEnabled(count > 0)

    def draft_answers(self) -> list:
        return [a.strip() if isinstance(a, str) else sorted(a) for a in self._draft]

    def _on_send(self) -> None:
        self.submitted.emit(format_answers(self.form, self.draft_answers()))

    def lock(self) -> None:
        """The answers were sent: show the form answered, with its corrections."""
        self.answers = self.draft_answers()
        while self._column.count():
            item = self._column.takeAt(0)
            if item.widget() is not None:
                item.widget().deleteLater()
            elif item.layout() is not None:
                _drop(item.layout())
        self._buttons, self._send, self._progress = [], None, None
        self._build()

    def warn(self, text: str) -> None:
        if self._progress is not None:
            self._progress.setText(text)


def _drop(layout) -> None:
    while layout.count():
        item = layout.takeAt(0)
        if item.widget() is not None:
            item.widget().deleteLater()
