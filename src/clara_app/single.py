"""One copy of the app per user: starting it again asks the running copy to show its window."""

from __future__ import annotations

import getpass

from PySide6.QtCore import QObject, Signal
from PySide6.QtNetwork import QLocalServer, QLocalSocket

SHOW = b"show"
CONNECT_MS = 500


def default_name() -> str:
    try:
        user = getpass.getuser()
    except Exception:
        user = "user"
    return f"clara-app-{user}"


class SingleInstance(QObject):
    """`acquire()` is True for the first copy, which then emits `show_requested` whenever another copy
    is started (that one asks, then exits)."""

    show_requested = Signal()

    def __init__(self, name: str | None = None, parent=None):
        super().__init__(parent)
        self.name = name or default_name()
        self._server: QLocalServer | None = None

    def acquire(self) -> bool:
        probe = QLocalSocket()
        probe.connectToServer(self.name)
        if probe.waitForConnected(CONNECT_MS):  # a copy is running: ask it to show itself
            probe.write(SHOW)
            probe.waitForBytesWritten(CONNECT_MS)
            probe.disconnectFromServer()
            return False
        QLocalServer.removeServer(self.name)  # a crashed copy may have left its socket behind
        server = QLocalServer(self)
        if not server.listen(self.name):
            return True  # cannot guard (rare): run anyway rather than refuse to start
        server.newConnection.connect(self._connection)
        self._server = server
        return True

    def _connection(self) -> None:
        assert self._server is not None
        while self._server.hasPendingConnections():
            socket = self._server.nextPendingConnection()
            socket.readyRead.connect(lambda s=socket: self._read(s))
            socket.disconnected.connect(socket.deleteLater)
            if socket.bytesAvailable():
                self._read(socket)

    def _read(self, socket: QLocalSocket) -> None:
        if bytes(socket.readAll()) == SHOW:
            self.show_requested.emit()
