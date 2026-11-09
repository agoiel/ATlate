"""Клин: стирание оригинального текста.

Два способа:
* заливка — буквы закрашиваются цветом фона облака (обычные облака и плашки);
* дорисовка — дыра на месте букв заполняется соседними пикселями, от краёв к центру
  (текст на однотонном или плавном фоне). Работает без нейросетей, только numpy.
"""

from __future__ import annotations

from statistics import median

import numpy as np

from ..models import Box
from .. import angle
from .project import Block


# ---------------------------------------------------------------- морфология без OpenCV

def dilate(mask: np.ndarray, r: int) -> np.ndarray:
    """Расширить маску на r пикселей (квадратом)."""
    if r <= 0:
        return mask
    h, w = mask.shape
    # по строкам, затем по столбцам — это и есть квадрат (2r+1)×(2r+1)
    tmp = mask.copy()
    for d in range(1, min(r, w - 1) + 1):
        tmp[:, d:] |= mask[:, :-d]
        tmp[:, :-d] |= mask[:, d:]
    out = tmp.copy()
    for d in range(1, min(r, h - 1) + 1):
        out[d:, :] |= tmp[:-d, :]
        out[:-d, :] |= tmp[d:, :]
    return out


# ---------------------------------------------------------------- маска букв

def _clip(b: Box, w: int, h: int) -> tuple[int, int, int, int]:
    return max(0, int(b.x)), max(0, int(b.y)), min(w, int(np.ceil(b.x2))), min(h, int(np.ceil(b.y2)))


def text_mask(arr: np.ndarray, block: Block) -> tuple[np.ndarray, tuple[int, int, int, int]]:
    """Маска пикселей оригинального текста блока.

    Возвращает маску для участка картинки и сам участок (x1, y1, x2, y2).
    """
    h, w = arr.shape[:2]
    lines = block.lines or [block.box]
    tilted = bool(block.src_angle)
    # у наклонённого текста прямые рамки строк в разы выше самих строк — берём настоящую высоту
    lh = block.source_line_h() if tilted else median(l.h for l in lines)
    pad = max(4.0, 0.6 * lh)
    x1, y1, x2, y2 = _clip(Box(block.box.x - pad, block.box.y - pad, block.box.w + 2 * pad, block.box.h + 2 * pad), w, h)
    region = arr[y1:y2, x1:x2, :3].astype(np.int16)
    mask = np.zeros(region.shape[:2], dtype=bool)
    if region.size == 0:
        return mask, (x1, y1, x2, y2)

    def strips(extra: float) -> np.ndarray:
        """Наклонённые полосы строк: прямая рамка наклонённой строки в несколько раз больше её самой
        и задевает рисунок или контур облака."""
        from PIL import Image, ImageDraw
        m = Image.new("L", (x2 - x1, y2 - y1), 0)
        d = ImageDraw.Draw(m)
        for ln in lines:
            pts = angle.corners(ln, block.src_angle, thick=lh, pad=extra)
            d.polygon([(px - x1, py - y1) for px, py in pts], fill=255)
        return np.asarray(m) > 0

    if block.kind == "free" and tilted:
        return strips(0.22 * lh), (x1, y1, x2, y2)

    if block.kind == "free":
        # пёстрый фон: отличить буквы от рисунка надёжно нельзя — стираем строку целиком
        for ln in lines:
            # с запасом: у звуков бывают толстая обводка и тени
            px, py = 0.22 * ln.h, 0.22 * ln.h
            a = _clip(Box(ln.x - px, ln.y - py, ln.w + 2 * px, ln.h + 2 * py), w, h)
            mask[a[1] - y1:a[3] - y1, a[0] - x1:a[2] - x1] = True
        return mask, (x1, y1, x2, y2)

    # однотонный фон: буквы — всё, что заметно отличается от цвета фона
    bg = np.array(block.bg[:3], dtype=np.int16)
    far = np.abs(region - bg).max(axis=2) > 48
    inner = np.zeros_like(mask)
    ring = np.zeros_like(mask)
    if tilted:
        inner, ring = strips(0.12 * lh), strips(0.37 * lh)
    for ln in ([] if tilted else lines):
        a = _clip(ln, w, h)
        inner[a[1] - y1:a[3] - y1, a[0] - x1:a[2] - x1] = True
        p = 0.25 * ln.h
        b = _clip(Box(ln.x - p, ln.y - p, ln.w + 2 * p, ln.h + 2 * p), w, h)
        ring[b[1] - y1:b[3] - y1, b[0] - x1:b[2] - x1] = True
    letters = far & inner
    # хвосты букв, вылезшие за рамку строки, берём только рядом с буквами — так не задеваем контур облака
    near = dilate(letters, max(2, int(0.2 * lh)))
    mask = letters | (far & ring & near)
    mask = dilate(mask, max(2, int(round(0.1 * lh))))
    return mask, (x1, y1, x2, y2)


