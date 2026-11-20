"""Глоссарий тайтла: имена, термины, обращения.

Формат файла (JSON, тот же будет и в Android-версии):
{
  "name": "Название тайтла",
  "source_lang": "ko",
  "entries": [
    {"source": "강진우", "target": "Кан Джину", "note": "главный герой, мужчина"},
    {"source": "헌터", "target": "охотник", "note": ""}
  ]
}

Как используется:
- обычные переводчики (Google/DeepL/Microsoft): перед отправкой термин в тексте заменяется
  сразу на русский вариант («якорь»), и переводчик оставляет его как есть;
- ИИ-переводчики (Gemini/OpenAI): найденные на странице термины с заметками
  передаются в подсказку, и модель сама склоняет их по падежам.

Авто-глоссарий ("auto": true в файле): ИИ-переводчик в том же ответе, что и перевод, называет
новые имена и термины, а программа добавляет их сюда. Такие записи помечены "auto": true —
их видно в редакторе. Чтобы мусор не попадал в глоссарий, каждый термин проверяется:
он должен встречаться в переведённом тексте, не быть фразой, не повторять уже записанный.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

# Авто-глоссарий: не больше стольких автоматических записей (дальше новые не добавляются —
# переводчику и так уходят только термины текущей страницы, но файл не должен расти бесконечно)
MAX_AUTO = 1500
MAX_NEW_PER_PAGE = 8          # за одну страницу — не больше, иначе модель «перестаралась»

_SENTENCE = re.compile(r"[.!?。！？…]|\.\.\.")
_LETTER = re.compile(r"[^\W\d_]", re.UNICODE)
_LATIN_WORD = re.compile(r"[A-Za-z\u00C0-\u024F]")
_HANGUL = re.compile(r"[\uAC00-\uD7AF]")
# корейские частицы и окончания, которые модель могла оставить на конце имени (от длинных к коротким)
_JOSA = ("에게서", "께서는", "이랑", "에게", "에서", "한테", "께서", "이나", "이야", "은", "는", "이", "가",
         "을", "를", "의", "에", "와", "과", "도", "만", "랑", "아", "야")


@dataclass
class GlossaryEntry:
    source: str
    target: str
    note: str = ""
    auto: bool = False        # добавлено авто-глоссарием, а не человеком


def _key(text: str) -> str:
    """Запись термина для сравнения: без регистра, пробелов и корейских частиц на конце."""
    k = re.sub(r"\s+", "", text).casefold().strip("\"'«»“”‘’()[]「」『』")
    if _HANGUL.search(k[-1:] or ""):
        for j in _JOSA:
            if k.endswith(j) and len(k) - len(j) >= 2:
                return k[: -len(j)]
    return k


@dataclass
class Glossary:
    name: str = ""
    source_lang: str = ""
    entries: list[GlossaryEntry] = field(default_factory=list)
    path: Path | None = None
    auto: bool = False        # пополнять сам (только с ИИ-переводчиками)

    # ------------------------------------------------------------ файлы
    @classmethod
    def load(cls, path: Path) -> "Glossary":
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        entries = [GlossaryEntry(e.get("source", ""), e.get("target", ""), e.get("note", ""), bool(e.get("auto")))
                   for e in data.get("entries", []) if e.get("source")]
        return cls(name=data.get("name", Path(path).stem), source_lang=data.get("source_lang", ""),
                   entries=entries, path=Path(path), auto=bool(data.get("auto")))

    def save(self, path: Path | None = None) -> None:
        path = Path(path or self.path)
        entries = []
        for e in self.entries:
            if not e.source.strip():
                continue
            d = {"source": e.source, "target": e.target, "note": e.note}
            if e.auto:
                d["auto"] = True        # у записей человека пометки нет — файл не раздувается
            entries.append(d)
        data = {"name": self.name, "source_lang": self.source_lang}
        if self.auto:
            data["auto"] = True
        data["entries"] = entries
        # через временный файл: если программу закроют посреди записи, глоссарий не испортится
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(path)
        self.path = path

    # ------------------------------------------------------------ авто-глоссарий
    @property
    def auto_count(self) -> int:
        return sum(1 for e in self.entries if e.auto)

    def add_terms(self, terms: list[dict], texts: list[str]) -> list[GlossaryEntry]:
        """Добавить новые термины от ИИ-переводчика. Возвращает то, что действительно добавлено.

        Термин отбрасывается, если его нет в переведённом тексте (модель его выдумала или
        «исправила»), если это фраза, а не имя, если он уже есть в глоссарии (в любом написании —
        с другим регистром или с частицей на конце) или если лимит авто-записей исчерпан.
        """
        page = re.sub(r"\s+", "", "\n".join(texts)).casefold()
        known = {_key(e.source) for e in self.entries}
        room = MAX_AUTO - self.auto_count
        added: list[GlossaryEntry] = []
        for t in terms:
            if len(added) >= min(MAX_NEW_PER_PAGE, room):
                break
            src = re.sub(r"\s+", " ", str(t.get("source") or "")).strip()
            tgt = re.sub(r"\s+", " ", str(t.get("target") or "")).strip()
            note = re.sub(r"\s+", " ", str(t.get("note") or "")).strip()[:60]
            if not src or not tgt or len(src) > 40 or len(tgt) > 60:
                continue
            if _SENTENCE.search(src) or len(_LETTER.findall(src)) < 2:
                continue        # фраза или вообще не слово (цифры, знаки)
            latin = bool(_LATIN_WORD.search(src))
            if (latin and len(src.split()) > 4) or (not latin and len(src.replace(" ", "")) > 12):
                continue        # слишком длинно для имени или термина
            if not latin and tgt.casefold() == src.casefold():
                continue        # модель не перевела
            if re.sub(r"\s+", "", src).casefold() not in page:
                continue        # в тексте страницы такого нет
            k = _key(src)
            if not k or k in known:
                continue
            known.add(k)
            e = GlossaryEntry(src, tgt, note, auto=True)
            self.entries.append(e)
            added.append(e)
        return added

    def clear_auto(self) -> int:
        """Удалить все записи, добавленные автоматически. Возвращает, сколько удалено."""
        n = self.auto_count
        self.entries = [e for e in self.entries if not e.auto]
        return n

    # ------------------------------------------------------------ применение
    def _sorted(self) -> list[GlossaryEntry]:
        # длинные термины первыми, чтобы «Кан Джину» не разбился на «Кан»
        return sorted((e for e in self.entries if e.source and e.target),
                      key=lambda e: len(e.source), reverse=True)

    @staticmethod
    def _pattern(e: GlossaryEntry) -> re.Pattern:
        """Термин ищется только в начале слова: «인형» (кукла) не совпадёт с «형».
        После термина могут идти частицы и окончания (선배님, 선배가, 길드에서)."""
        # английские термины — целым словом: «Gate» не должен совпасть с «Gatekeeper»
        end = r"(?![^\W_])" if e.source[-1:].isascii() and e.source[-1:].isalpha() else ""
        return re.compile(r"(?<![^\W_])" + re.escape(e.source) + end, re.IGNORECASE)

    def find(self, texts: list[str]) -> list[GlossaryEntry]:
        """Термины, которые встречаются в этих текстах."""
        joined = "\n".join(texts)
        return [e for e in self._sorted() if self._pattern(e).search(joined)]

    def apply_anchors(self, text: str) -> str:
        """Заменяет термины на их перевод прямо в исходном тексте."""
        out = text
        for e in self._sorted():
            # глагольные окончания не трогаем: «각성했다» — «пробудился», а не «пробуждение했다»
            p = re.compile(self._pattern(e).pattern + r"(?![하했한할해합함됐된될되돼])", re.IGNORECASE)
            out = p.sub(lambda _m, t=e.target: t, out)
        return out


def universal_for(folder: Path, lang: str) -> str:
    """Имя встроенного универсального глоссария для языка оригинала ('' — нет такого).

    Для языков на латинице без своего глоссария — английский: в нём корейские обращения
    латиницей (hyung, sunbae), их пишут и в сканлейтах на других языках.
    """
    from . import languages
    name = f"universal_{lang.split('-')[0]}.json"
    if (folder / name).exists():
        return name
    if languages.is_latin(lang) and (folder / "universal_en.json").exists():
        return "universal_en.json"
    return ""


def list_glossaries(folder: Path) -> list[Path]:
    folder.mkdir(parents=True, exist_ok=True)
    return sorted(folder.glob("*.json"))
