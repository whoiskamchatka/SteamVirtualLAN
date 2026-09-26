"""Only one SteamVirtualLAN per Windows user.

The app keeps running in the notification area after its window is closed,
so starting it again is how people bring it back. A second copy would start
Steam's API a second time and fight the first over the same virtual adapter,
so it only asks the running one to show its window, and exits.
"""

import getpass

from PySide6.QtCore import QObject, Signal
from PySide6.QtNetwork import QLocalServer, QLocalSocket

CONNECT_TIMEOUT_MS = 500


def server_name() -> str:
    return f"SteamVirtualLAN-{getpass.getuser()}"


class SingleInstance(QObject):
    # Another start of the app asked this one to show itself.
    activated = Signal()

    def __init__(self, name: str | None = None, parent: QObject | None = None):
        super().__init__(parent)
        self.name = name or server_name()
        self.server: QLocalServer | None = None

    def claim(self) -> bool:
        """True if this is the only instance. Otherwise the running one is
        asked to show itself, and this one should exit."""
        socket = QLocalSocket()
        socket.connectToServer(self.name)
        if socket.waitForConnected(CONNECT_TIMEOUT_MS):
            socket.disconnectFromServer()
            return False
        server = QLocalServer(self)
        if not server.listen(self.name):
            # A server left behind by a crashed instance (not on Windows,
            # where the pipe goes away with its process).
            QLocalServer.removeServer(self.name)
            if not server.listen(self.name):
                return True
        server.newConnection.connect(self._connection)
        self.server = server
        return True

    def close(self) -> None:
        if self.server is not None:
            self.server.close()
            self.server = None

    def _connection(self) -> None:
        # Connecting is the whole request; what the other side writes doesn't matter.
        while self.server is not None and self.server.hasPendingConnections():
            connection = self.server.nextPendingConnection()
            connection.disconnected.connect(connection.deleteLater)
            connection.disconnectFromServer()
            self.activated.emit()
