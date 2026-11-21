"""Проверка обновлений: вышла ли на GitHub новая версия программы.

Не чаще раза в день, один запрос к открытому API GitHub без каких-либо данных о пользователе.
Ничего не скачивается и не ставится само: программа только показывает, что есть новая версия,
и ссылку на страницу релиза.
"""

from __future__ import annotations

import os
import re
import time

import requests

from . import __version__

REPO = "agoiel/ATlate"
API = f"https://api.github.com/repos/{REPO}/releases/latest"     # черновики и пре-релизы сюда не попадают
PAGE = f"https://github.com/{REPO}/releases/latest"
EVERY = 24 * 3600            # как часто проверять, секунды


def parse(version: str) -> tuple[int, int, int, int]:
    """«v0.6.5» → (0, 6, 5, 0). Нет цифр — (0, 0, 0, 0)."""
    nums = [int(n) for n in re.findall(r"\d+", version or "")[:4]]
    return tuple(nums + [0] * (4 - len(nums)))  # type: ignore[return-value]


def is_newer(latest: str, current: str = __version__) -> bool:
    return any(parse(latest)) and parse(latest) > parse(current)


def due(cfg: dict, now: float | None = None) -> bool:
    """Пора ли проверять: проверка включена и с прошлой прошли сутки.
    ATLATE_NO_UPDATE_CHECK=1 — не проверять (так делают тесты, чтобы не ходить в сеть)."""
    if not cfg.get("update_check", True) or os.environ.get("ATLATE_NO_UPDATE_CHECK"):
        return False
    return (now if now is not None else time.time()) - float(cfg.get("update_checked_at") or 0) >= EVERY


def fetch(timeout: float = 10) -> tuple[str, str] | None:
    """Последний релиз на GitHub → (версия, ссылка на страницу) или None, если узнать не удалось."""
    try:
        r = requests.get(API, timeout=timeout, headers={"Accept": "application/vnd.github+json",
                                                         "User-Agent": "ATlate-update-check"})
        if r.status_code != 200:
            return None
        data = r.json()
    except (requests.RequestException, ValueError):
        return None
    tag = str(data.get("tag_name") or "").strip()
    if not tag or data.get("draft") or data.get("prerelease"):
        return None
    url = str(data.get("html_url") or PAGE)
    if not url.startswith("https://github.com/"):
        url = PAGE
    return tag.lstrip("vV"), url


def offer(cfg: dict, found: tuple[str, str] | None) -> tuple[str, str] | None:
    """Что показать пользователю: новая версия, если она новее текущей и её не скрыли кнопкой «Позже»."""
    try:
        version, url = (str(x) for x in found)
    except (TypeError, ValueError):
        return None
    if not url.startswith("https://github.com/"):
        url = PAGE
    if not is_newer(version) or version == cfg.get("update_skip", ""):
        return None
    return version, url
