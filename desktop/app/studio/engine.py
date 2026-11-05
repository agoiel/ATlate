"""Обработка страниц Студии: распознавание (в том числе длинных лент), перевод, клин, тайп."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image

from .. import languages
from ..bubbles import group_lines
from ..models import TextLine
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


def recognize_tiled(pipeline: Pipeline, arr: np.ndarray, lang: str, mode: str) -> list[TextLine]:
    """Распознать страницу; высокие ленты — кусками с перекрытием."""
    h = arr.shape[0]
    parallel = bool(pipeline.cfg.get("parallel_ocr", True))
    if h <= TILE + OVERLAP:
        return pipeline.recognize(Image.fromarray(arr), lang, mode, parallel)[0]
    passes: list[list[TextLine]] = []
    y = 0
    while y < h:
        y2 = min(h, y + TILE + OVERLAP)
        lines = pipeline.recognize(Image.fromarray(arr[y:y2]), lang, mode, parallel)[0]
        for ln in lines:
            ln.box.y += y
        passes.append(lines)
        if y2 >= h:
            break
        y += TILE
    # строки из зоны перекрытия встречаются дважды — объединение оставит одну
    return enhance.merge(passes)


def detect(pipeline: Pipeline, arr: np.ndarray) -> list[Block]:
    cfg = pipeline.cfg
    lang = cfg["source_lang"]
    info = languages.src(lang)
    lines = recognize_tiled(pipeline, arr, lang, cfg.get("ocr_mode", "thorough"))
    bubbles = group_lines(lines, arr, joiner=info["joiner"], direction=info["direction"],
                          gap=float(cfg["bubble_gap"]), row_gap=float(cfg["bubble_row_gap"]),
                          border_check=bool(cfg["bubble_border_check"]),
                          skip_free_text=False, skip_cut=False)
    blocks = []
    for b in bubbles:
        blk = Block.new(b.box, lines=[ln.box for ln in b.lines], source=b.text, kind=b.kind,
                        bg=tuple(b.bg_color), lang=languages.detect(b.text, lang))
        # «...», «?!» и прочее без букв не трогаем
        blk.enabled = b.translatable
        blocks.append(blk)
    return blocks


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


def render(project: Project, page: Page, original: np.ndarray | None = None) -> Rendered:
    arr = original if original is not None else load_image(project.image_path(page))
    clean = clean_page(arr, page.blocks)
    result, layouts = typeset_page(clean, page.blocks, project.style)
    return Rendered(arr, clean, result, layouts)


def process_page(pipeline: Pipeline, project: Project, page: Page, retranslate: bool = False) -> Rendered:
    """Полный цикл для страницы: найти текст (если ещё не искали), перевести, стереть, вписать."""
    arr = load_image(project.image_path(page))
    if page.status == "new" or not page.blocks:
        page.blocks = detect(pipeline, arr)
    page.error = translate(pipeline, page.blocks, force=retranslate)
    page.status = "error" if page.error else "done"
    return render(project, page, arr)


def export_page(project: Project, page: Page, rendered: Rendered | None = None) -> Path:
    r = rendered or render(project, page)
    out = project.out_path(page)
    out.parent.mkdir(parents=True, exist_ok=True)
    if out.suffix.lower() in (".jpg", ".jpeg"):
        r.result.save(out, quality=95, subsampling=0)
    else:
        r.result.save(out)
    return out
