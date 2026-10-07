"""Opening an address somebody clicked, or the server sent: web pages and mail only.

A link in an answer is written by a language model, and the address the server gives (the Discord invitation, Google's
sign-in page) by the server: neither may start a program (`ms-msdt:`, `search-ms:`) or reach a file or a share
(`file:`, `smb:`, a `\\\\host\\share` that would send this computer's credentials to its owner).
"""

from __future__ import annotations

from urllib.parse import urlsplit

SAFE_SCHEMES = ("http", "https", "mailto")


def is_safe(url: object) -> bool:
    """Is it an `http`, `https` or `mailto` address (and not one that smuggles a host or another scheme)?"""
    text = url.toString() if hasattr(url, "toString") else str(url or "")
    text = text.strip()
    if not text or text.startswith(("\\", "//")) or any(ord(c) < 32 for c in text):
        return False
    try:
        parts = urlsplit(text)
    except ValueError:
        return False
    if parts.scheme.lower() not in SAFE_SCHEMES:
        return False
    return bool(parts.netloc) if parts.scheme.lower() != "mailto" else bool(parts.path)


def open_external(url: object) -> bool:
    """Open it in the browser (or the mail program) when it is safe. Returns whether it was opened."""
    if not is_safe(url):
        return False
    from PySide6.QtCore import QUrl
    from PySide6.QtGui import QDesktopServices

    return QDesktopServices.openUrl(url if isinstance(url, QUrl) else QUrl(str(url).strip()))
