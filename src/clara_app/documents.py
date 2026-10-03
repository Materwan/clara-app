"""Documents attached to a message: read here, on this computer, and sent with the message as text.

Clara only ever sees the files the user picked (the paperclip, or a file dropped on the window):

    PDF               the text of each page (pypdf); a scanned PDF has no text and is refused
    code and text     .py, .c, .h, .md and any other text file: read as UTF-8 (or Windows-1252)

Each document goes into the message inside a `<document>` tag, code in a fenced block marked with its
language, so the model knows where a file starts and ends and what it is.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

MAX_TEXT_BYTES = 2_000_000  # a text file larger than this is surely not meant to be read whole
MAX_PDF_BYTES = 50_000_000
MAX_TOTAL_CHARS = 150_000  # all the documents of one message (the server takes 200 000 characters at most)
SNIFF_BYTES = 8192  # read to tell a text file from a binary one

LANGUAGES = {
    ".py": "python", ".pyw": "python", ".pyi": "python",
    ".c": "c", ".h": "c",
    ".cpp": "cpp", ".cc": "cpp", ".cxx": "cpp", ".hpp": "cpp", ".hh": "cpp",
    ".md": "markdown", ".markdown": "markdown",
    ".txt": "", ".log": "", ".rst": "rst", ".tex": "latex",
    ".json": "json", ".yaml": "yaml", ".yml": "yaml", ".toml": "toml", ".ini": "ini", ".cfg": "ini",
    ".csv": "csv", ".xml": "xml", ".html": "html", ".htm": "html", ".css": "css", ".sql": "sql",
    ".js": "javascript", ".ts": "typescript", ".java": "java", ".cs": "csharp", ".rs": "rust", ".go": "go",
    ".sh": "bash", ".ps1": "powershell", ".bat": "bat", ".cmd": "bat", ".asm": "asm", ".s": "asm",
    ".m": "matlab", ".r": "r", ".lua": "lua", ".kt": "kotlin", ".swift": "swift", ".php": "php", ".rb": "ruby",
}

# For the file dialog
FILTER = ";;".join(
    [
        "Documents (*.pdf " + " ".join(f"*{suffix}" for suffix in sorted(LANGUAGES)) + ")",
        "PDF (*.pdf)",
        "Python (*.py)",
        "C / C++ (*.c *.h *.cpp *.hpp)",
        "Markdown (*.md *.markdown)",
        "All files (*)",
    ]
)


class DocumentError(Exception):
    """The file cannot be attached (the message says why, and is fit to show)."""


@dataclass(frozen=True)
class Document:
    path: Path
    kind: str  # "pdf", a language ("python", "c", "markdown"...), or "" for plain text
    text: str
    pages: int = 0  # a PDF's

    @property
    def name(self) -> str:
        return self.path.name

    @property
    def size(self) -> str:
        """How big it is, in words a person reads."""
        if self.kind == "pdf":
            return f"{self.pages} page{'s' if self.pages != 1 else ''}"
        lines = self.text.count("\n") + (0 if self.text.endswith("\n") or not self.text else 1)
        return f"{lines} line{'s' if lines != 1 else ''}"

    def for_model(self) -> str:
        """The document as it is put in the message."""
        kind = self.kind or "text"
        if self.kind == "pdf":
            body = self.text
        else:
            longest = max((len(run) for run in re.findall(r"`+", self.text)), default=0)
            fence = "`" * max(3, longest + 1)  # longer than any run of backticks inside: it cannot be closed early
            body = f"{fence}{self.kind}\n{self.text.rstrip()}\n{fence}"
        return f'<document name="{self.name}" type="{kind}">\n{body}\n</document>'


def _decode(data: bytes) -> str:
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return data.decode("cp1252", errors="replace")


def _read_text(path: Path, size: int) -> Document:
    if size > MAX_TEXT_BYTES:
        raise DocumentError(f"{path.name} is too big to send ({size // 1000:,} kB; at most {MAX_TEXT_BYTES // 1_000_000} MB).")
    data = path.read_bytes()
    if b"\x00" in data[:SNIFF_BYTES]:
        raise DocumentError(f"{path.name} is not a text file: only PDF, code and text files can be read.")
    text = _decode(data).replace("\r\n", "\n")
    if not text.strip():
        raise DocumentError(f"{path.name} is empty.")
    return Document(path, LANGUAGES.get(path.suffix.lower(), ""), text)


def _read_pdf(path: Path, size: int) -> Document:
    if size > MAX_PDF_BYTES:
        raise DocumentError(f"{path.name} is too big ({size // 1_000_000} MB; at most {MAX_PDF_BYTES // 1_000_000} MB).")
    try:
        from pypdf import PdfReader
        from pypdf.errors import PdfReadError
    except ImportError:
        raise DocumentError("Reading PDF files needs pypdf: pip install pypdf") from None
    try:
        reader = PdfReader(str(path))
        if reader.is_encrypted and not reader.decrypt(""):
            raise DocumentError(f"{path.name} is protected by a password.")
        pages = [(page.extract_text() or "").strip() for page in reader.pages]
    except DocumentError:
        raise
    except (PdfReadError, ValueError, KeyError, TypeError, OSError) as error:
        raise DocumentError(f"{path.name} could not be read as a PDF ({error}).") from None
    if not any(pages):
        raise DocumentError(f"{path.name} has no text to read (a scanned PDF?).")
    text = "\n\n".join(f"[page {number}]\n{page}" for number, page in enumerate(pages, 1) if page)
    return Document(path, "pdf", text, len(pages))


def read_document(path: str | Path) -> Document:
    """The text of a file. DocumentError when it cannot be attached."""
    path = Path(path)
    try:
        size = path.stat().st_size
        if not path.is_file():
            raise DocumentError(f"{path.name} is not a file.")
        if path.suffix.lower() == ".pdf":
            return _read_pdf(path, size)
        return _read_text(path, size)
    except OSError as error:
        raise DocumentError(f"Cannot read {path.name}: {error.strerror or error}.") from None


def total_chars(documents: list[Document]) -> int:
    return sum(len(document.for_model()) for document in documents)


def compose(message: str, documents: list[Document]) -> str:
    """The message sent to Clara: what the user wrote, then the documents."""
    parts = [message.strip()] if message.strip() else []
    if documents:
        names = ", ".join(document.name for document in documents)
        parts.append(f"(Attached: {names})" if parts else f"Here {'is' if len(documents) == 1 else 'are'}: {names}.")
        parts += [document.for_model() for document in documents]
    return "\n\n".join(parts)


_DOCUMENT_START = re.compile(r'<document name="([^"\n]*)" type="[^"\n]*">\n')
_DOCUMENT_END = "\n</document>"
_ATTACHED = re.compile(r"(?:^|\n\n)(?:\(Attached: (?P<attached>[^\n]*)\)|Here (?:is|are): (?P<here>[^\n]*)\.)$")


def split_message(content: str) -> tuple[str, list[str]]:
    """A message as `compose` made it, taken apart: `(what the user wrote, names of the documents)`. A message
    without documents comes back whole."""
    start = re.search(r'(?:^|\n\n)(?=<document name=")', content)
    if start is None:
        return content, []
    head, position = content[: start.start()], start.end()
    names: list[str] = []
    while (opening := _DOCUMENT_START.match(content, position)) is not None:
        names.append(opening.group(1))
        # a document ends where the next one starts, or at the end (its text may hold "</document>" itself)
        end = content.find(_DOCUMENT_END, opening.end())
        while end != -1:
            after = end + len(_DOCUMENT_END)
            if after == len(content) or content.startswith('\n\n<document name="', after):
                break
            end = content.find(_DOCUMENT_END, end + 1)
        if end == -1:
            break
        position = end + len(_DOCUMENT_END) + 2
    if not names:
        return content, []
    return _ATTACHED.sub("", head).strip(), names


def _documents_start(content: str) -> int | None:
    """Where the documents of a message start, even if it was cut in the middle of the first tag."""
    marker = '<document name="'
    for found in re.finditer(r"\n\n<", content):
        if marker.startswith(content[found.start() + 2 : found.start() + 2 + len(marker)]):
            return found.start()
    return None


def preview(content: str) -> str:
    """One line saying what the start of a message is (it may be cut in the middle of a document)."""
    start = _documents_start(content)
    head = (content[:start] if start is not None else content).rstrip()
    attached = _ATTACHED.search(head)
    text = " ".join(_ATTACHED.sub("", head).split())
    if not text and attached:
        text = "📎 " + (attached.group("attached") or attached.group("here"))
    return text
