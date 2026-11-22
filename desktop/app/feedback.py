"""Обратная связь: куда писать об ошибках и идеях, и готовое письмо с версией программы."""

from __future__ import annotations

import platform
from urllib.parse import quote

from . import __version__
from .i18n import tr

EMAIL = "arcgobabuiss@gmail.com"


def mailto() -> str:
    """Ссылка mailto: с темой и началом письма — версия программы и системы уже вписаны."""
    subject = tr("ATlate {0}: отзыв", __version__)
    body = tr("Что случилось или что хочется улучшить:\n\n\nЧто делали перед этим:\n\n\n"
              "ATlate {0} · {1}\n", __version__, platform.platform(terse=True))
    return f"mailto:{EMAIL}?subject={quote(subject)}&body={quote(body)}"
