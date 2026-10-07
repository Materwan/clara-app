r"""Text with LaTeX formulas, typeset by KaTeX in a web view (Qt WebEngine).

Clara writes formulas as `$x^2$` and `\(x^2\)` (inline), `$$...$$` and `\[...\]` (on their own lines), as the web site
does. A label cannot show them, so a message or a QCM text that holds one is shown by a `MathView`: the Markdown is
turned into HTML here (the formulas set aside first, so that Markdown leaves them alone), and a small page typesets
them with the KaTeX files that come with the app (`katex/`, the same as the web site's). Text without a formula stays
in an ordinary label: web views are heavy, and only those that need one get one.

What the page can do is limited: it loads nothing but the local KaTeX files (a Content-Security-Policy refuses the
network, so an image in an answer is not fetched), raw HTML of the answer is shown as text, and a click on a link
opens the system browser instead of navigating the view.
"""

from __future__ import annotations

import html
import re
from pathlib import Path
from string import Template

from markdown_it import MarkdownIt
from PySide6.QtCore import QEvent, QObject, QSize, Qt, QUrl, Signal
from PySide6.QtGui import QPalette
from PySide6.QtWidgets import QAbstractScrollArea, QApplication, QSizePolicy, QWidget

from .links import open_external
from .theme import THEME

try:  # Qt WebEngine is a large optional part of PySide6: without it formulas stay as their source
    from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineProfile, QWebEngineSettings
    from PySide6.QtWebEngineWidgets import QWebEngineView

    WEBENGINE = True
except ImportError:  # pragma: no cover - depends on the installation
    WEBENGINE = False

KATEX_DIR = Path(__file__).parent / "katex"
HEIGHT_PREFIX = "h:"  # the page tells its height through its title, which Qt reports as a signal
WHEEL_NOTCH = 120  # angleDelta of one notch of a mouse wheel
WHEEL_LINES = 3  # lines a notch scrolls

# ---- finding the formulas ---------------------------------------------------------------------------
# The same rules as the web site's markdown.js: \$ is a dollar sign, $...$ hugs its formula and no digit follows
# (so "from $5 to $10" is no formula), code is left alone.

_FENCE = re.compile(
    r"^[ \t]*(`{3,}|~{3,})[^\n]*\n(?:[\s\S]*?\n)?[ \t]*\1[`~]*[ \t]*$|^[ \t]*(?:`{3,}|~{3,})[^\n]*\n[\s\S]*\Z",
    re.MULTILINE,
)
_PIECE = re.compile(
    r"""(?P<code>(`+)[\s\S]*?[^`]\2(?!`))
      | (?P<dollar>\\\$)
      | \$\$(?P<d1>[^`]+?)\$\$
      | \\\[(?P<d2>[\s\S]+?)\\\]
      | \\\((?P<i1>[\s\S]+?)\\\)
      | \$(?![\s$])(?P<i2>(?:[^$`\\\n]|\\[\s\S])+?)(?<!\s)\$(?!\d)
    """,
    re.VERBOSE,
)
_TOKEN = "CLARAMATH{}X"  # letters and digits only: Markdown leaves it as it is
_TOKEN_RE = re.compile(r"CLARAMATH(\d+)X")


def extract_math(text: str) -> tuple[str, list[tuple[str, bool]]]:
    """The text with each formula replaced by a token, and the formulas `(tex, display)` in the order of the tokens."""
    formulas: list[tuple[str, bool]] = []

    def piece(found: re.Match) -> str:
        if found.group("code") is not None or found.group("dollar") is not None:
            return found.group(0)
        display = found.group("d1") is not None or found.group("d2") is not None
        tex = (found.group("d1") or found.group("d2") or found.group("i1") or found.group("i2")).strip()
        formulas.append((tex, display))
        return _TOKEN.format(len(formulas) - 1)

    out, last = [], 0
    for fence in _FENCE.finditer(text):
        out.append(_PIECE.sub(piece, text[last : fence.start()]))
        out.append(fence.group(0))
        last = fence.end()
    out.append(_PIECE.sub(piece, text[last:]))
    return "".join(out), formulas


