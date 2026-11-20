"""Тайп Студии на разных языках перевода: запасной шрифт, переносы, письмо справа налево.

    python tools/test_typeset_langs.py out/

* в шрифте по умолчанию нет тайских и арабских букв — должен подобраться запасной шрифт
  (иначе вместо букв квадраты);
* русские слова переносятся по словарю (пере-вод), а не по любой букве;
* арабский текст рисуется справа налево со связанными буквами, если в Pillow есть raqm.
  На Windows для этого нужен FriBiDi в папке libs (tools/get_fribidi.ps1): если он там лежит,
  raqm обязан включиться, а арабское слово — рисоваться иначе, чем простой вёрсткой.

Код возврата 1, если запасной шрифт не нашёлся там, где он должен быть (Windows), или FriBiDi
лежит, а сложная вёрстка не включилась.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import app  # noqa: E402 — раньше Pillow: подгружает FriBiDi, как при запуске программы
import numpy as np  # noqa: E402
from PIL import Image, ImageDraw, ImageFont, features  # noqa: E402

from app.models import Box  # noqa: E402
from app.studio import fonts  # noqa: E402
from app.studio.project import Block, Style  # noqa: E402
from app.studio.typeset import _split_long, typeset_page  # noqa: E402

out = Path(sys.argv[1] if len(sys.argv) > 1 else "out")
out.mkdir(parents=True, exist_ok=True)
failed = 0
print(f"шрифт по умолчанию: {fonts.name_of(fonts.default_font())} · raqm (сложная вёрстка): {features.check('raqm')}")

SAMPLES = [("ru", "Это экстраординарная международная организация охотников"),
           ("th", "ภาษาไทยเป็นภาษาที่สวยงามมาก ฉันชอบมัน"),
           ("ar", "أين تذهب؟ قلت لك أن تنتظر هنا!")]
images = []
for lang, text in SAMPLES:
    fp = fonts.font_for_text(fonts.default_font(), text)
    missing = sum(1 for c in set(text) if not c.isspace() and not fonts.has_glyph(fp, c))
    ok = missing == 0 or sys.platform != "win32"      # на Windows шрифты с этими буквами есть всегда
    failed += not ok
    print(f"{'OK  ' if ok else 'FAIL'} {lang}: шрифт {fonts.name_of(fp)!r}, нет букв: {missing}")
    im = Image.new("RGB", (300, 220), (200, 210, 230))
    ImageDraw.Draw(im).ellipse((10, 10, 290, 210), fill="white", outline="black", width=3)
    b = Block.new(Box(80, 80, 140, 60), lines=[Box(80, 80, 140, 26), Box(80, 112, 140, 26)],
                  kind="bubble", bg=(255, 255, 255), translation=text)
    res, lay = typeset_page(np.asarray(im), [b], Style(), lang)
    print(f"     строки: {[t for t, _, _ in lay[b.id].lines]}, размер {lay[b.id].size}")
    images.append(res)

raqm = features.check("raqm")
libs = Path(__file__).resolve().parents[1] / "libs"
if sys.platform == "win32" and any(libs.glob("*fribidi*.dll")):
    failed += not raqm
    print(f"{'OK  ' if raqm else 'FAIL'} FriBiDi из папки libs подхватился — сложная вёрстка включена: {raqm}"
          f" ({app.fribidi or 'не загружался'})")


def _shot(font, word: str) -> bytes:
    im = Image.new("L", (260, 90), 0)
    ImageDraw.Draw(im).text((10, 10), word, font=font, fill=255)
    return im.tobytes()


if raqm:
    for lang, word, strict in (("ar", "تذهب", True), ("th", "ที่สุด", False)):
        fp = fonts.font_for_text(fonts.default_font(), word)
        shaped = _shot(ImageFont.truetype(fp, 40), word) != _shot(
            ImageFont.truetype(fp, 40, layout_engine=ImageFont.Layout.BASIC), word)
        if strict:
            failed += not shaped
        print(f"{'OK  ' if shaped or not strict else 'FAIL'} {lang}: буквы сверстаны сложной вёрсткой "
              f"(отличается от простой): {shaped}")

f = fonts.load(fonts.default_font(), 28)
parts = _split_long("переводчица", f, f.getlength("переводчица") * 0.55, "ru")
ok = parts[0] in ("пере-", "пе-", "переводчи-", "перевод-")
failed += not ok
print(f"{'OK  ' if ok else 'FAIL'} перенос по словарю: {parts}")

sheet = Image.new("RGB", (310 * len(images), 220), "white")
for i, im in enumerate(images):
    sheet.paste(im, (i * 310, 0))
sheet.save(out / "typeset_langs.png")
print(f"failed: {failed}")
sys.exit(1 if failed else 0)
