r"""Formulas: found in Clara's text, kept apart from the Markdown, and typeset in a web view (chat bubbles, QCM cards)."""

from __future__ import annotations

import pytest
from conftest import wait_until
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QFrame, QLabel, QRadioButton

from clara_app import mathview
from clara_app.api import ClaraApi
from clara_app.chat_view import CLARA, USER, ChatView
from clara_app.chat_window import ChatWindow
from clara_app.mathview import extract_math, has_math, to_html
from clara_app.qcm import QcmCard

webengine = pytest.mark.skipif(not mathview.available(), reason="Qt WebEngine is not installed")

FORM = {
    "ref": "0a1b2c3d",
    "title": r"Calculus $\int$",
    "graded": True,
    "questions": [
        {"text": r"What is $\int_0^1 x^2\,dx$?", "type": "single", "options": [r"$\frac{1}{3}$", "one half"],
         "correct": [0], "explanation": r"Because $\int x^2 = \frac{x^3}{3}$.", "answer": ""},
        {"text": "Plain question?", "type": "text", "options": [], "correct": None, "explanation": "",
         "answer": r"$e^{x}$"},
    ],
}


# ---- finding the formulas (no web view needed) ------------------------------------------------------------

def test_the_four_notations_are_formulas():
    text, formulas = extract_math(r"a $x^2$ b \(y\) c $$z$$ d \[w\]")
    assert formulas == [("x^2", False), ("y", False), ("z", True), ("w", True)]
    assert "$" not in text and "\\" not in text


def test_money_code_and_escaped_dollars_are_not_formulas():
    for text in ("costs $5 to $10", r"it is \$3 and \$4", "`$x$` in code", "```\n$$x$$\n```", "no formula here", ""):
        assert not has_math(text), text


def test_a_formula_after_a_code_block_is_still_found():
    assert extract_math("```py\nx = '$'\n```\nthen $y$")[1] == [("y", False)]


def test_an_unclosed_fence_hides_the_rest_as_code():
    assert not has_math("```\nstill writing $x$")


def test_the_markdown_leaves_formulas_alone_and_escapes_the_rest():
    page = to_html(r"**bold** $a_1 * b_2$ <b>raw</b> \[\frac{1}{2}\]" + "\n\n- item")
    assert "<strong>bold</strong>" in page and "<li>item</li>" in page
    assert 'data-tex="a_1 * b_2"' in page  # not turned into emphasis
    assert 'class="math display" data-tex="\\frac{1}{2}"' in page
    assert "&lt;b&gt;raw&lt;/b&gt;" in page and "<b>" not in page


def test_a_formula_cannot_break_out_of_its_attribute():
    page = to_html('$"><script>alert(1)</script>$')
    assert "<script>" not in page and "&lt;script&gt;" in page


def test_links_with_a_script_are_not_made_into_links():
    assert "href" not in to_html("[x](javascript:alert(1)) and $y$")


# ---- the web view -----------------------------------------------------------------------------------------

def typeset_count(view) -> int:
    found = []
    view.page().runJavaScript("document.querySelectorAll('.katex').length", found.append)
    wait_until(lambda: bool(found), timeout=10)
    return found[0]


@webengine
def test_a_view_typesets_its_formulas_and_takes_the_height_of_its_page(qt):
    view = mathview.MathView(r"Hello $\frac{1}{3}$" + "\n\n" + r"$$\sum_{k=1}^n k$$")
    view.resize(400, 100)
    view.show()
    wait_until(lambda: view.height() > 40, timeout=15)
    assert typeset_count(view) == 2
    view.close()


@webengine
def test_a_text_that_is_not_math_keeps_its_label_and_a_streamed_one_is_typeset_when_complete(qt):
    view = ChatView()
    view.resize(500, 400)
    view.show()
    plain = view.add(CLARA, "just **words**")
    assert plain.math is None
    streamed = view.add(CLARA, "")
    streamed.set_text(r"so $x^2$")
    assert streamed.math is None  # still arriving: the source in a label
    streamed.settle()
    assert streamed.math is not None and streamed.text == r"so $x^2$"
    wait_until(lambda: typeset_count(streamed.math) == 1, timeout=15)
    assert view.add(USER, r"my $x$").math is None  # what the user types is not typeset
    view.clear()


@webengine
def test_the_answers_of_a_conversation_read_back_are_typeset(qt, config, server):
    _, state = server
    state.add_conversation("app:tester:m", ("integral?", r"It is $\frac{1}{3}$."), title="Math")
    window = ChatWindow(lambda: config, ClaraApi)
    window.show()
    window.open_conversation("app:tester:m")
    wait_until(lambda: window.conversation == "app:tester:m")
    bubble = next(b for b in window.view.bubbles if b.role == CLARA)
    assert bubble.math is not None
    wait_until(lambda: typeset_count(bubble.math) == 1, timeout=15)
    window.quit_for_good()


@webengine
def test_a_live_answer_with_a_formula_is_typeset_once_it_is_complete(qt, config, server):
    _, state = server
    state.reply = ["The area is ", r"$\frac{1}{3}$."]
    window = ChatWindow(lambda: config, ClaraApi)
    window.show()
    window.send("area?")
    wait_until(lambda: not window.busy)
    bubble = next(b for b in window.view.bubbles if b.role == CLARA)
    assert bubble.math is not None and bubble.text == r"The area is $\frac{1}{3}$."
    window.quit_for_good()


@webengine
def test_a_qcm_card_typesets_its_formulas_and_keeps_plain_texts_as_labels(qt):
    card = QcmCard(FORM)
    card.resize(500, 400)
    card.show()
    views = card.findChildren(mathview.MathView)
    assert len(views) == 3  # the title, the first question, its first option; not "one half" nor the plain question
    wait_until(lambda: all(typeset_count(v) >= 1 for v in views), timeout=20)
    radios = card.findChildren(QRadioButton)
    row = radios[0].parentWidget()
    assert all(v.testAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents) for v in row.findChildren(mathview.MathView))
    QTest.mouseClick(row, Qt.MouseButton.LeftButton)  # a click on the row selects its option
    assert radios[0].isChecked()
    card.close()


@webengine
def test_an_answered_card_shows_formulas_of_its_explanation(qt):
    card = QcmCard(FORM, [[1], "e^x"])
    card.resize(500, 600)
    card.show()
    boxes = [f for f in card.findChildren(QFrame) if f.objectName() == "qcm-explain"]
    assert len(boxes) == 2  # the explanation and the expected answer both hold a formula
    wait_until(lambda: all(typeset_count(b.findChild(mathview.MathView)) >= 1 for b in boxes), timeout=20)
    card.close()


@webengine
def test_the_number_of_a_question_with_a_formula_is_not_taken_for_a_list(qt):
    card = QcmCard(FORM)
    card.show()
    question = next(v for v in card.findChildren(mathview.MathView) if v.objectName() == "qcm-question")
    assert "<ol" not in to_html(question.text) and to_html(question.text).startswith("<p>1. What is")
    card.close()


def test_each_option_has_its_letter_apart_from_its_text(qt):
    card = QcmCard({**FORM, "questions": [FORM["questions"][0]]})
    letters = [label.text() for label in card.findChildren(QLabel) if label.objectName() == "qcm-letter"]
    assert letters == ["A.", "B."]
