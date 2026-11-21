"""Тайтлы — сохранённый контекст каждой манхвы (как в Android-версии).

У тайтла своё название, свой глоссарий, последние реплики (их видит переводчик)
и кэш уже переведённых облаков. Хранится в папке titles рядом с программой.
"""

from __future__ import annotations

import copy
import json
import time
import uuid
from collections import OrderedDict, deque
from dataclasses import dataclass, field
from pathlib import Path

from .config import DATA_ROOT

TITLES_DIR = DATA_ROOT / "titles"
MAX_HISTORY = 60
MAX_CACHE = 3000


@dataclass
class Title:
    id: str
    name: str
    glossary: str = ""
    history: deque = field(default_factory=lambda: deque(maxlen=MAX_HISTORY))
    cache: OrderedDict = field(default_factory=OrderedDict)
    updated: float = field(default_factory=time.time)
    # своя область, режим и языки тайтла (см. PROFILE_KEYS); пусто — ещё не запоминались
    profile: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        d = {"id": self.id, "name": self.name, "glossary": self.glossary, "updated": self.updated,
             "history": [list(p) for p in self.history], "cache": dict(self.cache)}
        if self.profile:
            d["profile"] = self.profile
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "Title":
        t = cls(d["id"], d.get("name", ""), d.get("glossary", ""), updated=d.get("updated", 0))
        prof = d.get("profile")
        if isinstance(prof, dict):
            t.profile = {k: v for k, v in prof.items() if k in PROFILE_KEYS}
        for p in d.get("history", []):
            if isinstance(p, list) and len(p) == 2:
                t.history.append((str(p[0]), str(p[1])))
        t.cache.update({str(k): str(v) for k, v in d.get("cache", {}).items()})
        return t


# Что тайтл помнит сам: у каждой манхвы своя область чтения, режим и языки, а у игры — язык чата
# и словарь для «Голоса в чат». Запоминается, когда переключаются на другой тайтл, и восстанавливается,
# когда возвращаются к этому.
PROFILE_KEYS = ("last_region", "translate_mode", "source_lang", "auto_lang", "target_lang", "content_mode",
                "voice_chat_lang", "voice_glossary")


def snapshot(cfg: dict) -> dict:
    return {k: copy.deepcopy(cfg.get(k)) for k in PROFILE_KEYS if k in cfg}


def remember(title_id: str, cfg: dict) -> None:
    """Запомнить у тайтла текущие область, режим и языки (перед переключением на другой)."""
    t = load(title_id)
    if t is not None:
        t.profile = snapshot(cfg)
        save(t)


def path(title_id: str) -> Path:
    TITLES_DIR.mkdir(parents=True, exist_ok=True)
    return TITLES_DIR / f"{title_id}.json"


def load(title_id: str) -> Title | None:
    if not title_id:
        return None
    p = path(title_id)
    try:
        return Title.from_dict(json.loads(p.read_text(encoding="utf-8"))) if p.exists() else None
    except (OSError, ValueError, KeyError):
        return None


def save(t: Title) -> None:
    while len(t.cache) > MAX_CACHE:
        t.cache.popitem(last=False)
    t.updated = time.time()
    p = path(t.id)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(t.to_dict(), ensure_ascii=False), encoding="utf-8")
    tmp.replace(p)


def create(name: str, glossary: str = "") -> Title:
    t = Title(uuid.uuid4().hex[:8], name.strip(), glossary)
    save(t)
    return t


def delete(title_id: str) -> None:
    try:
        path(title_id).unlink()
    except OSError:
        pass


def clear_context(title_id: str) -> None:
    t = load(title_id)
    if t:
        t.history.clear()
        t.cache.clear()
        save(t)


def list_titles() -> list[Title]:
    TITLES_DIR.mkdir(parents=True, exist_ok=True)
    out = []
    for p in TITLES_DIR.glob("*.json"):
        t = load(p.stem)
        if t:
            out.append(t)
    return sorted(out, key=lambda t: t.updated, reverse=True)
