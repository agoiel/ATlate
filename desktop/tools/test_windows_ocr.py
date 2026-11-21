"""Настоящее распознавание Windows: по очереди и параллельно должно дать одно и то же.

    python tools/test_windows_ocr.py out/

Рисует страницу с английским текстом (светлым и тёмным, на пёстром фоне), распознаёт её
в режиме «Максимальный» и сравнивает. Итог пишется в out/windows_ocr.txt.
"""

import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("ATLATE_UI_LANG", "ru")   # проверки написаны для русского интерфейса
os.environ.setdefault("ATLATE_NO_UPDATE_CHECK", "1")   # проверки не ходят в сеть за обновлениями

import numpy as np  # noqa: E402
from PIL import Image, ImageDraw, ImageFont  # noqa: E402

import app.pipeline as pipeline_mod  # noqa: E402
from app.pipeline import Pipeline  # noqa: E402

out = Path(sys.argv[1] if len(sys.argv) > 1 else "out")
out.mkdir(parents=True, exist_ok=True)


def page() -> Image.Image:
    rng = np.random.default_rng(3)
    bg = rng.integers(60, 200, (900, 700, 3), dtype=np.uint8)
    img = Image.fromarray(bg, "RGB").resize((700, 900))
    d = ImageDraw.Draw(img)
    try:
        font = ImageFont.truetype("arial.ttf", 30)
    except OSError:
        font = ImageFont.load_default()
    for i, (text, fill, box) in enumerate([
        ("Where are you going?", "black", "white"),
        ("I told you to wait here!", "black", "white"),
        ("THE NORTHERN GATE", "white", None),
        ("Run. Now.", "yellow", None),
        ("He is not coming back.", "white", (30, 30, 30)),
    ]):
        y = 60 + i * 160
        if box:
            d.rounded_rectangle((40, y - 20, 640, y + 60), 30, fill=box)
        d.text((70, y), text, font=font, fill=fill)
    return img


def main() -> int:
    cfg = {"source_lang": "en", "ocr_engine": "windows", "ocr_mode": "max", "ocr_upscale": 1.5}
    p = Pipeline(cfg)
    img = page()
    img.save(out / "windows_ocr_page.png")
    pipeline_mod.parallelism = lambda n, live=False: max(1, min(n, 3))  # раннер может иметь мало ядер
    report = []
    p.recognize(img, "en", "fast", False)  # прогрев
    ok = True
    for attempt in range(3):
        t = time.perf_counter()
        seq = p.recognize(img, "en", "max", False)
        seq_ms = (time.perf_counter() - t) * 1000
        t = time.perf_counter()
        par = p.recognize(img, "en", "max", True)
        par_ms = (time.perf_counter() - t) * 1000
        a = [(l.text, round(l.box.x), round(l.box.y)) for l in seq[0]]
        b = [(l.text, round(l.box.x), round(l.box.y)) for l in par[0]]
        same = a == b
        ok = ok and same
        report.append(f"attempt {attempt + 1}: sequential {seq_ms:.0f} ms, parallel x{par[3]} {par_ms:.0f} ms, "
                      f"lines {len(a)}/{len(b)}, same={same}")
    report.append("lines: " + " | ".join(t for t, _, _ in a))
    text = "\n".join(report)
    print(text)
    (out / "windows_ocr.txt").write_text(text, encoding="utf-8")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
