"""Таблица языков и их коды для разных движков.

Чтобы добавить язык, допишите сюда новую запись.
"""

# Языки оригинала
SOURCE_LANGUAGES: dict[str, dict] = {
    "ko": {
        "name": "Корейский (манхва)",
        "windows": ["ko"],
        "easyocr": ["ko", "en"],
        "google": "ko",
        "deepl": "KO",
        "microsoft": "ko",
        "llm": "Korean",
        "joiner": " ",        # чем склеивать строки внутри облака
        "direction": "ltr",   # порядок облаков в одном ряду
    },
    "ja": {
        "name": "Японский (манга)",
        "windows": ["ja"],
        "easyocr": ["ja", "en"],
        "google": "ja",
        "deepl": "JA",
        "microsoft": "ja",
        "llm": "Japanese",
        "joiner": "",
        "direction": "rtl",
    },
    "zh": {
        "name": "Китайский упрощённый (маньхуа)",
        "windows": ["zh-Hans", "zh-CN"],
        "easyocr": ["ch_sim", "en"],
        "google": "zh-CN",
        "deepl": "ZH",
        "microsoft": "zh-Hans",
        "llm": "Simplified Chinese",
        "joiner": "",
        "direction": "ltr",
    },
    "zh-tw": {
        "name": "Китайский традиционный",
        "windows": ["zh-Hant", "zh-TW"],
        "easyocr": ["ch_tra", "en"],
        "google": "zh-TW",
        "deepl": "ZH",
        "microsoft": "zh-Hant",
        "llm": "Traditional Chinese",
        "joiner": "",
        "direction": "ltr",
    },
    "en": {
        "name": "Английский (сканлейты)",
        "windows": ["en"],
        "easyocr": ["en"],
        "google": "en",
        "deepl": "EN",
        "microsoft": "en",
        "llm": "English",
        "joiner": " ",
        "direction": "ltr",
    },
    # Языки на латинице. Распознаются той же латинской моделью, что и английский: в Windows подойдёт
    # пакет распознавания самого языка, а если его нет — любой установленный пакет латиницы (обычно английский).
    "de": {"name": "Немецкий", "windows": ["de"], "easyocr": ["de", "en"], "google": "de", "deepl": "DE",
           "microsoft": "de", "llm": "German", "joiner": " ", "direction": "ltr"},
    "es": {"name": "Испанский", "windows": ["es"], "easyocr": ["es", "en"], "google": "es", "deepl": "ES",
           "microsoft": "es", "llm": "Spanish", "joiner": " ", "direction": "ltr"},
    "pt": {"name": "Португальский", "windows": ["pt"], "easyocr": ["pt", "en"], "google": "pt", "deepl": "PT",
           "microsoft": "pt", "llm": "Portuguese", "joiner": " ", "direction": "ltr"},
    "id": {"name": "Индонезийский", "windows": ["id"], "easyocr": ["id", "en"], "google": "id", "deepl": "ID",
           "microsoft": "id", "llm": "Indonesian", "joiner": " ", "direction": "ltr"},
    # DeepL филиппинский не знает (пустой код — переводчик этот язык не поддерживает)
    "fil": {"name": "Филиппинский", "windows": ["fil", "tl"], "easyocr": ["tl", "en"], "google": "tl", "deepl": "",
            "microsoft": "fil", "llm": "Filipino (Tagalog)", "joiner": " ", "direction": "ltr"},
    # Любой другой текст на латинице: язык определяет сам переводчик (auto — «определи сам»)
    "latin": {"name": "Латиница (язык определит переводчик)", "windows": ["en"], "easyocr": ["en"],
              "google": "auto", "deepl": "auto", "microsoft": "auto",
              "llm": "the original language (detect it from the text)", "joiner": " ", "direction": "ltr"},
}

# Языки, которые пишутся латиницей (их читает одна и та же модель распознавания)
LATIN = {"en", "de", "es", "pt", "id", "fil", "latin"}
# Что пробует автоопределение языка: по одному языку на письменность (латиница — одна на всех)
PROBE = ["ko", "ja", "zh", "zh-tw", "latin"]
AUTO = "auto"       # код «пусть переводчик определит язык сам»

