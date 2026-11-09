"""Сборка строк текста в облака без отдельной нейросети.

Идея:
1. OCR уже нашёл строки текста и их координаты.
2. Соседние строки похожей высоты, стоящие друг под другом (или рядом в одной строке),
   склеиваются в один блок — это и есть облако.
3. Если между двумя строками проходит контур облака (тёмная линия), их не склеиваем:
   это два разных облака, стоящих вплотную.
4. По фону вокруг блока определяем тип: обычное облако, плашка или текст поверх рисунка.
5. Блоки сортируются в порядке чтения, строки внутри склеиваются в одну фразу.
"""

from __future__ import annotations

import re
from statistics import median

import numpy as np

from .models import Box, Bubble, TextLine

_LETTER_RE = re.compile(r"[^\W\d_]", re.UNICODE)
# Иероглифы, кана, хангыль — между ними пробелы не нужны (для ja/zh)
_CJK = r"぀-ヿ㐀-䶿一-鿿豈-﫿ｦ-ﾟ"
_CJK_SPACE_RE = re.compile(rf"(?<=[{_CJK}])\s+(?=[{_CJK}])")


# ---------------------------------------------------------------- геометрия

def _x_overlap(a: Box, b: Box) -> float:
    return min(a.x2, b.x2) - max(a.x, b.x)


def _y_overlap(a: Box, b: Box) -> float:
    return min(a.y2, b.y2) - max(a.y, b.y)


def _same_tilt(a: TextLine, b: TextLine, limit: float = 12.0) -> bool:
    """Строки с заметно разным наклоном — это разные надписи, даже если стоят рядом.

    Наклон строки известен не всегда (тогда None) — в этом случае не мешаем склейке.
    """
    return a.angle is None or b.angle is None or abs(a.angle - b.angle) <= limit


def _height_ok(a: Box, b: Box, max_ratio: float = 1.9) -> bool:
    hi, lo = max(a.h, b.h), max(1.0, min(a.h, b.h))
    return hi / lo <= max_ratio


# ---------------------------------------------------------------- проверки по картинке

_GRAY = np.array([0.299, 0.587, 0.114])


def _gray(rgb: np.ndarray) -> np.ndarray:
    """Яркость куска картинки (uint8)."""
    return (rgb[..., :3].astype(np.float32) @ _GRAY).astype(np.uint8)


def _has_separator(image: np.ndarray | None, x1: float, x2: float, y1: float, y2: float,
                   axis: str) -> bool:
    """Есть ли в полосе между строками сплошная линия (контур облака).

    axis='rows'  — полоса горизонтальная, ищем тёмную/контрастную строку пикселей;
    axis='cols'  — полоса вертикальная, ищем такой же столбец.

    Яркость считается только для самой полосы: перевод в серое всей картинки целиком
    на длинной вебтун-ленте занимал секунды и больше гигабайта памяти.
    """
    if image is None:
        return False
    h, w = image.shape[:2]
    xa, xb = max(0, int(x1)), min(w, int(round(x2)))
    ya, yb = max(0, int(y1)), min(h, int(round(y2)))
    if xb - xa < 3 or yb - ya < 2:
        return False
    strip = _gray(image[ya:yb, xa:xb]).astype(np.int16)
    bg = int(np.median(strip))
    diff = np.abs(strip - bg) > 60
    frac = diff.mean(axis=1 if axis == "rows" else 0)
    return bool(frac.max() > 0.6)


def _ring_stats(img: np.ndarray, box: Box, pad: float) -> tuple[float, tuple[int, int, int]]:
    """Однородность фона вокруг блока и его цвет.

    Возвращает (доля пикселей, близких к медианному цвету; медианный цвет RGB).
    """
    h, w = img.shape[:2]
    x1, y1 = max(0, int(box.x - pad)), max(0, int(box.y - pad))
    x2, y2 = min(w, int(box.x2 + pad)), min(h, int(box.y2 + pad))
    ix1, iy1 = max(0, int(box.x)), max(0, int(box.y))
    ix2, iy2 = min(w, int(box.x2)), min(h, int(box.y2))
    region = img[y1:y2, x1:x2]
    if region.size == 0:
        return 0.0, (255, 255, 255)
    mask = np.ones(region.shape[:2], dtype=bool)
    mask[iy1 - y1:iy2 - y1, ix1 - x1:ix2 - x1] = False
    ring = region[mask]
    if len(ring) < 10:
        return 0.0, (255, 255, 255)
    color = np.median(ring, axis=0)
    close = np.all(np.abs(ring.astype(np.int16) - color.astype(np.int16)) <= 30, axis=1)
    return float(close.mean()), tuple(int(c) for c in color[:3])


# ---------------------------------------------------------------- текст