# ---------------------------------------------------------------- дорисовка

def _resize(a: np.ndarray, w: int, h: int) -> np.ndarray:
    """Плавное увеличение (билинейное) для массива (H, W, 3)."""
    from PIL import Image
    return np.stack([np.asarray(Image.fromarray(a[..., c].astype(np.float32), "F").resize((w, h), Image.BILINEAR))
                     for c in range(a.shape[2])], axis=2)


def inpaint(region: np.ndarray, mask: np.ndarray) -> np.ndarray:
    """Заполнить дыру по маске цветом соседних пикселей.

    1) пирамида «сжать-разжать»: грубая заливка средним цветом краёв;
    2) диффузия: цвет плавно перетекает от краёв дыры внутрь, без ступенек.
    """
    img = region.astype(np.float32)
    known = (~mask).astype(np.float32)
    if known.sum() == 0:
        return region
    levels = [(img * known[..., None], known)]
    while True:
        im, wt = levels[-1]
        if (wt > 0).all() or min(wt.shape) <= 1:
            break
        hh, ww = wt.shape
        im = np.pad(im, ((0, hh % 2), (0, ww % 2), (0, 0)))
        wt = np.pad(wt, ((0, hh % 2), (0, ww % 2)))
        levels.append((im[0::2, 0::2] + im[1::2, 0::2] + im[0::2, 1::2] + im[1::2, 1::2],
                       wt[0::2, 0::2] + wt[1::2, 0::2] + wt[0::2, 1::2] + wt[1::2, 1::2]))
    im, wt = levels[-1]
    if (wt > 0).all():
        color = im / wt[..., None]
    else:
        color = np.broadcast_to(im.sum(axis=(0, 1)) / max(float(wt.sum()), 1e-6), im.shape).copy()
    for im, wt in reversed(levels[:-1]):
        up = _resize(color, wt.shape[1], wt.shape[0])
        own = im / np.maximum(wt, 1e-6)[..., None]
        a = np.clip(wt, 0, 1)[..., None]
        color = own * a + up * (1 - a)
    out = img.copy()
    out[mask] = color[mask]
    # диффузия: каждый пиксель дыры — среднее соседей, известные пиксели не меняются
    ys, xs = np.nonzero(mask)
    if len(ys):
        y1, y2 = max(0, ys.min() - 1), min(mask.shape[0], ys.max() + 2)
        x1, x2 = max(0, xs.min() - 1), min(mask.shape[1], xs.max() + 2)
        sub = out[y1:y2, x1:x2]
        m = mask[y1:y2, x1:x2]
        steps = int(min(120, 2 * max(y2 - y1, x2 - x1)))
        for _ in range(steps):
            p = np.pad(sub, ((1, 1), (1, 1), (0, 0)), mode="edge")
            avg = (p[:-2, 1:-1] + p[2:, 1:-1] + p[1:-1, :-2] + p[1:-1, 2:]) / 4
            sub[m] = avg[m]
        out[y1:y2, x1:x2] = sub
        # лёгкое зерно как у окружающего фона, чтобы заплатка не выглядела гладким пятном
        edge = dilate(m, 3) & ~m
        if edge.sum() > 20:
            ref = sub[edge]
            local = (np.pad(sub, ((1, 1), (1, 1), (0, 0)), mode="edge")[1:-1, 2:])[edge]
            # устойчивая оценка шума: контуры и края рисунка на неё почти не влияют
            grain = 1.4826 * float(np.median(np.abs(ref - local))) / 1.414
            if grain > 0.5:
                rng = np.random.default_rng(0)
                sub[m] += rng.normal(0, grain, (int(m.sum()), 1)).astype(np.float32)
                out[y1:y2, x1:x2] = sub
    return np.clip(out, 0, 255).astype(region.dtype)


# ---------------------------------------------------------------- страница целиком

def clean_page(arr: np.ndarray, blocks: list[Block]) -> np.ndarray:
    """Стереть оригинальный текст во всех включённых блоках. arr — RGB (H, W, 3) uint8."""
    out = arr[..., :3].copy()
    for b in blocks:
        if not b.enabled:
            continue
        mode = b.clean_mode()
        if mode == "none":
            continue
        mask, (x1, y1, x2, y2) = text_mask(out, b)
        if not mask.any():
            continue
        region = out[y1:y2, x1:x2]
        if mode == "fill":
            region[mask] = np.array(b.bg[:3], dtype=np.uint8)
        else:
            out[y1:y2, x1:x2] = inpaint(region, mask)
    return out
