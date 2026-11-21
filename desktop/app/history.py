"""История переводов (режим разработчика): оригинал и перевод каждой реплики, со временем,
тайтлом, языками и переводчиком.

Пишется только при включённом режиме разработчика — в файл history.jsonl в папке данных,
одна строка на переведённую страницу. Файл не растёт бесконечно: когда он больше MAX_BYTES,
старая половина записей отбрасывается.
"""

from __future__ import annotations

import csv
import io
import json
import time
from dataclasses import dataclass
from pathlib import Path

from .config import DATA_ROOT

PATH = DATA_ROOT / "history.jsonl"
MAX_BYTES = 2_000_000          # ~ несколько тысяч реплик


@dataclass
class Line:
    time: float
    title: str
    src: str
    tgt: str
    translator: str
    original: str
    translation: str


def add(result, cfg: dict, title: str = "", path: Path | None = None) -> int:
    """Записать переведённую страницу. Возвращает, сколько реплик записано."""
    path = path or PATH
    lines = [[b.text.strip(), b.translation.strip(), b.lang or cfg.get("source_lang", "")]
             for b in result.bubbles if b.translation and b.translation.strip()]
    if not lines:
        return 0
    rec = {"t": round(time.time()), "title": title, "tgt": cfg.get("target_lang", ""),
           "tor": cfg.get("translator", ""), "lines": lines}
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        if path.stat().st_size > MAX_BYTES:
            _trim(path)
    except OSError:
        return 0
    return len(lines)


def _trim(path: Path) -> None:
    rows = path.read_text(encoding="utf-8").splitlines()
    keep = rows[len(rows) // 2:]
    tmp = path.with_suffix(".tmp")
    tmp.write_text("\n".join(keep) + "\n", encoding="utf-8")
    tmp.replace(path)


def load(path: Path | None = None) -> list[Line]:
    """Все реплики, новые сверху."""
    path = path or PATH
    out: list[Line] = []
    try:
        rows = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return out
    for row in reversed(rows):
        try:
            rec = json.loads(row)
        except ValueError:
            continue                     # повреждённая строка (например, оборванная запись) — пропускаем
        for item in rec.get("lines", []):
            if not isinstance(item, list) or len(item) < 2:
                continue
            out.append(Line(float(rec.get("t", 0)), str(rec.get("title", "")), str(item[2] if len(item) > 2 else ""),
                            str(rec.get("tgt", "")), str(rec.get("tor", "")), str(item[0]), str(item[1])))
    return out


def search(lines: list[Line], query: str) -> list[Line]:
    q = query.strip().casefold()
    if not q:
        return lines
    return [x for x in lines if q in x.original.casefold() or q in x.translation.casefold() or q in x.title.casefold()]


def to_text(lines: list[Line]) -> str:
    """Для буфера обмена: оригинал и перевод парами."""
    return "\n\n".join(f"{x.original}\n{x.translation}" for x in lines)


def to_csv(lines: list[Line]) -> str:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["time", "title", "source_lang", "target_lang", "translator", "original", "translation"])
    for x in lines:
        w.writerow([time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(x.time)), x.title, x.src, x.tgt,
                    x.translator, x.original, x.translation])
    return buf.getvalue()


def clear(path: Path | None = None) -> None:
    path = path or PATH
    try:
        path.unlink()
    except FileNotFoundError:
        pass
