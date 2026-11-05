"""Проект Студии: глава (папка с картинками), найденные блоки текста и их перевод.

Всё хранится рядом с главой, в папке agolate:
    agolate/project.json   — блоки, переводы и правки
    agolate/translated/    — готовые страницы после выгрузки
"""

from __future__ import annotations

import json
import re
import uuid
import zipfile
from dataclasses import asdict, dataclass, field
from pathlib import Path

from ..models import Box

IMAGE_EXT = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}
ARCHIVE_EXT = {".zip", ".cbz"}
PROJECT_DIR = "agolate"
VERSION = 1


def _natural(s: str):
    """Сортировка «как в проводнике»: 2.png раньше 10.png."""
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", s)]


def _box(d) -> Box | None:
    return Box(d["x"], d["y"], d["w"], d["h"]) if d else None


@dataclass
class Block:
    """Один блок текста на странице: облако, плашка или надпись поверх рисунка."""

    id: str
    box: Box                       # где стоит оригинальный текст (его и стираем)
    lines: list[Box] = field(default_factory=list)  # строки оригинала — по ним строится маска букв
    source: str = ""               # распознанный текст
    translation: str = ""
    kind: str = "bubble"           # bubble — светлое облако, box — плашка, free — поверх рисунка
    bg: tuple[int, int, int] = (255, 255, 255)
    lang: str = ""
    enabled: bool = True           # стирать оригинал и вписывать перевод
    clean: str = "auto"            # auto | fill (залить цветом фона) | inpaint (дорисовать) | none
    area: Box | None = None        # куда вписывать перевод (если задано вручную)
    size: int = 0                  # размер шрифта, 0 — подобрать
    color: str = ""                # цвет текста #RRGGBB, пусто — по фону
    outline: str = "auto"          # auto | on | off
    font: str = ""                 # путь к шрифту, пусто — шрифт проекта
    manual: bool = False           # блок добавлен вручную

    @staticmethod
    def new(box: Box, **kw) -> "Block":
        return Block(uuid.uuid4().hex[:8], box, **kw)

    def clean_mode(self) -> str:
        if self.clean != "auto":
            return self.clean
        return "inpaint" if self.kind == "free" else "fill"

    def to_dict(self) -> dict:
        d = asdict(self)
        d["box"] = self.box.to_dict()
        d["lines"] = [b.to_dict() for b in self.lines]
        d["area"] = self.area.to_dict() if self.area else None
        d["bg"] = list(self.bg)
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "Block":
        d = dict(d)
        d["box"] = _box(d["box"])
        d["lines"] = [_box(b) for b in d.get("lines", [])]
        d["area"] = _box(d.get("area"))
        d["bg"] = tuple(d.get("bg", (255, 255, 255)))
        known = cls.__dataclass_fields__
        return cls(**{k: v for k, v in d.items() if k in known})


@dataclass
class Page:
    file: str                      # имя файла картинки в папке главы
    blocks: list[Block] = field(default_factory=list)
    status: str = "new"            # new — не обработана, done — готова, error — ошибка
    error: str = ""

    def to_dict(self) -> dict:
        return {"file": self.file, "status": self.status, "error": self.error,
                "blocks": [b.to_dict() for b in self.blocks]}

    @classmethod
    def from_dict(cls, d: dict) -> "Page":
        return cls(d["file"], [Block.from_dict(b) for b in d.get("blocks", [])],
                   d.get("status", "new"), d.get("error", ""))


@dataclass
class Style:
    """Оформление перевода во всём проекте."""

    font: str = ""                 # путь к шрифту; пусто — шрифт по умолчанию
    min_size: int = 12
    max_size: int = 36
    caps: bool = False             # ЗАГЛАВНЫМИ, как принято в части переводов
    line_spacing: float = 1.1
    fmt: str = "png"               # png | jpg | same (как у оригинала)


@dataclass
class Project:
    root: Path
    pages: list[Page] = field(default_factory=list)
    style: Style = field(default_factory=Style)

    # ------------------------------------------------------------ пути
    @property
    def dir(self) -> Path:
        return self.root / PROJECT_DIR

    @property
    def file(self) -> Path:
        return self.dir / "project.json"

    @property
    def out_dir(self) -> Path:
        return self.dir / "translated"

    def image_path(self, page: Page) -> Path:
        return self.root / page.file

    @property
    def title(self) -> str:
        return self.root.name

    # ------------------------------------------------------------ открытие и сохранение
    @classmethod
    def open(cls, path: Path, files: list[Path] | None = None) -> "Project":
        """Открыть главу: папку, архив .zip/.cbz или выбранные картинки из одной папки."""
        path = Path(path)
        if path.is_file() and path.suffix.lower() in ARCHIVE_EXT:
            path = extract_archive(path)
        root = path if path.is_dir() else path.parent
        proj = cls(root)
        if proj.file.exists():
            proj._load()
        names = [p.name for p in (files or sorted(root.iterdir(), key=lambda p: _natural(p.name)))
                 if p.is_file() and p.suffix.lower() in IMAGE_EXT]
        if files:
            names.sort(key=_natural)
        known = {p.file: p for p in proj.pages}
        proj.pages = [known.get(n) or Page(n) for n in names] if names else proj.pages
        return proj

    def _load(self) -> None:
        try:
            d = json.loads(self.file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        self.pages = [Page.from_dict(p) for p in d.get("pages", [])]
        st = d.get("style", {})
        self.style = Style(**{k: v for k, v in st.items() if k in Style.__dataclass_fields__})

    def save(self) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        data = {"version": VERSION, "style": asdict(self.style), "pages": [p.to_dict() for p in self.pages]}
        tmp = self.file.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(self.file)

    def out_path(self, page: Page) -> Path:
        src = Path(page.file)
        ext = src.suffix.lower() if self.style.fmt == "same" else "." + self.style.fmt
        if ext == ".jpeg":
            ext = ".jpg"
        return self.out_dir / (src.stem + ext)


def extract_archive(archive: Path) -> Path:
    """Распаковать главу из .zip/.cbz в папку рядом с архивом (один раз)."""
    target = archive.with_suffix("")
    if target.exists() and any(target.iterdir()):
        return target
    target.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive) as z:
        for info in z.infolist():
            name = Path(info.filename).name
            if info.is_dir() or Path(name).suffix.lower() not in IMAGE_EXT:
                continue
            # картинки из вложенных папок складываем в одну, без путей из архива
            (target / name).write_bytes(z.read(info))
    return target
