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
# термин начинается с иероглифа или каны (японский, китайский — языки без пробелов)
_CJK_CHARS = "\u3040-\u30FF\u3400-\u4DBF\u4E00-\u9FFF\uF900-\uFAFF"
_CJK_START = re.compile(f"[{_CJK_CHARS}]")
_MAY_START = r"(?:(?<![^\W_])|(?=[" + _CJK_CHARS + "]))"
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


# Встроенные глоссарии переводят на русский. В старых копиях (уже скопированных в папку данных)
# поля target_lang ещё нет — для них язык перевода берётся отсюда.
BUNDLED_RU = {"universal_ko.json", "universal_en.json", "example_ko.json"}


@dataclass
class Glossary:
    name: str = ""
    source_lang: str = ""
    entries: list[GlossaryEntry] = field(default_factory=list)
    path: Path | None = None
    auto: bool = False        # пополнять сам (только с ИИ-переводчиками)
    target_lang: str = ""     # на какой язык переводы в записях ('' — подходит для любого)

    @property
    def title(self) -> str:
        """Название для показа: у встроенных глоссариев — на языке интерфейса."""
        if self.path is not None and self.path.name in BUNDLED_RU:
            from .i18n import tr
            return tr(self.name)
        return self.name or (self.path.stem if self.path is not None else "")

    def fits(self, target_lang: str) -> bool:
        """Подходит ли глоссарий для перевода на этот язык (иначе в перевод попали бы чужие слова)."""
        return not self.target_lang or self.target_lang == target_lang

    # ------------------------------------------------------------ файлы
    @classmethod
    def load(cls, path: Path) -> "Glossary":
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        entries = [GlossaryEntry(e.get("source", ""), e.get("target", ""), e.get("note", ""), bool(e.get("auto")))
                   for e in data.get("entries", []) if e.get("source")]
        target = data.get("target_lang") or ("ru" if Path(path).name in BUNDLED_RU else "")
        return cls(name=data.get("name", Path(path).stem), source_lang=data.get("source_lang", ""),
                   entries=entries, path=Path(path), auto=bool(data.get("auto")), target_lang=target)

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
        if self.target_lang:
            data["target_lang"] = self.target_lang
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
    def _term(e: GlossaryEntry, inside: bool = False) -> str:
        """Шаблон одного термина. Термин ищется только в начале слова: «인형» (кукла) не совпадёт
        с «형». После термина могут идти частицы и окончания (선배님, 선배가, 길드에서).

        Исключение — японский и китайский: там нет пробелов, и «начало слова» почти никогда не
        выполнялось («強くなった田中さん» — 田中 не находился). Для терминов из иероглифов и каны
        длиной от двух знаков начало слова не требуется; однознаковые («王») — по-прежнему только
        в начале, иначе они находились бы внутри других слов («王国»).
        """
        cjk = bool(_CJK_START.match(e.source))
        start = "" if cjk and len(e.source) >= 2 else r"(?<![^\W_])"
        if inside and not cjk:
            start = ""      # внутри общего шаблона начало слова уже проверил быстрый отсев (_MAY_START)
        # английские термины — целым словом: «Gate» не должен совпасть с «Gatekeeper»
        end = r"(?![^\W_])" if e.source[-1:].isascii() and e.source[-1:].isalpha() else ""
        return start + re.escape(e.source) + end

    def _compiled(self):
        """Один общий шаблон на весь глоссарий — собирается заново, только когда глоссарий изменился.

        Раньше на каждой странице для каждой записи собирался свой шаблон: при 500+ записях
        встроенный кэш Python переполнялся, и поиск терминов занимал до секунды на страницу.
        """
        sig = tuple((e.source, e.target) for e in self.entries)
        cache = self.__dict__.get("_cache")
        if cache is not None and cache[0] == sig:
            return cache[1:]
        terms = self._sorted()
        by_key: dict[str, list[GlossaryEntry]] = {}
        for e in terms:
            by_key.setdefault(e.source.lower(), []).append(e)
        if terms:
            alts = "|".join(self._term(e, inside=True) for e in terms)
            # поиск: на каждой позиции текста (заглядыванием вперёд) — самый длинный термин, который там начинается
            # быстрый отсев: термин может начаться только в начале слова или на иероглифе/кане —
            # остальные позиции (середина корейских и английских слов) даже не проверяются
            find_re = re.compile(r"(?=" + _MAY_START + "(" + alts + "))", re.IGNORECASE)
            # подстановка: альтернативы от длинных к коротким — «Кан Джину» не разобьётся на «Кан»;
            # глагольные окончания не трогаем: «각성했다» — «пробудился», а не «пробуждение했다»
            anchor_re = re.compile(_MAY_START + "(?:" + alts + r")(?![하했한할해합함됐된될되돼])", re.IGNORECASE)
        else:
            find_re = anchor_re = None
        # термины, которые сами являются началом другого термина (선배 → 선배님): на той же позиции
        # шаблон найдёт только длинный, короткие проверяются отдельно
        prefixes: dict[int, list[GlossaryEntry]] = {}
        for e in terms:
            low = e.source.lower()
            shorter = [f for k in range(1, len(low)) for f in by_key.get(low[:k], [])]
            if shorter:
                prefixes[id(e)] = shorter
        self.__dict__["_cache"] = (sig, find_re, anchor_re, by_key, terms, prefixes)
        return find_re, anchor_re, by_key, terms, prefixes

    def _entries_for(self, matched: str, by_key: dict, terms: list[GlossaryEntry]) -> list[GlossaryEntry]:
        found = by_key.get(matched.lower())
        if found is None:
            # редкие буквы, у которых регистр сравнивается иначе, — сверяем по одному
            found = [e for e in terms if re.fullmatch(self._term(e), matched, re.IGNORECASE)]
        return found

    def find(self, texts: list[str]) -> list[GlossaryEntry]:
        """Термины, которые встречаются в этих текстах (от длинных к коротким)."""
        find_re, _a, by_key, terms, prefixes = self._compiled()
        if find_re is None:
            return []
        joined = "\n".join(texts)
        hits: set[int] = set()
        for m in find_re.finditer(joined):
            for e in self._entries_for(m.group(1), by_key, terms):
                hits.add(id(e))
                for f in prefixes.get(id(e), ()):
                    if id(f) not in hits and re.compile(self._term(f), re.IGNORECASE).match(joined, m.start()):
                        hits.add(id(f))
        return [e for e in terms if id(e) in hits]

    def apply_anchors(self, text: str) -> str:
        """Заменяет термины на их перевод прямо в исходном тексте."""
        _f, anchor_re, by_key, terms, _p = self._compiled()
        if anchor_re is None:
            return text

        def target(m: re.Match) -> str:
            found = self._entries_for(m.group(0), by_key, terms)
            return found[0].target if found else m.group(0)

        return anchor_re.sub(target, text)


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
