"""Проверка внутри собранного exe: то, что кладётся в сборку отдельными файлами, нашлось.

Собирается PyInstaller с теми же параметрами для данных, что и программа (см. release.yml):

    python -m PyInstaller --noconfirm --onedir --name frozen_check --add-data "libs;libs" ^
        --collect-data pyphen tools/frozen_check.py
    dist/frozen_check/frozen_check.exe

* FriBiDi из libs подгружается (app/__init__.py) — сложная вёрстка Pillow включена;
* словари переносов Pyphen на месте.
Код возврата 1, если что-то не нашлось.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")    # собранный exe пишет в консоль в кодировке системы
if not getattr(sys, "frozen", False):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import app  # noqa: E402,F401 — подгружает FriBiDi до Pillow
import pyphen  # noqa: E402
from PIL import Image, ImageDraw, ImageFont  # noqa: E402 — как в Студии (features грузит модуль по имени,
                                             # и PyInstaller его без ImageFont в сборку не кладёт)


def _shaped(word: str, font_name: str) -> bool:
    """Слово рисуется иначе, чем простой вёрсткой, — значит, буквы соединены и стоят как надо."""
    def shot(font) -> bytes:
        im = Image.new("L", (260, 90), 0)
        ImageDraw.Draw(im).text((10, 10), word, font=font, fill=255)
        return im.tobytes()
    try:
        return shot(ImageFont.truetype(font_name, 40)) != shot(
            ImageFont.truetype(font_name, 40, layout_engine=ImageFont.Layout.BASIC))
    except OSError:
        return False


rows = [("собрано PyInstaller", bool(getattr(sys, "frozen", False))),
        ("FriBiDi подхватился — сложная вёрстка включена", bool(ImageFont.core.HAVE_RAQM)),
        ("арабский текст сверстан (Segoe UI)", _shaped("تذهب", "segoeui.ttf" if sys.platform == "win32"
                                                         else "DejaVuSans.ttf")),
        ("словари переносов на месте", "ru" in pyphen.LANGUAGES and "de" in pyphen.LANGUAGES)]
for name, ok in rows:
    print(f"{'OK  ' if ok else 'FAIL'} {name}")
print(f"FriBiDi: {app.fribidi or 'не загружался'}")
failed = sum(not ok for _, ok in rows)
print(f"failed: {failed}")
sys.exit(1 if failed else 0)
