"""ATlate — запуск.

    python main.py                         — обычный запуск с окном
    python main.py --image page.png        — перевести картинку без окна (для проверки)
    python main.py --image page.png --debug debug.png --no-translate
"""

from __future__ import annotations

import argparse
import sys


def run_cli(args) -> int:
    from PIL import Image, ImageDraw

    from app.config import Config
    from app.pipeline import Pipeline

    cfg = Config()
    image = Image.open(args.image).convert("RGB")
    result = Pipeline(cfg).process(image, translate=not args.no_translate)
    if result.error:
        print("ОШИБКА:", result.error)
    for i, b in enumerate(result.bubbles, 1):
        mark = f"[{b.kind}]"
        print(f"{i:2}. {mark:9} {b.text}")
        if b.translation:
            print(f"    → {b.translation}")
        elif b.skip_reason:
            print(f"    (пропущено: {b.skip_reason})")
    print("Время:", ", ".join(f"{k} {v:.2f} c" for k, v in result.timings.items()))

    if args.debug:
        dbg = image.copy()
        d = ImageDraw.Draw(dbg)
        colors = {"bubble": (0, 170, 0), "box": (0, 90, 255), "free": (230, 0, 180)}
        for ln in (l for b in result.bubbles for l in b.lines):
            d.rectangle((ln.box.x, ln.box.y, ln.box.x2, ln.box.y2), outline=(255, 160, 0), width=1)
        for i, b in enumerate(result.bubbles, 1):
            x = b.box
            d.rectangle((x.x - 3, x.y - 3, x.x2 + 3, x.y2 + 3), outline=colors.get(b.kind), width=3)
            d.text((x.x - 2, x.y - 16), str(i), fill=colors.get(b.kind))
        dbg.save(args.debug)
        print("Отладочная картинка:", args.debug)
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Переводчик манхвы с экрана")
    ap.add_argument("--image", help="перевести картинку из файла и вывести результат в консоль")
    ap.add_argument("--debug", help="сохранить картинку с найденными облаками")
    ap.add_argument("--no-translate", action="store_true", help="только распознать, без перевода")
    args = ap.parse_args()
    if args.image:
        return run_cli(args)
    from app.ui.main_window import run_gui
    return run_gui()


if __name__ == "__main__":
    sys.exit(main())
