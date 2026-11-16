"""Автоопределение языка оригинала на экране.

Распознавателю нужно заранее сказать язык, поэтому «узнать язык по тексту» нельзя: текста ещё нет.
Вместо этого область быстро распознаётся каждым доступным языком, и побеждает тот, чей
распознаватель нашёл больше букв своей письменности и меньше постороннего мусора.

Здесь только правила выбора (без распознавания) — их можно проверять на обычных строках.
"""

from __future__ import annotations

import re

_HANGUL = re.compile(r"[가-힯ᄀ-ᇿ㄰-㆏]")
_HIRAGANA = re.compile(r"[぀-ゟ]")
_KANA = re.compile(r"[぀-ヿㇰ-ㇿｦ-ﾟ]")
_HAN = re.compile(r"[㐀-䶿一-鿿]")
_LATIN = re.compile(r"[A-Za-zÀ-ɏ]")
_LETTER = re.compile(r"[^\W\d_]", re.UNICODE)
_WORD = re.compile(r"[A-Za-zÀ-ɏ]+")
_VOWEL = re.compile(r"[aeiouyAEIOUYÀ-ÿ]")

# Иероглифы, которые пишутся по-разному в упрощённом и традиционном китайском (частые)
_SIMPLIFIED = ("们这说时会对发开关东车门问间长见现学习书话语读写听买卖钱饭个为国来过还进远边动体点热爱亲乐万与么从儿兴军"
               "办务区医华单变号吗员团图场坏声处头妈孙实宝导将岁师带帮应张强当总战报数无旧机杀权条样气灭灵爷独电画确离"
               "种笔节类红级线练组经结给统继续网罗联胜脑艺药获虽观规视觉计认让记讲许论设证试该请谁调谈谢负责败货质贵"
               "费赶转轻输达运选钟铁错镇闻队阳阴难顺须顾领题风飞马鱼鸟龙齐")
_TRADITIONAL = ("們這說時會對發開關東車門問間長見現學習書話語讀寫聽買賣錢飯個為國來過還進遠邊動體點熱愛親樂萬與麼從兒興軍"
                "辦務區醫華單變號嗎員團圖場壞聲處頭媽孫實寶導將歲師帶幫應張強當總戰報數無舊機殺權條樣氣滅靈爺獨電畫確離"
                "種筆節類紅級線練組經結給統繼續網羅聯勝腦藝藥獲雖觀規視覺計認讓記講許論設證試該請誰調談謝負責敗貨質貴"
                "費趕轉輕輸達運選鐘鐵錯鎮聞隊陽陰難順須顧領題風飛馬魚鳥龍齊")
_SIMP_SET, _TRAD_SET = set(_SIMPLIFIED), set(_TRADITIONAL)

MIN_LETTERS = 4         # меньше букв — судить не о чем («!?», одно короткое слово)
CHINESE = ("zh", "zh-tw")


def _count(rx: re.Pattern, text: str) -> int:
    return len(rx.findall(text))


def _wordlike(text: str) -> int:
    """Буквы латиницы в «настоящих» словах: от трёх букв и с гласной.

    Распознаватель латиницы на корейском или японском тексте выдаёт обрывки вроде «OJ Ll I7» —
    такие за слова не считаются.
    """
    return sum(len(w) for w in _WORD.findall(text) if len(w) >= 3 and _VOWEL.search(w))


def own_letters(text: str, lang: str) -> int:
    """Сколько в тексте букв письменности этого языка."""
    if lang == "ko":
        return _count(_HANGUL, text)
    if lang == "ja":
        return _count(_KANA, text) + _count(_HAN, text)
    if lang in CHINESE:
        return _count(_HAN, text)
    return _wordlike(text)


def score(text: str, lang: str) -> float:
    """Насколько текст похож на правильно распознанный текст этого языка.

    Буквы своей письменности, умноженные на «чистоту»: чужой распознаватель выдаёт смесь
    обрывков разных письменностей, свой — почти только свои буквы.
    """
    own = own_letters(text, lang)
    if own == 0:
        return 0.0
    total = _count(_LETTER, text)
    if lang == "ja":
        # японский текст почти всегда содержит хирагану; катакана не в счёт — её легко спутать
        # с иероглифами (ロ и 口, カ и 力, ニ и 二), и «японский» находился бы в китайском тексте
        hira = _count(_HIRAGANA, text)
        if hira < 2 or hira < 0.15 * own:
            return 0.0
    if lang in CHINESE and _count(_HIRAGANA, text) >= 2:
        return 0.0
    if lang == "en":
        total = max(total, _count(_LATIN, text))
    return own * own / max(1, total)


def chinese_variant(texts: dict[str, str], current: str) -> str:
    """Упрощённый или традиционный — по иероглифам, которые в них пишутся по-разному."""
    joined = "".join(texts.get(code, "") for code in CHINESE)
    simp = sum(1 for ch in joined if ch in _SIMP_SET)
    trad = sum(1 for ch in joined if ch in _TRAD_SET)
    have = [c for c in CHINESE if c in texts]
    if len(have) == 1:
        return have[0]
    if simp > trad:
        return "zh"
    if trad > simp:
        return "zh-tw"
    return current if current in CHINESE else "zh"


def choose(texts: dict[str, str], current: str) -> tuple[str | None, dict[str, float]]:
    """Выбрать язык по результатам пробного распознавания.

    texts — что прочитал распознаватель каждого языка (код языка → весь текст).
    Возвращает (язык или None, если уверенно сказать нельзя; оценки по языкам).
    """
    scores = {lang: round(score(text, lang), 1) for lang, text in texts.items()}
    cjk = {lang: s for lang, s in scores.items() if lang != "en"}
    best = max(cjk, key=cjk.get) if cjk else None
    en = scores.get("en", 0.0)
    if best is not None and cjk[best] >= MIN_LETTERS and cjk[best] >= 0.3 * en:
        if best in CHINESE:
            best = chinese_variant(texts, current)
        return best, scores
    if en >= MIN_LETTERS:
        return "en", scores
    return None, scores


def fit(text: str, lang: str) -> str:
    """Подходит ли распознанный текст выбранному языку: good | bad | empty.

    empty — букв слишком мало, чтобы судить (страница без текста); bad — буквы есть,
    но не той письменности (или обрывки вместо слов): похоже, язык на экране другой.
    """
    total = _count(_LETTER, text)
    if total < MIN_LETTERS:
        return "empty"
    own = own_letters(text, lang)
    hira = _count(_HIRAGANA, text)
    if lang == "ja" and own >= 8 and hira == 0:
        return "bad"        # одни иероглифы без хираганы — скорее китайский
    if lang in CHINESE and hira >= 2:
        return "bad"
    return "good" if own >= MIN_LETTERS and own >= 0.5 * total else "bad"
