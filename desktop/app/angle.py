"""Наклон текста: под каким углом стоит оригинал, чтобы перевод встал так же.

Распознавание отдаёт только прямые рамки строк, поэтому угол ищется по самим буквам:
пиксели букв «проецируются» на ось поперёк строки под разными углами. Когда угол угадан,
строки ложатся в узкие плотные полосы — такой угол и есть наклон текста.

Угол — в градусах, плюс — правый край строки выше левого (против часовой стрелки).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from statistics import median

import numpy as np

from .models import Box, TextLine

MAX_ANGLE = 45.0        # круче — это уже вертикальный текст, его не трогаем
MIN_ANGLE = 3.0         # меньше — считаем текст ровным (чтобы ровные реплики не «плясали»)
_SAMPLE = 6000


# ---------------------------------------------------------------- геометрия

def to_frame(x: float, y: float, deg: float) -> tuple[float, float]:
    """Точка (от центра) → в систему, где наклонённая строка горизонтальна."""
    a = math.radians(deg)
    c, s = math.cos(a), math.sin(a)
    return x * c - y * s, x * s + y * c


def from_frame(x: float, y: float, deg: float) -> tuple[float, float]:
    return to_frame(x, y, -deg)


def oriented_size(w: float, h: float, deg: float) -> tuple[float, float]:
    """Длина и толщина наклонённого прямоугольника по его прямой рамке (w, h).

    Рамка строки под углом шире и выше самой строки: w = L·cos + T·sin, h = L·sin + T·cos.
    """
    a = math.radians(abs(deg))
    c, s = math.cos(a), math.sin(a)
    d = c * c - s * s
    if abs(deg) < 0.5:
        return w, h
    if d < 0.15:
        # около 45° по рамке ничего не понять: считаем строку диагональю рамки
        return math.hypot(w, h) * 0.9, max(2.0, min(w, h) * 0.35)
    length = (w * c - h * s) / d
    thick = (h * c - w * s) / d
    length = float(np.clip(length, 0.4 * max(w, h), math.hypot(w, h)))
    thick = float(np.clip(thick, 0.12 * min(w, h), min(w, h)))
    return length, thick


def line_height(lines: list[Box], deg: float) -> float:
    """Настоящая высота строки наклонённого текста по рамкам строк."""
    if not lines:
        return 0.0
    return float(median(oriented_size(l.w, l.h, deg)[1] for l in lines))


def corners(box: Box, deg: float, length: float | None = None, thick: float | None = None,
            pad: float = 0.0) -> list[tuple[float, float]]:
    """Углы наклонённого прямоугольника с центром в центре рамки."""
    L, T = oriented_size(box.w, box.h, deg)
    if thick is not None and length is None and abs(deg) >= 0.5:
        # толщина известна — длина по рамке считается надёжнее (и с запасом: лучше стереть лишнее,
        # чем оставить край буквы)
        a = math.radians(abs(deg))
        c, s = math.cos(a), math.sin(a)
        L = (box.w - thick * s) / c
        if s > 0.2:
            L = max(L, (box.h - thick * c) / s)
        L = float(np.clip(L, 0.4 * max(box.w, box.h), math.hypot(box.w, box.h)))
    L = (length if length is not None else L) + 2 * pad
    T = (thick if thick is not None else T) + 2 * pad
    out = []
    for dx, dy in ((-L / 2, -T / 2), (L / 2, -T / 2), (L / 2, T / 2), (-L / 2, T / 2)):
        x, y = from_frame(dx, dy, deg)
        out.append((box.cx + x, box.cy + y))
    return out


# ---------------------------------------------------------------- пиксели букв

def _ink(arr: np.ndarray, box: Box, lines: list[Box], kind: str, bg) -> tuple[np.ndarray, np.ndarray] | None:
    """Координаты пикселей букв блока (x, y) относительно центра блока."""
    h, w = arr.shape[:2]
    x1, y1 = max(0, int(box.x)), max(0, int(box.y))
    x2, y2 = min(w, int(math.ceil(box.x2))), min(h, int(math.ceil(box.y2)))
    if x2 - x1 < 6 or y2 - y1 < 6:
        return None
    region = arr[y1:y2, x1:x2, :3].astype(np.int16)
    inner = np.zeros(region.shape[:2], dtype=bool)
    for ln in lines or [box]:
        inner[max(0, int(ln.y) - y1):max(0, int(math.ceil(ln.y2)) - y1),
              max(0, int(ln.x) - x1):max(0, int(math.ceil(ln.x2)) - x1)] = True
    if kind in ("bubble", "box"):
        ink = (np.abs(region - np.array(bg[:3], dtype=np.int16)).max(axis=2) > 48) & inner
    else:
        # пёстрый фон: цвет букв заранее не известен, зато у букв резкие края —
        # берём пиксели, где яркость резко меняется (на плавном рисунке таких мало)
        gray = (region @ np.array([0.299, 0.587, 0.114])).astype(np.float32)
        edge = np.zeros(gray.shape, dtype=np.float32)
        edge[:, 1:] += np.abs(np.diff(gray, axis=1))
        edge[1:, :] += np.abs(np.diff(gray, axis=0))
        ink = (edge > 48) & inner
    n = int(ink.sum())
    if n < 60 or n > 0.7 * inner.sum():
        return None
    ys, xs = np.nonzero(ink)
    if n > _SAMPLE:
        pick = np.random.default_rng(0).choice(n, _SAMPLE, replace=False)
        ys, xs = ys[pick], xs[pick]
    return xs + x1 - box.cx, ys + y1 - box.cy


def _profile(xs: np.ndarray, ys: np.ndarray, deg: float) -> np.ndarray:
    """Сколько пикселей букв на каждой высоте, если смотреть вдоль строки под углом deg."""
    a = math.radians(deg)
    t = xs * math.sin(a) + ys * math.cos(a)
    return np.bincount((t - t.min()).astype(np.int32))


def _score(xs: np.ndarray, ys: np.ndarray, deg: float) -> float:
    p = _profile(xs, ys, deg).astype(np.float64)
    return float((p * p).sum())


def _measure_line(xs: np.ndarray, ys: np.ndarray, deg: float) -> float:
    """Высота строки под найденным углом: толщина полос, в которые легли буквы."""
    p = _profile(xs, ys, deg)
    on = p > max(1.0, 0.03 * float(p.max()))
    edges = np.flatnonzero(np.diff(np.concatenate(([False], on, [False])).astype(np.int8)))
    runs = [int(b - a) for a, b in zip(edges[0::2], edges[1::2]) if b - a >= 3]
    return float(median(runs)) if runs else 0.0


# ---------------------------------------------------------------- угол блока

def detect(arr: np.ndarray, box: Box, lines: list[Box], kind: str, bg,
           hints: list[float] | None = None) -> tuple[float, float]:
    """Наклон текста блока и настоящая высота его строки: (угол, высота) или (0, 0), если текст ровный.

    hints — углы строк от распознавания (по расположению слов), если оно их дало:
    запасной способ, когда по картинке понять не удалось.
    """
    ink = _ink(arr, box, lines, kind, bg)
    if ink is not None:
        xs, ys = ink
        flat = _score(xs, ys, 0.0)
        coarse = np.arange(-MAX_ANGLE, MAX_ANGLE + 0.1, 2.0)
        best = max(coarse, key=lambda d: _score(xs, ys, float(d)))
        fine = np.arange(best - 2.0, best + 2.01, 0.5)
        best = float(max(fine, key=lambda d: _score(xs, ys, float(d))))
        top = _score(xs, ys, best)
        # наклон признаём, только если под этим углом строки заметно плотнее, чем «ровно»
        if abs(best) >= MIN_ANGLE and abs(best) <= MAX_ANGLE and top >= 1.25 * flat:
            deg = float(round(best))
            lh = _measure_line(xs, ys, best)
            by_box = line_height(lines or [box], deg)
            if lh <= 0 or (by_box > 0 and lh > 1.8 * by_box):
                lh = by_box       # строки слиплись в одну полосу — берём оценку по рамкам
            return deg, round(lh, 1)
        if abs(best) < MIN_ANGLE:
            hints = None          # по картинке текст ровный — подсказкам распознавания не верим
    good = [a for a in (hints or []) if a is not None]
    if good:
        deg = float(median(good))
        strong = [a for a in good if abs(a) >= 6.0]
        if abs(deg) >= 6.0 and abs(deg) <= MAX_ANGLE and len(strong) * 2 > len(good):
            deg = float(round(deg))
            return deg, round(line_height(lines or [box], deg), 1)
    return 0.0, 0.0


def ring_stats(arr: np.ndarray, box: Box, deg: float, pad: float) -> tuple[float, tuple[int, int, int]]:
    """Однородность и цвет фона вокруг наклонённого текста (как bubbles._ring_stats, но по наклонённой рамке).

    Прямая рамка наклонённой реплики вылезает углами за облако, и по ней облако выглядит
    «пёстрым фоном». Наклонённая рамка остаётся внутри облака.
    """
    from PIL import Image, ImageDraw

    h, w = arr.shape[:2]
    outer = corners(box, deg, pad=pad)
    x1 = max(0, int(min(p[0] for p in outer)))
    y1 = max(0, int(min(p[1] for p in outer)))
    x2 = min(w, int(math.ceil(max(p[0] for p in outer))))
    y2 = min(h, int(math.ceil(max(p[1] for p in outer))))
    if x2 - x1 < 4 or y2 - y1 < 4:
        return 0.0, (255, 255, 255)
    m = Image.new("L", (x2 - x1, y2 - y1), 0)
    d = ImageDraw.Draw(m)
    d.polygon([(px - x1, py - y1) for px, py in outer], fill=255)
    d.polygon([(px - x1, py - y1) for px, py in corners(box, deg)], fill=0)
    ring = arr[y1:y2, x1:x2, :3][np.asarray(m) > 0]
    if len(ring) < 10:
        return 0.0, (255, 255, 255)
    color = np.median(ring, axis=0)
    close = np.all(np.abs(ring.astype(np.int16) - color.astype(np.int16)) <= 30, axis=1)
    return float(close.mean()), tuple(int(c) for c in color[:3])


def adopt(arr: np.ndarray, box: Box, lines: list[Box], kind: str, bg, deg: float,
          src_deg: float) -> tuple[float, float] | None:
    """Угол перевода поменяли вручную: подходит ли он и самому оригиналу?

    Если под новым углом буквы оригинала ложатся в полосы плотнее, чем под прежним, значит,
    наклон оригинала не определился или определился неточно — тогда возвращаются новый угол
    оригинала и высота его строки. Иначе None: оригинал стоит как стоял, наклонён только перевод.
    """
    ink = _ink(arr, box, lines, kind, bg)
    if ink is None or abs(deg) > MAX_ANGLE:
        return None
    xs, ys = ink
    if _score(xs, ys, deg) < 1.1 * _score(xs, ys, src_deg):
        return None
    lh = _measure_line(xs, ys, deg) if abs(deg) >= 0.5 else 0.0
    return float(deg), round(lh, 1)


@dataclass
class Tilt:
    """Наклон блока текста и его настоящие размеры (у ровного текста angle = 0)."""

    angle: float = 0.0
    line_h: float = 0.0            # высота строки
    length: float = 0.0            # размеры всего текста: вдоль строк и поперёк
    thick: float = 0.0
    cx: float = 0.0                # центр текста на картинке
    cy: float = 0.0
    kind: str = ""                 # тип фона, уточнённый по наклонённой рамке
    bg: tuple = (255, 255, 255)


def extent(lines: list[Box], deg: float, lh: float) -> tuple[float, float, float, float]:
    """Размеры и центр наклонённого блока по его строкам: (длина, толщина, cx, cy).

    Каждая строка — наклонённая полоса высотой lh с центром в центре своей прямой рамки.
    """
    us, ts = [], []
    for ln in lines:
        pts = corners(ln, deg, thick=lh)
        for x, y in pts:
            u, t = to_frame(x, y, deg)
            us.append(u)
            ts.append(t)
    cu, ct = (min(us) + max(us)) / 2, (min(ts) + max(ts)) / 2
    cx, cy = from_frame(cu, ct, deg)
    return max(us) - min(us), max(ts) - min(ts), cx, cy


def find(arr: np.ndarray, box: Box, lines: list[Box], kind: str, bg,
         hints: list[float] | None = None) -> Tilt:
    """Наклон блока со всем, что из него следует: размеры текста и уточнённый тип фона."""
    deg, lh = detect(arr, box, lines, kind, bg, hints)
    out = Tilt(kind=kind, bg=tuple(bg))
    if not deg:
        return out
    if kind == "free":
        # прямая рамка наклонённой реплики вылезает за облако, и оно кажется «рисунком» —
        # проверяем фон ещё раз, по наклонённой рамке
        uniform, color = ring_stats(arr, box, deg, max(3.0, 0.4 * (lh or line_height(lines, deg))))
        if uniform >= 0.8:
            out.kind = "bubble" if sum(color) / 3 >= 170 else "box"
            out.bg = color
            again = detect(arr, box, lines, out.kind, out.bg, hints)
            if again[0]:
                deg, lh = again
    out.angle = deg
    out.line_h = lh or line_height(lines or [box], deg)
    out.length, out.thick, out.cx, out.cy = extent(lines or [box], deg, out.line_h)
    return out


def words_angle(rects: list[tuple[float, float, float, float]]) -> float | None:
    """Наклон строки по рамкам её слов (x, y, w, h): центры слов лежат вдоль строки.

    None — слов мало или они стоят слишком близко, чтобы судить.
    """
    if len(rects) < 2:
        return None
    cx = np.array([r[0] + r[2] / 2 for r in rects], dtype=np.float64)
    cy = np.array([r[1] + r[3] / 2 for r in rects], dtype=np.float64)
    hh = float(median(r[3] for r in rects))
    span = float(cx.max() - cx.min())
    if span < 2.5 * hh:
        return None
    slope = float(np.polyfit(cx, cy, 1)[0])
    deg = -math.degrees(math.atan(slope))
    # два слова — ненадёжно: центр слова гуляет из-за хвостов букв
    if len(rects) == 2 and abs(deg) < 10.0:
        return 0.0
    return deg


# ---------------------------------------------------------------- порядок строк

def join(lines: list[TextLine], deg: float, lh: float, joiner: str) -> str:
    """Склеить строки наклонённого блока в правильном порядке.

    Прямые рамки наклонённых строк налезают друг на друга, и обычная сборка путает,
    какая строка выше. Поэтому строки сначала «выпрямляются».
    """
    from .bubbles import _join_lines

    lh = lh or line_height([l.box for l in lines], deg)
    flat = []
    for ln in lines:
        x, y = to_frame(ln.box.cx, ln.box.cy, deg)
        length = oriented_size(ln.box.w, ln.box.h, deg)[0]
        flat.append(TextLine(Box(x - length / 2, y - lh / 2, length, lh), ln.text, ln.conf))
    return _join_lines(flat, joiner)
