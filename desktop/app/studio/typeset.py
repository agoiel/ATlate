"""Тайп: вписывание перевода в облако.

Текст вписывается не в прямоугольник, а в настоящую форму облака: для каждой строки
своя доступная ширина (в овальном облаке верхняя и нижняя строки короче средних).
Размер шрифта подбирается сам — самый крупный, при котором всё помещается.
"""

from __future__ import annotations

from dataclasses import dataclass
from statistics import median

import numpy as np
from PIL import Image, ImageDraw

from ..models import Box
from . import fonts
from .project import Block, Style


# ---------------------------------------------------------------- область для текста

@dataclass
class Shape:
    """Куда можно писать: для каждой строки картинки — отрезок [left, right]."""

    top: int
    bottom: int
    left: np.ndarray      # по строкам от top до bottom
    right: np.ndarray
    cx: float
    cy: float

    def span(self, y1: float, y2: float) -> tuple[float, float]:
        """Самый узкий отрезок на высоте от y1 до y2 (строка текста должна поместиться целиком)."""
        a = int(max(self.top, np.floor(y1))) - self.top
        b = int(min(self.bottom, np.ceil(y2))) - self.top
        if b <= a or a < 0 or b > len(self.left):
            return 0.0, 0.0
        lo, hi = float(self.left[a:b].max()), float(self.right[a:b].min())
        return lo, max(lo, hi)

    @staticmethod
    def rect(b: Box) -> "Shape":
        top, bottom = int(b.y), int(np.ceil(b.y2))
        n = max(1, bottom - top)
        return Shape(top, top + n, np.full(n, b.x), np.full(n, b.x2), b.cx, b.cy)


def _flood(ok: np.ndarray, sy: int, sx: int) -> np.ndarray:
    """Заливка связной области из точки (построчно, быстро)."""
    h, w = ok.shape
    filled = np.zeros_like(ok)
    stack = [(sy, sx)]
    while stack:
        y, x = stack.pop()
        if filled[y, x] or not ok[y, x]:
            continue
        row, done = ok[y], filled[y]
        left = x
        while left > 0 and row[left - 1] and not done[left - 1]:
            left -= 1
        right = x
        while right < w - 1 and row[right + 1] and not done[right + 1]:
            right += 1
        filled[y, left:right + 1] = True
        for ny in (y - 1, y + 1):
            if 0 <= ny < h:
                seg = ok[ny, left:right + 1] & ~filled[ny, left:right + 1]
                if seg.any():
                    # по одной точке на каждый кусок
                    starts = np.flatnonzero(seg & ~np.concatenate(([False], seg[:-1])))
                    stack.extend((ny, left + int(s)) for s in starts)
    return filled


