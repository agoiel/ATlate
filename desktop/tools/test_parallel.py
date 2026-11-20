"""Проверка: параллельное распознавание даёт тот же результат, что и по очереди.

    python tools/test_parallel.py

Вместо настоящего OCR — поддельный: «находит» строки, зависящие от содержимого
картинки, и отвечает со случайной задержкой, чтобы проходы заканчивались вразнобой.
"""

import random
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("ATLATE_UI_LANG", "ru")   # проверки написаны для русского интерфейса

import numpy as np  # noqa: E402
from PIL import Image  # noqa: E402

from app.models import Box, TextLine  # noqa: E402
from app.ocr.base import OcrBackend  # noqa: E402
import app.pipeline as pipeline_mod  # noqa: E402
from app.pipeline import Pipeline  # noqa: E402

# на машине с парой ядер проходы иначе шли бы по очереди — проверяем именно параллельный путь
pipeline_mod.parallelism = lambda n, live=False: max(1, min(n, 3))


class FakeOcr(OcrBackend):
    name = "fake"
    supports_parallel = True

    def recognize(self, image, lang):
        a = np.asarray(image.convert("L"), dtype=np.int64)
        seed = int(a.sum() % 100_003)
        rnd = random.Random(seed)
        time.sleep(0.05 + random.random() * 0.15)  # случайная задержка — проходы финишируют вразнобой
        out = []
        for _ in range(rnd.randint(3, 9)):
            x, y = rnd.randint(0, 300), rnd.randint(0, 500)
            text = "".join(rnd.choice("가나다라마바사abcdef") for _ in range(rnd.randint(2, 12)))
            out.append(TextLine(Box(x, y, rnd.randint(20, 120), rnd.randint(10, 30)), text))
        return out


def main() -> int:
    cfg = {"source_lang": "ko", "ocr_mode": "max", "ocr_engine": "fake"}
    p = Pipeline(cfg)
    p._ocr = FakeOcr(cfg)
    p._ocr_name = "fake"
    rng = np.random.default_rng(1)
    bad = 0
    for i in range(12):
        img = Image.fromarray(rng.integers(0, 255, (400, 320, 3), dtype=np.uint8), "RGB")
        for mode in ("fast", "thorough", "max"):
            seq = p.recognize(img, "ko", mode, parallel=False)
            par = p.recognize(img, "ko", mode, parallel=True)
            same = [(l.text, l.box) for l in seq[0]] == [(l.text, l.box) for l in par[0]]
            same_passes = [ps.variant for ps in seq[1]] == [ps.variant for ps in par[1]]
            if not (same and same_passes and seq[2] == par[2]):
                bad += 1
                print("MISMATCH", i, mode)
    t = time.perf_counter()
    p.recognize(img, "ko", "max", parallel=False)
    seq_t = time.perf_counter() - t
    t = time.perf_counter()
    _, _, _, threads = p.recognize(img, "ko", "max", parallel=True)
    par_t = time.perf_counter() - t
    print(f"36 comparisons, mismatches: {bad}; max mode: sequential {seq_t*1000:.0f} ms, "
          f"parallel x{threads} {par_t*1000:.0f} ms")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
