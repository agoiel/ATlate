"""Несколько проходов распознавания для сложных страниц.

Движки OCR лучше всего читают тёмный текст на светлом ровном фоне. Чтобы находить
текст на тёмных и пёстрых фонах, светлый и цветной текст, каждая страница
распознаётся в нескольких вариантах, а результаты объединяются:

1. оригинал;
2. серая версия с растянутым контрастом и инверсией — светлый текст на тёмном
   фоне становится тёмным на светлом;
3. «локальный контраст» — из яркости вычитается размытый фон, остаются только
   резкие перепады, то есть буквы. Помогает на пёстрых фонах и с цветным текстом;
4. (максимальный режим) инверсия локального контраста;
5. (максимальный режим) «цветовой контраст» — отличие цвета от фона вокруг, для
   цветного текста на фоне похожей яркости.
"""

from __future__ import annotations

import re

import numpy as np
from PIL import Image, ImageFilter

from ..i18n import tr
from ..models import Box, TextLine

MODES = {
    "fast": tr("Быстрый — один проход"),
    "thorough": tr("Тщательный — 3 прохода (тёмные фоны, светлый текст)"),
    "max": tr("Максимальный — 5 проходов (пёстрые фоны, цветной текст)"),
}

_LETTER = re.compile(r"[^\W\d_]", re.UNICODE)


def _gray(img: Image.Image) -> np.ndarray:
    return np.asarray(img.convert("L"), dtype=np.float32)


def _stretch(g: np.ndarray) -> np.ndarray:
    lo, hi = np.percentile(g, 2), np.percentile(g, 98)
    if hi - lo < 1:
        return g
    return np.clip((g - lo) * 255.0 / (hi - lo), 0, 255)


def _local_contrast(img: Image.Image) -> np.ndarray:
    """Яркость минус размытый фон: остаются только резкие перепады (буквы)."""
    g = _gray(img)
    radius = int(np.clip(min(img.size) / 25, 12, 48))
    bg = np.asarray(img.convert("L").filter(ImageFilter.BoxBlur(radius)), dtype=np.float32)
    return np.clip(128 + (g - bg) * 3.0, 0, 255)


def _color_contrast(img: Image.Image) -> np.ndarray:
    """Насколько цвет пикселя отличается от цвета фона вокруг.

    Ловит цветной текст на фоне похожей яркости (красное на коричневом и т.п.),
    который в серой версии почти не виден. Буквы получаются тёмными на белом.
    """
    rgb = np.asarray(img.convert("RGB"), dtype=np.float32)
    radius = int(np.clip(min(img.size) / 25, 12, 48))
    bg = np.asarray(img.convert("RGB").filter(ImageFilter.BoxBlur(radius)), dtype=np.float32)
    dist = np.sqrt(((rgb - bg) ** 2).sum(axis=2))
    return np.clip(255 - dist * 2.5, 0, 255)


def _to_image(a: np.ndarray) -> Image.Image:
    return Image.fromarray(a.astype(np.uint8), "L").convert("RGB")


def variant_keys(mode: str) -> list[str]:
    """Какие варианты картинки распознаются в этом режиме. Первый всегда — оригинал."""
    if mode == "fast":
        return ["orig"]
    if mode == "max":
        return ["orig", "inv", "local", "local_inv", "color"]
    return ["orig", "inv", "local"]


class Source:
    """Исходная картинка и общие для вариантов промежуточные данные (считаются один раз).

    Варианты можно готовить из разных потоков: общее считается под замком.
    """

    def __init__(self, img: Image.Image):
        import threading
        self.img = img
        self._lock = threading.Lock()
        self._local: np.ndarray | None = None

    def local(self) -> np.ndarray:
        with self._lock:
            if self._local is None:
                self._local = _local_contrast(self.img)
            return self._local


def make(key: str, src: Source) -> Image.Image:
    """Подготовить вариант картинки."""
    img = src.img
    if key == "inv":
        return _to_image(255 - _stretch(_gray(img)))
    if key == "local":
        return _to_image(src.local())
    if key == "local_inv":
        return _to_image(255 - src.local())
    if key == "color":
        return _to_image(_color_contrast(img))
    return img


def variants(img: Image.Image, mode: str) -> list[Image.Image]:
    """Все варианты картинки сразу (по очереди)."""
    src = Source(img)
    return [make(k, src) for k in variant_keys(mode)]


# ---------------------------------------------------------------- объединение результатов

def _letters(t: str) -> int:
    return len(_LETTER.findall(t))


def _overlap(a: Box, b: Box) -> float:
    """Площадь пересечения относительно меньшего из прямоугольников."""
    w = min(a.x2, b.x2) - max(a.x, b.x)
    h = min(a.y2, b.y2) - max(a.y, b.y)
    if w <= 0 or h <= 0:
        return 0.0
    return (w * h) / max(1.0, min(a.w * a.h, b.w * b.h))


def merge(passes: list[list[TextLine]], stats: dict | None = None) -> list[TextLine]:
    """Объединяет строки из нескольких проходов без дублей.

    Первый проход (оригинал) считается самым надёжным. Строка из другого прохода
    добавляется, если на её месте ничего не нашли, и заменяет найденное, если в ней
    заметно больше букв (значит, первый проход прочитал строку не полностью).
    """
    result: list[TextLine] = list(passes[0]) if passes else []
    for lines in passes[1:]:
        for cand in lines:
            n = _letters(cand.text)
            text = cand.text.replace(" ", "")
            # мусор с узоров фона: меньше двух букв или в основном не буквы
            if n < 2 or n < 0.5 * len(text):
                continue
            hits = [i for i, ex in enumerate(result) if _overlap(ex.box, cand.box) > 0.4]
            if not hits:
                result.append(cand)
                if stats is not None:
                    stats["added"] = stats.get("added", 0) + 1
            elif len(hits) == 1 and n > 1.3 * _letters(result[hits[0]].text):
                result[hits[0]] = cand
                if stats is not None:
                    stats["replaced"] = stats.get("replaced", 0) + 1
    return result
