"""Attaching documents: PDF, code and text files are read on this computer and sent with the message."""

from __future__ import annotations

import pytest
from conftest import wait_until

from clara_app.chat_view import ERROR, USER
from clara_app.chat_window import ChatWindow
from clara_app.documents import (
    MAX_TOTAL_CHARS,
    Document,
    DocumentError,
    compose,
    read_document,
)


def make_pdf(*pages: str) -> bytes:
    """A small valid PDF with one line of text per page."""
    objects = ["<< /Type /Catalog /Pages 2 0 R >>", ""]
    kids = []
    for text in pages:
        stream = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode()
        content = len(objects) + 2
        kids.append(f"{len(objects) + 1} 0 R")
        objects.append(
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents {content} 0 R "
            "/Resources << /Font << /F1 << /Type /Font /Subtype /Type1 /BaseFont /Helvetica >> >> >> >>"
        )
        objects.append(f"<< /Length {len(stream)} >>\nstream\n{stream.decode()}\nendstream")
    objects[1] = f"<< /Type /Pages /Kids [{' '.join(kids)}] /Count {len(pages)} >>"
    out = b"%PDF-1.4\n"
    offsets = []
    for number, body in enumerate(objects, 1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n{body}\nendobj\n".encode()
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode()
    out += "".join(f"{offset:010d} 00000 n \n" for offset in offsets).encode()
    out += f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return out


# --- reading ------------------------------------------------------------------------------------


def test_python_c_and_markdown_are_read_with_their_language(tmp_path):
    for name, language in (("main.py", "python"), ("lib.c", "c"), ("lib.h", "c"), ("README.md", "markdown")):
        path = tmp_path / name
        path.write_bytes(b"line one\r\nline two\n")  # Windows line ends become plain ones
        document = read_document(path)
        assert (document.kind, document.text, document.size) == (language, "line one\nline two\n", "2 lines")


def test_any_other_text_file_is_read_as_text(tmp_path):
    path = tmp_path / "notes.weird"
    path.write_text("hello", encoding="utf-8")
    assert read_document(path).kind == "" and read_document(path).size == "1 line"


def test_a_file_that_is_not_utf8_is_still_read(tmp_path):
    path = tmp_path / "old.c"
    path.write_bytes("/* café */".encode("cp1252"))
    assert read_document(path).text == "/* café */"


def test_a_pdf_gives_the_text_of_its_pages(tmp_path):
    path = tmp_path / "paper.pdf"
    path.write_bytes(make_pdf("Hello PDF", "Second page"))
    document = read_document(path)
    assert (document.kind, document.pages, document.size) == ("pdf", 2, "2 pages")
    assert "[page 1]\nHello PDF" in document.text and "[page 2]\nSecond page" in document.text


@pytest.mark.parametrize(
    ("name", "content", "message"),
    [
        ("image.png", b"\x89PNG\r\n\x1a\n\x00\x00\x00", "not a text file"),
        ("empty.txt", b"   \n", "empty"),
        ("broken.pdf", b"%PDF-1.4 this is not really a pdf", "could not be read"),
        ("blank.pdf", make_pdf(""), "no text"),
    ],
)
def test_what_cannot_be_read_is_explained(tmp_path, name, content, message):
    path = tmp_path / name
    path.write_bytes(content)
    with pytest.raises(DocumentError, match=message):
        read_document(path)


def test_a_missing_file_is_explained(tmp_path):
    with pytest.raises(DocumentError, match="Cannot read"):
        read_document(tmp_path / "gone.py")


# --- what Clara is sent -----------------------------------------------------------------------------


def test_code_goes_in_a_fence_that_its_own_backticks_cannot_close(tmp_path):
    document = Document(tmp_path / "doc.md", "markdown", "Example:\n```python\nprint(1)\n```\n")
    sent = document.for_model()
    assert sent.startswith('<document name="doc.md" type="markdown">\n````markdown\n')
    assert sent.endswith("\n````\n</document>")


def test_the_message_is_the_text_then_the_documents(tmp_path):
    code = Document(tmp_path / "a.py", "python", "print('hi')")
    pdf = Document(tmp_path / "b.pdf", "pdf", "[page 1]\nHello", 1)
    message = compose("What do these do?", [code, pdf])
    assert message.startswith("What do these do?\n\n(Attached: a.py, b.pdf)\n\n<document name=\"a.py\" type=\"python\">")
    assert '<document name="b.pdf" type="pdf">\n[page 1]\nHello\n</document>' in message
    assert compose("", [code]).startswith("Here is: a.py.")
    assert compose("just text", []) == "just text"


# --- the window ---------------------------------------------------------------------------------------


def make_window(qt, config) -> ChatWindow:
    return ChatWindow(lambda: config)


def test_attached_documents_are_sent_with_the_next_message(qt, config, server, tmp_path):
    _, state = server
    (tmp_path / "main.c").write_text("int main(void) { return 0; }\n", encoding="utf-8")
    (tmp_path / "paper.pdf").write_bytes(make_pdf("Results"))
    window = make_window(qt, config)
    window.attach([str(tmp_path / "main.c"), str(tmp_path / "paper.pdf")])
    wait_until(lambda: len(window.documents) == 2 and not window._readers)
    assert window.attachments.isVisibleTo(window) and window.attach_button.isEnabled()

    window.input.setPlainText("Explain")
    window.send()
    wait_until(lambda: not window.busy)

    message = state.chat_bodies[0]["message"]
    assert message.startswith("Explain\n\n(Attached: main.c, paper.pdf)")
    assert "```c\nint main(void) { return 0; }\n```" in message and "[page 1]\nResults" in message
    shown = [text for role, text in window.view.texts() if role == USER][-1]
    assert "📎 main\\.c (1 line)" in shown and "📎 paper\\.pdf (1 page)" in shown
    assert window.documents == [] and not window.attachments.isVisibleTo(window)  # gone with that message
    window.quit_for_good()


def test_a_document_alone_can_be_sent(qt, config, server, tmp_path):
    _, state = server
    (tmp_path / "notes.md").write_text("# Notes", encoding="utf-8")
    window = make_window(qt, config)
    window.attach([str(tmp_path / "notes.md")])
    wait_until(lambda: window.documents and not window._readers)
    window.send()
    wait_until(lambda: state.chat_bodies and not window.busy)
    assert state.chat_bodies[0]["message"].startswith("Here is: notes.md.")
    window.quit_for_good()


def test_a_document_can_be_removed_and_is_not_attached_twice(qt, config, tmp_path):
    (tmp_path / "a.py").write_text("x = 1", encoding="utf-8")
    window = make_window(qt, config)
    window.attach([str(tmp_path / "a.py")])
    wait_until(lambda: window.documents and not window._readers)
    window.attach([str(tmp_path / "a.py")])  # already there: nothing to read
    assert len(window.documents) == 1 and not window._readers
    window.detach(window.documents[0])
    assert window.documents == [] and not window.attachments.isVisibleTo(window)
    window.quit_for_good()


def test_an_unreadable_or_too_big_document_is_explained(qt, config, tmp_path):
    (tmp_path / "pic.png").write_bytes(b"\x89PNG\x00\x00")
    (tmp_path / "huge.txt").write_text("x" * (MAX_TOTAL_CHARS + 10), encoding="utf-8")
    window = make_window(qt, config)
    window.attach([str(tmp_path / "pic.png"), str(tmp_path / "huge.txt")])
    wait_until(lambda: not window._readers)
    errors = [text for role, text in window.view.texts() if role == ERROR]
    assert len(errors) == 2 and "not a text file" in errors[0] and "does not fit" in errors[1]
    assert window.documents == []
    window.quit_for_good()