def has_math(text: str) -> bool:
    return bool(text) and bool(extract_math(text)[1])


_MARKDOWN = MarkdownIt("commonmark", {"html": False, "breaks": True}).enable(["table", "strikethrough"])


def to_html(text: str) -> str:
    """The HTML of a message: Markdown, with a `<span class="math" data-tex="...">` where each formula goes (KaTeX
    fills it in the page; the source stays there if it cannot)."""
    protected, formulas = extract_math(text.replace("\r\n", "\n"))
    body = _MARKDOWN.render(protected)

    def span(found: re.Match) -> str:
        index = int(found.group(1))
        if index >= len(formulas):
            return found.group(0)
        tex, display = formulas[index]
        source = html.escape(tex, quote=False)
        return f'<span class="math{" display" if display else ""}" data-tex="{html.escape(tex, quote=True)}">{source}</span>'

    return _TOKEN_RE.sub(span, body)


# ---- the page ---------------------------------------------------------------------------------------

PAGE = Template(
    """<!doctype html><html><head><meta charset="utf-8"><title>h:0</title>
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; script-src file: 'unsafe-inline'; style-src file: 'unsafe-inline'; font-src file:">
<link rel="stylesheet" href="katex.min.css">
<style>
html, body { margin: 0; padding: 0; overflow: hidden; background: transparent; }
#c { font: $size / 1.45 $family; color: $color; overflow-wrap: anywhere; $extra }
#c > :first-child { margin-top: 0; } #c > :last-child { margin-bottom: 0; }
p, ul, ol, blockquote, pre, table { margin: 0 0 .6em; }
ul, ol { padding-left: 1.4em; }
h1, h2, h3, h4 { margin: .7em 0 .3em; line-height: 1.25; }
h1 { font-size: 1.3em; } h2 { font-size: 1.2em; } h3, h4 { font-size: 1.08em; }
code { font-family: Consolas, "Cascadia Mono", monospace; font-size: .92em; background: rgba(128,128,128,.18); border-radius: 4px; padding: 0 .25em; }
pre { background: rgba(128,128,128,.14); border-radius: 6px; padding: .5em .7em; overflow-x: auto; }
pre code { background: none; padding: 0; }
blockquote { margin-left: 0; padding-left: .8em; border-left: 3px solid rgba(128,128,128,.5); }
table { border-collapse: collapse; } th, td { border: 1px solid rgba(128,128,128,.5); padding: .2em .5em; }
a { color: #3d5afe; }
.math:not(.typeset) { font-family: Consolas, "Cascadia Mono", monospace; font-size: .92em; }
.math.display { display: block; margin: .4em 0; overflow-x: auto; overflow-y: hidden; text-align: center; }
.katex { font-size: 1.08em; } .katex-display { margin: 0; }
</style>
<script src="katex.min.js"></script></head>
<body><div id="c">$body</div>
<script>
for (const node of document.querySelectorAll("span.math")) {
  try {
    katex.render(node.dataset.tex, node, { displayMode: node.classList.contains("display"), throwOnError: true, strict: "ignore", trust: false, maxExpand: 1000 });
    node.classList.add("typeset");
  } catch (error) { /* not a formula KaTeX knows: its source stays */ }
}
const root = document.getElementById("c");
const report = () => { document.title = "$prefix" + Math.ceil(root.getBoundingClientRect().height); };
new ResizeObserver(report).observe(root);
report();
if (document.fonts) document.fonts.ready.then(report);
</script></body></html>"""
)


def page_for(text: str, color: str, family: str, size: str, extra: str = "") -> str:
    return PAGE.substitute(
        body=to_html(text), color=color, family=family, size=size, extra=extra, prefix=HEIGHT_PREFIX
    )