def _join_lines(lines: list[TextLine], joiner: str) -> str:
    """Склеивает строки облака в одну фразу в правильном порядке."""
    lines = sorted(lines, key=lambda l: l.box.cy)
    rows: list[list[TextLine]] = []
    for ln in lines:
        if rows and _y_overlap(rows[-1][-1].box, ln.box) > 0.5 * min(rows[-1][-1].box.h, ln.box.h):
            rows[-1].append(ln)
        else:
            rows.append([ln])

    parts: list[str] = []
    for row in rows:
        row.sort(key=lambda l: l.box.x)
        text = joiner.join(l.text.strip() for l in row if l.text.strip())
        if not text:
            continue
        # перенос слова через дефис: "some-" + "thing" -> "something"
        if parts and parts[-1].endswith("-") and text[:1].islower():
            parts[-1] = parts[-1][:-1] + text
        else:
            parts.append(text)

    out = joiner.join(parts)
    out = re.sub(r"\s+", " ", out).strip()
    if joiner == "":
        out = _CJK_SPACE_RE.sub("", out)
    return out


def _has_letters(text: str) -> bool:
    return bool(_LETTER_RE.search(text))


# ---------------------------------------------------------------- основной алгоритм

def group_lines(
    lines: list[TextLine],
    image: np.ndarray | None = None,
    *,
    joiner: str = " ",
    direction: str = "ltr",
    gap: float = 1.1,
    row_gap: float = 1.5,
    border_check: bool = True,
    skip_free_text: bool = False,
    skip_cut: bool = True,
) -> list[Bubble]:
    """Собирает строки OCR в облака.

    image — RGB-картинка (H, W, 3) uint8; без неё не работают проверка контура и тип фона.
    """
    lines = [l for l in lines if l.text.strip() and l.box.w > 1 and l.box.h > 1]
    if not lines:
        return []

    # для проверки контура облака; серое считается по кусочкам, только там, где нужно
    sep_image = image if border_check else None

    n = len(lines)
    parent = list(range(n))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def unite(i: int, j: int) -> None:
        ri, rj = find(i), find(j)
        if ri != rj:
            parent[rj] = ri

    for i in range(n):
        a = lines[i].box
        for j in range(i + 1, n):
            b = lines[j].box
            if not _height_ok(a, b) or not _same_tilt(lines[i], lines[j]):
                continue
            hmin = min(a.h, b.h)

            # 1) Слова/куски в одной строке
            if _y_overlap(a, b) > 0.5 * hmin:
                left, right = (a, b) if a.x <= b.x else (b, a)
                hgap = right.x - left.x2
                if hgap <= row_gap * hmin:
                    if hgap > 2 and _has_separator(sep_image, left.x2, right.x,
                                                   max(a.y, b.y), min(a.y2, b.y2), "cols"):
                        continue
                    unite(i, j)
                continue

            # 2) Строки друг под другом
            top, bot = (a, b) if a.y <= b.y else (b, a)
            vgap = bot.y - top.y2
            if vgap > gap * hmin:
                continue
            xo = _x_overlap(a, b)
            centered = abs(a.cx - b.cx) <= 0.5 * max(a.w, b.w)
            if xo <= 0 and not centered:
                continue
            if vgap > 2:
                sx1, sx2 = (max(a.x, b.x), min(a.x2, b.x2)) if xo > 0 else \
                    (min(a.cx, b.cx), max(a.cx, b.cx))
                if _has_separator(sep_image, sx1, sx2, top.y2, bot.y, "rows"):
                    continue
            unite(i, j)

    groups: dict[int, list[TextLine]] = {}
    for i in range(n):
        groups.setdefault(find(i), []).append(lines[i])

    img_h = image.shape[0] if image is not None else None
    img_w = image.shape[1] if image is not None else None

    bubbles: list[Bubble] = []
    for members in groups.values():
        box = members[0].box
        for m in members[1:]:
            box = box.union(m.box)
        text = _join_lines(members, joiner)
        bubble = Bubble(box=box, lines=members, text=text)

        if image is not None:
            line_h = median(m.box.h for m in members)
            uniform, color = _ring_stats(image, box, pad=max(3.0, 0.4 * line_h))
            bubble.bg_color = color
            if uniform >= 0.8:
                bubble.kind = "bubble" if sum(color) / 3 >= 170 else "box"
            else:
                bubble.kind = "free"

        if not _has_letters(text):
            bubble.skip_reason = "нет букв"
        elif skip_free_text and bubble.kind == "free":
            bubble.skip_reason = "текст поверх рисунка"
        elif skip_cut and img_h is not None and (box.y <= 2 or box.y2 >= img_h - 2
                                                   or box.x <= 1 or box.x2 >= img_w - 1):
            bubble.skip_reason = "обрезано краем области"
        bubbles.append(bubble)

    return sort_reading_order(bubbles, direction)


def sort_reading_order(bubbles: list[Bubble], direction: str = "ltr") -> list[Bubble]:
    """Сверху вниз; облака на одной высоте — слева направо (или справа налево для манги)."""
    items = sorted(bubbles, key=lambda b: b.box.y)
    rows: list[list[Bubble]] = []
    for b in items:
        if rows:
            last = rows[-1]
            ref = min(last, key=lambda r: r.box.h)
            if _y_overlap(ref.box, b.box) > 0.3 * min(ref.box.h, b.box.h):
                last.append(b)
                continue
        rows.append([b])
    out: list[Bubble] = []
    for row in rows:
        row.sort(key=lambda b: b.box.x, reverse=(direction == "rtl"))
        out.extend(row)
    return out