# Языки перевода
# Языки перевода. Пустой код — этот переводчик язык не поддерживает.
TARGET_LANGUAGES: dict[str, dict] = {
    "ru": {"name": "Русский", "google": "ru", "deepl": "RU", "microsoft": "ru", "llm": "Russian"},
    "uk": {"name": "Украинский", "google": "uk", "deepl": "UK", "microsoft": "uk", "llm": "Ukrainian"},
    "be": {"name": "Белорусский", "google": "be", "deepl": "", "microsoft": "", "llm": "Belarusian"},
    "kk": {"name": "Казахский", "google": "kk", "deepl": "", "microsoft": "kk", "llm": "Kazakh"},
    "uz": {"name": "Узбекский", "google": "uz", "deepl": "", "microsoft": "uz", "llm": "Uzbek"},
    "en": {"name": "Английский", "google": "en", "deepl": "EN-US", "microsoft": "en", "llm": "English"},
    "de": {"name": "Немецкий", "google": "de", "deepl": "DE", "microsoft": "de", "llm": "German"},
    "fr": {"name": "Французский", "google": "fr", "deepl": "FR", "microsoft": "fr", "llm": "French"},
    "es": {"name": "Испанский", "google": "es", "deepl": "ES", "microsoft": "es", "llm": "Spanish"},
    "pt": {"name": "Португальский (Бразилия)", "google": "pt", "deepl": "PT-BR", "microsoft": "pt", "llm": "Brazilian Portuguese"},
    "it": {"name": "Итальянский", "google": "it", "deepl": "IT", "microsoft": "it", "llm": "Italian"},
    "pl": {"name": "Польский", "google": "pl", "deepl": "PL", "microsoft": "pl", "llm": "Polish"},
    "cs": {"name": "Чешский", "google": "cs", "deepl": "CS", "microsoft": "cs", "llm": "Czech"},
    "tr": {"name": "Турецкий", "google": "tr", "deepl": "TR", "microsoft": "tr", "llm": "Turkish"},
    "id": {"name": "Индонезийский", "google": "id", "deepl": "ID", "microsoft": "id", "llm": "Indonesian"},
    "vi": {"name": "Вьетнамский", "google": "vi", "deepl": "", "microsoft": "vi", "llm": "Vietnamese"},
    "th": {"name": "Тайский", "google": "th", "deepl": "", "microsoft": "th", "llm": "Thai"},
    "ar": {"name": "Арабский", "google": "ar", "deepl": "AR", "microsoft": "ar", "llm": "Arabic"},
}


import re as _re

_HANGUL = _re.compile(r"[\uAC00-\uD7AF\u1100-\u11FF\u3130-\u318F]")
_KANA = _re.compile(r"[\u3040-\u30FF\u31F0-\u31FF\uFF66-\uFF9F]")
_HAN = _re.compile(r"[\u3400-\u4DBF\u4E00-\u9FFF]")
_LATIN = _re.compile(r"[A-Za-z\u00C0-\u024F]")


def detect(text: str, selected: str) -> str:
    """Язык облака по символам.

    Латиница: если выбран язык на латинице — он; иначе (вставка на латинице в корейской манхве и т.п.)
    язык определит переводчик. По буквам немецкий от испанского не отличить.
    """
    h, k = len(_HANGUL.findall(text)), len(_KANA.findall(text))
    c, latin = len(_HAN.findall(text)), len(_LATIN.findall(text))
    if h and h >= k:
        return "ko"
    if k:
        return "ja"
    if c:
        return selected if selected in ("ja", "zh", "zh-tw") else "zh"
    if latin >= 2:
        return selected if selected in LATIN else "latin"
    return selected


def is_latin(code: str) -> bool:
    return code in LATIN


def name_of(code: str) -> str:
    """Название языка по коду — нашему или ISO (его возвращают переводчики, когда определяют язык сами)."""
    code = (code or "").strip()
    low = code.lower()
    for table in (SOURCE_LANGUAGES, TARGET_LANGUAGES):
        if low in table:
            return table[low]["name"].split(" (")[0]
    base = low.replace("_", "-").split("-")[0]
    if base == "tl":
        base = "fil"
    for table in (SOURCE_LANGUAGES, TARGET_LANGUAGES):
        if base in table:
            return table[base]["name"].split(" (")[0]
    return code


def src(code: str) -> dict:
    return SOURCE_LANGUAGES.get(code, SOURCE_LANGUAGES["ko"])


def tgt(code: str) -> dict:
    return TARGET_LANGUAGES.get(code, TARGET_LANGUAGES["ru"])