def text_shape(clean: np.ndarray, block: Block) -> Shape:
    """Область для перевода: внутренность облака или плашки, иначе — рамка блока."""
    if block.area is not None:
        return Shape.rect(block.area)
    lines = block.lines or [block.box]
    lh = median(l.h for l in lines)
    fallback = Shape.rect(Box(block.box.x - 0.3 * lh, block.box.y - 0.2 * lh,
                              block.box.w + 0.6 * lh, block.box.h + 0.4 * lh))
    if block.kind not in ("bubble", "box"):
        return fallback

    h, w = clean.shape[:2]
    b = block.box
    grow = max(b.w, b.h) * 0.9 + 2 * lh
    x1, y1 = max(0, int(b.x - grow)), max(0, int(b.y - grow))
    x2, y2 = min(w, int(b.x2 + grow)), min(h, int(b.y2 + grow))
    region = clean[y1:y2, x1:x2, :3].astype(np.int16)
    ok = np.abs(region - np.array(block.bg[:3], dtype=np.int16)).max(axis=2) <= 40
    sy, sx = int(b.cy) - y1, int(b.cx) - x1
    if not ok[sy, sx]:
        ys, xs = np.nonzero(ok[int(b.y) - y1:int(b.y2) - y1, int(b.x) - x1:int(b.x2) - x1])
        if len(ys) == 0:
            return fallback
        i = int(np.argmin((ys + int(b.y) - y1 - sy) ** 2 + (xs + int(b.x) - x1 - sx) ** 2))
        sy, sx = int(ys[i] + int(b.y) - y1), int(xs[i] + int(b.x) - x1)
    filled = _flood(ok, sy, sx)
    rows = np.flatnonzero(filled.any(axis=1))
    cols = np.flatnonzero(filled.any(axis=0))
    if len(rows) < lh or len(cols) < lh:
        return fallback
    # заливка дошла до края окна поиска — облако без контура (белый фон страницы): берём рамку блока
    touches = (rows[0] == 0 and y1 > 0) + (rows[-1] == y2 - y1 - 1 and y2 < h) \
        + (cols[0] == 0 and x1 > 0) + (cols[-1] == x2 - x1 - 1 and x2 < w)
    if touches >= 2:
        return fallback
    top, bottom = int(rows[0]), int(rows[-1]) + 1
    bw = cols[-1] - cols[0] + 1
    bh = bottom - top
    mx = max(3.0, 0.07 * bw)
    my = max(2.0, 0.06 * bh)
    left = np.full(bottom - top, np.inf)
    right = np.full(bottom - top, -np.inf)
    for i, y in enumerate(range(top, bottom)):
        xs = np.flatnonzero(filled[y])
        if len(xs) == 0:
            continue
        # кусок строки, в котором середина облака
        mid = int(np.clip(sx, xs[0], xs[-1]))
        breaks = np.flatnonzero(np.diff(xs) > 1)
        starts = np.concatenate(([0], breaks + 1))
        ends = np.concatenate((breaks, [len(xs) - 1]))
        k = int(np.clip(np.searchsorted(xs[starts], mid, side="right") - 1, 0, len(starts) - 1))
        left[i] = xs[starts[k]] + mx
        right[i] = xs[ends[k]] - mx
    inset = int(my)
    left[:inset] = np.inf
    right[:inset] = -np.inf
    if inset:
        left[-inset:] = np.inf
        right[-inset:] = -np.inf
    return Shape(top + y1, bottom + y1, left + x1, right + x1,
                 float(cols[0] + cols[-1]) / 2 + x1, float(top + bottom) / 2 + y1)


# ---------------------------------------------------------------- раскладка текста

@dataclass
class Layout:
    size: int
    lines: list[tuple[str, float, float]]   # (текст, центр x, центр y)
    overflow: bool = False                  # не поместилось даже самым мелким шрифтом


def _split_long(word: str, font, width: float) -> list[str]:
    """Разбить слишком длинное слово переносами."""
    parts, cur = [], ""
    for ch in word:
        if cur and font.getlength(cur + ch + "-") > width:
            parts.append(cur + "-")
            cur = ch
        else:
            cur += ch
    parts.append(cur)
    return parts


def _try(words: list[str], shape: Shape, font, size: int, spacing: float, hyphenate: bool) -> Layout | None:
    lh = size * spacing
    space = font.getlength(" ")
    max_lines = int((shape.bottom - shape.top) / lh)
    for n in range(1, max_lines + 1):
        y0 = shape.cy - n * lh / 2
        if y0 < shape.top or y0 + n * lh > shape.bottom:
            break
        out: list[tuple[str, float, float]] = []
        queue = list(words)
        for i in range(n):
            lo, hi = shape.span(y0 + i * lh, y0 + (i + 1) * lh)
            width = hi - lo
            if width < size:
                break
            line, line_w = [], 0.0
            while queue:
                wd = queue[0]
                ww = font.getlength(wd)
                add = ww if not line else line_w + space + ww
                if add <= width:
                    line.append(queue.pop(0))
                    line_w = add
                elif not line and hyphenate and len(wd) > 3:
                    parts = _split_long(wd, font, width)
                    queue[0:1] = parts
                    if font.getlength(parts[0]) > width:
                        break
                else:
                    break
            if not line:
                break
            out.append((" ".join(line), (lo + hi) / 2, y0 + (i + 0.5) * lh))
        if not queue and out:
            # строк вышло меньше, чем пробовали, — сдвинуть к центру (в выпуклом облаке там шире)
            shift = (n - len(out)) * lh / 2
            return Layout(size, [(t, x, y + shift) for t, x, y in out])
    return None