def available() -> bool:
    return WEBENGINE and (KATEX_DIR / "katex.min.js").is_file()


if WEBENGINE:
    _profile: QWebEngineProfile | None = None

    def _shared_profile() -> QWebEngineProfile:
        """One profile for every view, and an off-the-record one: nothing of Clara's answers is cached on disk."""
        global _profile
        if _profile is None:
            _profile = QWebEngineProfile(QApplication.instance())
        return _profile

    class _Page(QWebEnginePage):
        def acceptNavigationRequest(self, url: QUrl, kind, is_main_frame: bool) -> bool:
            if kind == QWebEnginePage.NavigationType.NavigationTypeLinkClicked:
                open_external(url)  # only web pages and mail: see links.py
                return False
            return url.scheme() in ("file", "about", "data", "")

    class MathView(QWebEngineView):
        """Markdown text with formulas, drawn by a web page as tall as its content (`heightChanged`)."""

        heightChanged = Signal(int)

        def __init__(self, text: str, color: str | None = None, extra: str = "", parent: QWidget | None = None):
            super().__init__(parent)
            self.text = text
            self._color = color
            self._extra = extra  # CSS for the text as a whole: "font-weight: 600"
            self._tall = 24
            self._hooked: QObject | None = None
            self.setPage(_Page(_shared_profile(), self))
            self.page().setBackgroundColor(Qt.GlobalColor.transparent)
            self.page().settings().setAttribute(QWebEngineSettings.WebAttribute.ShowScrollBars, False)
            self.page().titleChanged.connect(self._titled)
            self.page().loadFinished.connect(lambda _ok: self._hook_wheel())
            self.setStyleSheet("background: transparent")
            self.setContextMenuPolicy(Qt.ContextMenuPolicy.NoContextMenu)
            self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            self.setFixedHeight(self._tall)
            self.set_text(text)
            THEME.changed.connect(self._retheme)

        def _retheme(self) -> None:
            self.set_text(self.text)  # in the colours of the new theme

        def set_text(self, text: str) -> None:
            self.text = text
            palette = self.palette()
            color = self._color or palette.color(QPalette.ColorRole.Text).name()
            font = QApplication.font()
            family = ", ".join(f'"{name}"' for name in [font.family()]) + ", system-ui, sans-serif"
            size = f"{font.pointSizeF() * 96 / 72:.1f}px" if font.pointSizeF() > 0 else f"{max(font.pixelSize(), 13)}px"
            self.setHtml(page_for(text, color, family, size, self._extra), QUrl.fromLocalFile(str(KATEX_DIR) + "/"))

        def sizeHint(self) -> QSize:
            return QSize(2000, self._tall)  # as wide as it is allowed to be

        def _titled(self, title: str) -> None:
            if not title.startswith(HEIGHT_PREFIX):
                return
            try:
                tall = max(int(title[len(HEIGHT_PREFIX) :]), 1)
            except ValueError:
                return
            if tall != self._tall:
                self._tall = tall
                self.setFixedHeight(tall)
                self.updateGeometry()
                self.heightChanged.emit(tall)

        # The page does not scroll: the wheel scrolls the conversation the view is in.
        def _hook_wheel(self) -> None:
            proxy = self.focusProxy()
            if proxy is not None and proxy is not self._hooked:
                self._hooked = proxy
                proxy.installEventFilter(self)

        def showEvent(self, event) -> None:
            super().showEvent(event)
            self._hook_wheel()

        def eventFilter(self, watched: QObject, event: QEvent) -> bool:
            if event.type() == QEvent.Type.Wheel:
                area = self.parentWidget()
                while area is not None and not isinstance(area, QAbstractScrollArea):
                    area = area.parentWidget()
                if area is not None:
                    bar = area.verticalScrollBar()
                    steps = event.angleDelta().y() / WHEEL_NOTCH
                    bar.setValue(bar.value() - round(steps * WHEEL_LINES * bar.singleStep()))
                    return True
            return super().eventFilter(watched, event)
