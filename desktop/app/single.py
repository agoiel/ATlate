"""Одна копия программы на пользователя.

Если ATlate запустить второй раз (двойной щелчок по ярлыку, автозапуск поверх открытой), раньше
открывалась вторая копия. Горячие клавиши доставались первой, кнопки на экране могли быть второй,
и перевод одной копии нельзя было убрать ни клавишами, ни кнопками другой — помогало только
закрыть программу. Особенно незаметно в компактном режиме: обе панели встают в одно и то же
запомненное место, одна ровно поверх другой.

Теперь вторая копия только просит первую показаться и сразу закрывается. Связь — локальный
канал (QLocalServer: на Windows именованный канал), имя — своё для каждого пользователя.
При перезапуске программы (смена языка интерфейса, докачка распознавания) канал сначала
освобождается, чтобы новая копия не приняла закрывающуюся за уже запущенную.
"""

from __future__ import annotations

import getpass
import hashlib
from collections.abc import Callable

from PySide6.QtNetwork import QLocalServer, QLocalSocket


def _name() -> str:
    try:
        user = getpass.getuser()
    except Exception:  # noqa: BLE001
        user = ""
    return "ATlate-" + hashlib.sha1(user.encode("utf-8", "replace")).hexdigest()[:12]


NAME = _name()
_server: QLocalServer | None = None


def ask_running(name: str = NAME, timeout_ms: int = 500) -> bool:
    """Уже запущена другая копия? Если да — попросить её показаться (True)."""
    sock = QLocalSocket()
    sock.connectToServer(name)
    if not sock.waitForConnected(timeout_ms):
        return False
    sock.write(b"show\n")
    sock.flush()
    sock.waitForBytesWritten(timeout_ms)
    sock.disconnectFromServer()
    return True


def claim(on_show: Callable[[], None], name: str = NAME) -> bool:
    """Стать единственной копией. False — другая копия уже запущена (её попросили показаться).
    on_show — что делать, когда кто-то запускает программу ещё раз."""
    global _server
    if ask_running(name):
        return False
    QLocalServer.removeServer(name)     # остался после аварийного выхода (на Linux — файл сокета)
    server = QLocalServer()
    server.setSocketOptions(QLocalServer.SocketOption.UserAccessOption)

    def on_connection() -> None:
        while server.hasPendingConnections():
            conn = server.nextPendingConnection()
            conn.disconnected.connect(conn.deleteLater)
            on_show()

    server.newConnection.connect(on_connection)
    if not server.listen(name):
        return True      # канал не открылся — работаем как раньше, без защиты от второй копии
    _server = server
    return True


def release() -> None:
    """Освободить имя (перед перезапуском программы)."""
    global _server
    if _server is not None:
        _server.close()
        _server = None
