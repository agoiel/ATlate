"""Шрифты для тайпа: свои (папка fonts рядом с программой) и установленные в системе."""

from __future__ import annotations

import os
import sys
from functools import lru_cache
from pathlib import Path

from PIL import ImageFont

from ..config import DATA_ROOT, ROOT

FONT_DIR = DATA_ROOT / "fonts"            # сюда кладутся шрифты, добавленные пользователем
BUNDLED_FONT_DIR = ROOT / "fonts"         # шрифты, которые идут с программой
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
    out = [FONT_DIR] + ([BUNDLED_FONT_DIR] if BUNDLED_FONT_DIR != FONT_DIR else [])
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
            (own if d in (FONT_DIR, BUNDLED_FONT_DIR) else other).append((name, str(p)))
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


# ---------------------------------------------------------------- буквы, которых нет в шрифте

# Запасные шрифты (по названию семейства): если в выбранном шрифте нет нужных букв — тайских,
# арабских и т.п., — текст рисуется первым из них, где буквы есть. Иначе вместо букв были бы квадраты.
FALLBACK_FAMILIES = ["Segoe UI", "Leelawadee UI", "Leelawadee", "Tahoma", "Arial", "Noto Sans",
                     "Noto Sans Thai", "Noto Naskh Arabic", "Noto Sans Arabic", "DejaVu Sans", "Arial Unicode MS"]
_PUA = "\ue000"          # знак из «частной» области — его нет почти ни в одном шрифте: так выглядит «нет буквы»


def _render(path: str, ch: str) -> bytes:
    from PIL import Image, ImageDraw
    im = Image.new("L", (48, 48))
    ImageDraw.Draw(im).text((8, 8), ch, font=load(path, 28), fill=255)
    return im.tobytes()


@lru_cache(maxsize=8192)
def has_glyph(path: str, ch: str) -> bool:
    """Есть ли буква в шрифте: отсутствующая рисуется так же, как заведомо отсутствующий знак."""
    if ch.isspace():
        return True
    try:
        return _render(path, ch) != _render(path, _PUA)
    except Exception:  # noqa: BLE001
        return True


@lru_cache(maxsize=512)
def _covering(path: str, chars: str) -> str:
    def missing(p: str) -> int:
        return sum(1 for c in chars if not has_glyph(p, c))
    if missing(path) == 0:
        return path
    names = list_fonts()
    best, best_missing = path, missing(path)
    for family in FALLBACK_FAMILIES:
        found = [p for name, p in names if name.split(" Bold")[0].split(" Regular")[0] == family]
        # жирное начертание, если есть: перевод в облаках обычно жирный
        found.sort(key=lambda p: 0 if "bold" in name_of(p).lower() else 1)
        for p in found[:1]:
            m = missing(p)
            if m == 0:
                return p
            if m < best_missing:
                best, best_missing = p, m
    return best


def font_for_text(path: str, text: str) -> str:
    """Шрифт, в котором есть все буквы текста: выбранный или запасной."""
    chars = "".join(sorted({c for c in text if not c.isspace()}))
    return _covering(path or default_font(), chars) if chars else path