def layout(text: str, shape: Shape, font_path: str, style: Style, fixed: int = 0,
           max_size: int | None = None) -> Layout:
    words = text.split()
    if not words:
        return Layout(style.min_size, [])
    sizes = [fixed] if fixed else list(range(max(style.min_size, max_size or style.max_size), style.min_size - 1, -1))
    for hyph in (False, True):
        for size in sizes:
            res = _try(words, shape, fonts.load(font_path, size), size, style.line_spacing, hyph)
            if res:
                return res
    # не помещается: самый мелкий шрифт, строки по ширине области, без учёта формы
    size = sizes[-1]
    font = fonts.load(font_path, size)
    width = max(size * 4, float(np.nanmax(np.where(np.isfinite(shape.right - shape.left),
                                                   shape.right - shape.left, 0))))
    lines, cur = [], ""
    for wd in words:
        if cur and font.getlength(cur + " " + wd) > width:
            lines.append(cur)
            cur = wd
        else:
            cur = f"{cur} {wd}".strip()
    lines.append(cur)
    lh = size * style.line_spacing
    y0 = shape.cy - len(lines) * lh / 2
    return Layout(size, [(t, shape.cx, y0 + (i + 0.5) * lh) for i, t in enumerate(lines)], overflow=True)


# ---------------------------------------------------------------- отрисовка

def _hex(c: str) -> tuple[int, int, int] | None:
    c = c.strip().lstrip("#")
    if len(c) != 6:
        return None
    try:
        return int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16)
    except ValueError:
        return None


def _colors(clean: np.ndarray, block: Block, shape: Shape) -> tuple[tuple, tuple | None]:
    """Цвет текста и обводки: тёмный текст на светлом фоне и наоборот."""
    if block.kind == "free":
        h, w = clean.shape[:2]
        b = block.box
        patch = clean[max(0, int(b.y)):min(h, int(b.y2)), max(0, int(b.x)):min(w, int(b.x2)), :3]
        lum = float(patch.mean()) if patch.size else 255.0
    else:
        lum = 0.299 * block.bg[0] + 0.587 * block.bg[1] + 0.114 * block.bg[2]
    dark, light = (20, 20, 20), (250, 250, 250)
    fill = _hex(block.color) or (dark if lum >= 140 else light)
    outline = block.outline == "on" or (block.outline == "auto" and block.kind == "free")
    stroke = (light if sum(fill) < 380 else dark) if outline else None
    return fill, stroke


def _size_for_height(font_path: str, line_h: float) -> int:
    """Размер шрифта, при котором строка перевода такой же высоты, как строка оригинала.

    У шрифтов разная «высота при том же размере»: рукописные мельче, поэтому меряем сам шрифт.
    """
    f = fonts.load(font_path, 100)
    x1, y1, x2, y2 = f.getbbox("ДАЁду")
    full = max(1, y2 - y1)
    return int(round(line_h * 100 / full * 1.05))


def typeset_page(clean: np.ndarray, blocks: list[Block], style: Style) -> tuple[Image.Image, dict[str, Layout]]:
    """Вписать перевод во все включённые блоки. Возвращает картинку и раскладку каждого блока."""
    img = Image.fromarray(clean[..., :3].copy())
    draw = ImageDraw.Draw(img)
    info: dict[str, Layout] = {}
    for b in blocks:
        text = (b.translation or "").strip()
        if not b.enabled or not text:
            continue
        if style.caps:
            text = text.upper()
        shape = text_shape(clean, b)
        font_path = b.font or style.font or fonts.default_font()
        # без ручного размера — не крупнее оригинала, чтобы короткие реплики не становились огромными
        lines = b.lines or [b.box]
        cap = _size_for_height(font_path, median(l.h for l in lines))
        lay = layout(text, shape, font_path, style, b.size, max(style.min_size, min(style.max_size, cap)))
        info[b.id] = lay
        fill, stroke = _colors(clean, b, shape)
        font = fonts.load(font_path, lay.size)
        sw = max(2, lay.size // 7) if stroke else 0
        for t, x, y in lay.lines:
            draw.text((x, y), t, font=font, fill=fill, anchor="mm",
                      stroke_width=sw, stroke_fill=stroke if stroke else None)
    return img, info
