"""Обработка страниц Студии: распознавание (в том числе длинных лент), перевод, клин, тайп."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image

from .. import angle, languages, sysload
from ..bubbles import group_lines
from ..models import Box, TextLine
from ..ocr import enhance
from ..pipeline import Pipeline
from .clean import clean_page
from .project import Block, Page, Project
from .typeset import Layout, typeset_page

# Длинная лента режется на куски такой высоты с перекрытием — иначе мелкий текст не прочитать
TILE = 2000
OVERLAP = 320


def load_image(path: Path) -> np.ndarray:
    with Image.open(path) as im:
        return np.asarray(im.convert("RGB"))


def _nothing() -> None:
    pass


def recognize_tiled(pipeline: Pipeline, arr: np.ndarray, lang: str, mode: str,
                    checkpoint=None) -> list[TextLine]:
    """Распознать страницу; высокие ленты — кусками с перекрытием.

    checkpoint — вызывается перед каждым куском и проходом: там работа встаёт на паузу
    или прерывается.
    """
    check = checkpoint or _nothing
    h = arr.shape[0]
    parallel = bool(pipeline.cfg.get("parallel_ocr", True))
    threads = sysload.ocr_threads(pipeline.cfg.get("studio_load", sysload.DEFAULT_LOAD))
    kw = {"max_threads": threads, "checkpoint": checkpoint}
    check()
    if h <= TILE + OVERLAP:
        return pipeline.recognize(Image.fromarray(arr), lang, mode, parallel, **kw)[0]
    passes: list[list[TextLine]] = []
    y = 0
    while y < h:
        check()
        y2 = min(h, y + TILE + OVERLAP)
        lines = pipeline.recognize(Image.fromarray(arr[y:y2]), lang, mode, parallel, **kw)[0]
        for ln in lines:
            ln.box.y += y
        passes.append(lines)
        if y2 >= h:
            break
        y += TILE
    # строки из зоны перекрытия встречаются дважды — объединение оставит одну
    return enhance.merge(passes)


def detect(pipeline: Pipeline, arr: np.ndarray, checkpoint=None) -> list[Block]:
    cfg = pipeline.cfg
    lang = cfg["source_lang"]
    info = languages.src(lang)
    lines = recognize_tiled(pipeline, arr, lang, cfg.get("ocr_mode", "thorough"), checkpoint)
    if checkpoint is not None:
        checkpoint()
    bubbles = group_lines(lines, arr, joiner=info["joiner"], direction=info["direction"],
                          gap=float(cfg["bubble_gap"]), row_gap=float(cfg["bubble_row_gap"]),
                          border_check=bool(cfg["bubble_border_check"]),
                          skip_free_text=False, skip_cut=False)
    blocks = []
    for b in bubbles:
        blk = Block.new(b.box, lines=[ln.box for ln in b.lines], source=b.text, kind=b.kind,
                        bg=tuple(b.bg_color), lang=languages.detect(b.text, lang))
        find_angle(arr, blk, b.lines, info["joiner"], lang)
        # «...», «?!» и прочее без букв не трогаем
        blk.enabled = b.translatable
        blocks.append(blk)
    return blocks


def find_angle(arr: np.ndarray, blk: Block, lines: list[TextLine], joiner: str, lang: str) -> None:
    """Наклон оригинала: перевод встанет под тем же углом. Заодно правит порядок наклонённых строк."""
    tilt = angle.find(arr, blk.box, blk.lines, blk.kind, blk.bg, [ln.angle for ln in lines])
    blk.angle = blk.src_angle = tilt.angle
    blk.line_h = tilt.line_h if tilt.angle else 0.0
    blk.kind, blk.bg = tilt.kind, tilt.bg
    if blk.angle and len(lines) > 1:
        text = angle.join(lines, blk.angle, blk.line_h, joiner)
        if text:
            blk.source = text
            blk.lang = languages.detect(text, lang)


class _Item:
    """Обёртка для переводчика конвейера: ему нужны поля text и translation."""

    def __init__(self, block: Block):
        self.block = block
        self.text = block.source
        self.translation = ""


def translate(pipeline: Pipeline, blocks: list[Block], force: bool = False) -> str:
    """Перевести блоки (с глоссарием и контекстом тайтла). Возвращает текст ошибки или пусто."""
    from ..translators.base import TranslateError

    pipeline._sync_title()
    lang = pipeline.cfg["source_lang"]
    todo: list[_Item] = []
    for b in blocks:
        if not b.enabled or not b.source.strip() or (b.translation and not force):
            continue
        hit = None if force else pipeline.cache.get(pipeline._cache_key(b.source))
        if hit is not None:
            b.translation = hit
        else:
            todo.append(_Item(b))
    if not todo:
        return ""
    groups: dict[str, list[_Item]] = {}
    for it in todo:
        groups.setdefault(it.block.lang or lang, []).append(it)
    error = ""
    try:
        for code, group in groups.items():
            pipeline._translate(group, code)
    except TranslateError as e:
        error = str(e)
    for it in todo:
        if it.translation:
            it.block.translation = it.translation
    pipeline._persist_title()
    return error


@dataclass
class Rendered:
    original: np.ndarray
    clean: np.ndarray
    result: Image.Image
    layouts: dict[str, Layout]


def clean_key(blocks: list[Block]) -> tuple:
    """Всё, от чего зависит клин страницы. Совпал ключ — стирать заново не нужно
    (например, поменяли только текст перевода или шрифт)."""
    def box(b: Box) -> tuple:
        return round(b.x, 1), round(b.y, 1), round(b.w, 1), round(b.h, 1)
    return tuple((b.id, b.enabled, b.clean_mode(), b.kind, tuple(b.bg), box(b.box), tuple(box(l) for l in b.lines),
                  b.src_angle, b.line_h)
                 for b in blocks)


def render(project: Project, page: Page, original: np.ndarray | None = None,
           clean: np.ndarray | None = None) -> Rendered:
    """Стереть оригинал и вписать перевод. clean — уже готовый клин этой страницы, если есть."""
    arr = original if original is not None else load_image(project.image_path(page))
    if clean is None:
        clean = clean_page(arr, page.blocks)
    result, layouts = typeset_page(clean, page.blocks, project.style)
    return Rendered(arr, clean, result, layouts)


def prepare_page(pipeline: Pipeline, project: Project, page: Page, retranslate: bool = False,
                 reset: bool = False, checkpoint=None) -> None:
    """Найти текст на странице (если ещё не искали) и перевести. Картинку не рисует.

    reset — забыть прежний перевод (ручные правки) и взять перевод заново.
    checkpoint — пауза и остановка: прерванная страница остаётся такой, какой была.
    """
    check = checkpoint or _nothing
    if page.status == "new" or not page.blocks:
        check()
        arr = load_image(project.image_path(page))
        blocks = detect(pipeline, arr, checkpoint)
        del arr
        check()
        page.blocks = blocks
    check()
    if reset:
        for b in page.blocks:
            b.translation = ""
    page.error = translate(pipeline, page.blocks, force=retranslate)
    page.status = "error" if page.error else "done"


def process_page(pipeline: Pipeline, project: Project, page: Page, retranslate: bool = False) -> Rendered:
    """Полный цикл для страницы: найти текст (если ещё не искали), перевести, стереть, вписать."""
    prepare_page(pipeline, project, page, retranslate)
    return render(project, page)


def export_page(project: Project, page: Page, rendered: Rendered | None = None) -> Path:
    r = rendered or render(project, page)
    out = project.out_path(page)
    out.parent.mkdir(parents=True, exist_ok=True)
    if out.suffix.lower() in (".jpg", ".jpeg"):
        r.result.save(out, quality=95, subsampling=0)
    else:
        r.result.save(out)
    return out
