"""Словарик: слова оригинала, которые игрок отметил на экране, с переводом и своей заметкой.

Слово попадает сюда из «Слов на экране»: навели мышь на слово оригинала (на Android — удержали
палец) и нажали «В словарик». Перевод слова берётся из глоссария тайтла, если слово там есть, иначе
его переводит выбранный переводчик — ИИ-переводчику заодно показывается фраза, где слово
встретилось. Заметку (транскрипцию, род, что угодно) игрок пишет сам в окне словарика.

Файл vocabulary.json в папке данных; формат тот же, что в Android-версии:
{"entries": [{"word": "hunter", "translation": "охотник", "note": "", "lang": "en", "tgt": "ru",
              "context": "...", "context_tr": "...", "title": "...", "added": 1763700000}]}
"""

from __future__ import annotations

import csv
import io
import json
import re
import threading
import time
import unicodedata
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .config import DATA_ROOT
from .glossary import _JOSA, Glossary

PATH = DATA_ROOT / "vocabulary.json"
_LOCK = threading.Lock()
_HANGUL = re.compile(r"[가-힯]")
_CASED = re.compile(r"[A-Za-zÀ-ɏЀ-ӿ]")   # латиница и кириллица: у них есть регистр


@dataclass
class Entry:
    word: str
    translation: str = ""
    note: str = ""                  # заметка игрока: транскрипция, род, пример — что угодно
    lang: str = ""                  # язык слова (ko, en, ja …)
    tgt: str = ""                   # язык перевода
    context: str = ""               # фраза, где слово встретилось
    context_tr: str = ""            # её перевод
    title: str = ""                 # тайтл
    added: int = field(default_factory=lambda: int(time.time()))

    @property
    def key(self) -> tuple[str, str]:
        return (self.lang or "").split("-")[0], self.word.casefold()


def clean(word: str) -> str:
    """Слово без знаков препинания и кавычек по краям: «Hunter!» → Hunter."""
    w = word.strip()
    while w and unicodedata.category(w[0])[0] in "PS":
        w = w[1:]
    while w and unicodedata.category(w[-1])[0] in "PS":
        w = w[:-1]
    return w


def base_form(word: str, lang: str = "") -> str:
    """Слово для словарика: без знаков препинания по краям, без корейской частицы на конце
    (학교에 → 학교), латиница и кириллица — строчными (в сканлейтах часто ВСЁ ЗАГЛАВНЫМИ)."""
    w = clean(word)
    if lang.startswith("ko") or _HANGUL.search(w[-1:] or ""):
        for j in _JOSA:
            if w.endswith(j) and len(w) - len(j) >= 2:
                w = w[: -len(j)]
                break
    if _CASED.search(w):
        w = w.lower()
    return w


# ================================================================ файл
def load(path: Path | None = None) -> list[Entry]:
    try:
        data = json.loads(Path(path or PATH).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    out = []
    names = set(Entry.__dataclass_fields__)
    for d in data.get("entries", []) if isinstance(data, dict) else []:
        if isinstance(d, dict) and str(d.get("word", "")).strip():
            out.append(Entry(**{k: v for k, v in d.items() if k in names}))
    return out


def save(entries: list[Entry], path: Path | None = None) -> None:
    """Через временный файл: если программу закроют посреди записи, словарик не испортится."""
    path = Path(path or PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps({"entries": [asdict(e) for e in entries]}, ensure_ascii=False, indent=1),
                   encoding="utf-8")
    tmp.replace(path)


def find(entries: list[Entry], word: str, lang: str) -> Entry | None:
    key = Entry(word, lang=lang).key
    return next((e for e in entries if e.key == key), None)


def add(entry: Entry, path: Path | None = None) -> tuple[Entry, bool]:
    """Добавить слово. Если оно уже есть — не дублируется: (запись из словарика, True)."""
    with _LOCK:
        entries = load(path)
        old = find(entries, entry.word, entry.lang)
        if old is not None:
            changed = False
            for f in ("translation", "context", "context_tr", "title"):
                if not getattr(old, f) and getattr(entry, f):
                    setattr(old, f, getattr(entry, f))
                    changed = True
            if changed:
                save(entries, path)
            return old, True
        entries.insert(0, entry)        # новые сверху
        save(entries, path)
        return entry, False


def search(entries: list[Entry], query: str) -> list[Entry]:
    q = query.strip().casefold()
    if not q:
        return entries
    return [e for e in entries if q in e.word.casefold() or q in e.translation.casefold()
            or q in e.note.casefold() or q in e.context.casefold() or q in e.title.casefold()]


def to_text(entries: list[Entry]) -> str:
    """Для буфера обмена: «слово — перевод (заметка)» по строке."""
    return "\n".join(f"{e.word} — {e.translation}" + (f" ({e.note})" if e.note else "") for e in entries)


def to_csv(entries: list[Entry]) -> str:
    """CSV: первые два столбца — слово и перевод, так его удобно загрузить в Anki и другие карточки."""
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(["word", "translation", "note", "language", "example", "example_translation", "title", "added"])
    for e in entries:
        w.writerow([e.word, e.translation, e.note, e.lang, e.context, e.context_tr, e.title,
                    time.strftime("%Y-%m-%d", time.localtime(e.added))])
    return buf.getvalue()


# ================================================================ перевод слова
def from_glossary(glossary: Glossary | None, word: str) -> str:
    """Перевод слова из глоссария тайтла (имя, термин), если оно там есть."""
    if glossary is None:
        return ""
    key = word.casefold()
    for e in glossary.entries:
        if e.source.strip().casefold() == key and e.target.strip():
            return e.target.strip()
    return ""


def translate_word(cfg, word: str, lang: str, tgt: str, context: str = "", context_tr: str = "",
                   glossary: Glossary | None = None) -> tuple[str, str]:
    """Перевод слова → (перевод, откуда). Сначала глоссарий тайтла, иначе выбранный переводчик:
    ИИ-переводчик видит фразу, где слово встретилось, обычный переводит само слово."""
    found = from_glossary(glossary, word)
    if found:
        return found, "glossary"
    from . import translators
    req = translators.TranslateRequest(texts=[word], src=lang or cfg.get("source_lang", "ko"), tgt=tgt, screen=True,
                                       history=[(context, context_tr)] if context and context_tr else [])
    out, by = translators.translate_with_fallback(cfg, req)
    return (out or [""])[0].strip(), by
