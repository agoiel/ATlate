"""EasyOCR — запасной движок. Работает везде, но тяжелее (нужен PyTorch, ~1–2 ГБ).

Установка: pip install easyocr
Первый запуск скачивает модели (~100 МБ на язык).
"""

from __future__ import annotations

import math

import numpy as np
from PIL import Image

from .. import languages
from ..models import Box, TextLine
from .base import OcrBackend, OcrError


class EasyOcrBackend(OcrBackend):
    name = "easyocr"

    def __init__(self, cfg):
        super().__init__(cfg)
        try:
            import easyocr  # noqa: F401
        except ImportError as e:
            raise OcrError("EasyOCR не установлен. Выполните: pip install easyocr") from e
        self._readers: dict[str, object] = {}

    def _reader(self, lang: str):
        if lang not in self._readers:
            import easyocr
            self._readers[lang] = easyocr.Reader(
                languages.src(lang)["easyocr"], gpu=bool(self.cfg.get("easyocr_gpu", False)))
        return self._readers[lang]

    def recognize(self, image: Image.Image, lang: str) -> list[TextLine]:
        k = float(self.cfg.get("ocr_upscale", 1.5))
        img = self.upscale(image.convert("RGB"), k)
        try:
            results = self._reader(lang).readtext(np.asarray(img), detail=1, paragraph=False)
        except Exception as e:  # noqa: BLE001
            raise OcrError(f"Ошибка EasyOCR: {e}") from e

        min_conf = float(self.cfg.get("ocr_min_conf", 0.3))
        out: list[TextLine] = []
        for pts, text, conf in results:
            if conf < min_conf:
                continue
            xs = [p[0] for p in pts]
            ys = [p[1] for p in pts]
            box = Box(min(xs), min(ys), max(xs) - min(xs), max(ys) - min(ys))
            # четыре угла рамки идут по часовой стрелке от левого верхнего: верхняя сторона — наклон строки
            angle = -math.degrees(math.atan2(pts[1][1] - pts[0][1], pts[1][0] - pts[0][0]))
            out.append(TextLine(box=box.scaled(1 / k), text=text, conf=float(conf),
                                angle=angle if abs(angle) <= 60 else None))
        return out
