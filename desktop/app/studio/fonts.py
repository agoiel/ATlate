"""Шрифты для тайпа: свои (папка fonts рядом с программой) и установленные в системе."""

from __future__ import annotations

import os
import sys
from functools import lru_cache
from pathlib import Path

from PIL import ImageFont

from ..config import ROOT

FONT_DIR = ROOT / "fonts"
FONT_EXT = {".ttf", ".otf", ".ttc"}

# Что взять по умолчанию, если пользователь ничего не выбрал (первый найденный)
PREFERRED = [
    "Pangolin Regular",            # кладётся в папку fonts при сборке: рукописный, с кириллицей
    "Comic Sans MS Bold",
    "Comic Sans MS Regular",
    "Segoe UI Semibold",
    "Arial Bold",
    "DejaVu Sans Bold",
]


def _dirs() -> list[Path]:
    out = [FONT_DIR]
    if sys.platform == "win32":
        win = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"
        local = Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "Windows" / "Fonts"
        out += [win, local]
    else:
        out += [Path("/usr/share/fonts"), Path.home() / ".fonts"]
    return out


@lru_cache(maxsize=1)
def list_fonts() -> tuple[tuple[str, str], ...]:
    """Все найденные шрифты: (название, путь). Свои шрифты — первыми."""
    seen: dict[str, str] = {}
    own: list[tuple[str, str]] = []
    other: list[tuple[str, str]] = []
    for d in _dirs():
        if not d.exists():
            continue
        for p in sorted(d.rglob("*")):
            if p.suffix.lower() not in FONT_EXT:
                continue
            try:
                family, style = ImageFont.truetype(str(p), 12).getname()
            except OSError:
                continue
            name = f"{family} {style}".strip() if style else family
            if name in seen:
                continue
            seen[name] = str(p)
            (own if d == FONT_DIR else other).append((name, str(p)))
    return tuple(own) + tuple(sorted(other, key=lambda x: x[0].lower()))


def refresh() -> None:
    list_fonts.cache_clear()


def default_font() -> str:
    fonts = dict(list_fonts())
    for name in PREFERRED:
        if name in fonts:
            return fonts[name]
    return next(iter(fonts.values()), "")


def name_of(path: str) -> str:
    for name, p in list_fonts():
        if p == path:
            return name
    return Path(path).stem if path else ""


@lru_cache(maxsize=256)
def load(path: str, size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    try:
        return ImageFont.truetype(path or default_font(), size)
    except OSError:
        return ImageFont.load_default(size)
