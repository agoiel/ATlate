"""Движки перевода. Все переключаются в настройках."""

from __future__ import annotations

from .base import RateLimit, TranslateError, TranslateRequest, Translator
from .llm import ClaudeTranslator, GeminiTranslator, OpenAICompatTranslator
from .machine import (DeepLTranslator, DuckDuckGoTranslator, GoogleFreeTranslator, LingvaTranslator,
                      MicrosoftTranslator, PapagoTranslator, YandexTranslator)

TRANSLATORS: dict[str, type[Translator]] = {
    cls.name: cls
    for cls in (GoogleFreeTranslator, YandexTranslator, DuckDuckGoTranslator, LingvaTranslator,
                DeepLTranslator, MicrosoftTranslator, PapagoTranslator,
                GeminiTranslator, ClaudeTranslator, OpenAICompatTranslator)
}


# Бесплатные способы без ключа: если выбранный не ответил, переводит следующий (запасные переводчики)
FREE_TRANSLATORS = ("google_free", "yandex", "lingva", "duckduckgo")


def create_translator(name: str, cfg) -> Translator:
    cls = TRANSLATORS.get(name, GoogleFreeTranslator)
    return cls(cfg.translator_settings(cls.name))


def translate_with_fallback(cfg, req: TranslateRequest, anchored: list[str] | None = None) -> tuple[list[str], str]:
    """Перевести запрос выбранным переводчиком → (переводы, кто перевёл). Если выбран бесплатный
    и он не ответил — пробуются другие бесплатные (как при переводе страницы, см. Pipeline._fallback).

    anchored — те же тексты с уже подставленными терминами глоссария: их получают обычные
    переводчики, а ИИ-переводчики — исходные тексты и термины списком (req.glossary).
    Для «Голоса в чат» и словарика: им не нужны ни кэш, ни контекст тайтла.
    """
    name = cfg.get("translator", "google_free")
    tor = create_translator(name, cfg)
    req.anchored = anchored if anchored and not tor.uses_llm else None
    try:
        return tor.translate(req), tor.title
    except TranslateError as err:
        paid_yandex = name == "yandex" and cfg.translator_settings("yandex").get("api_key")
        if not cfg.get("free_fallback", True) or name not in FREE_TRANSLATORS or paid_yandex:
            raise
        for alt_name in FREE_TRANSLATORS:
            if alt_name == name:
                continue
            # Яндекс — только бесплатным способом: платный ключ без спроса не тратим
            alt = TRANSLATORS[alt_name]({} if alt_name == "yandex" else cfg.translator_settings(alt_name))
            req.anchored = anchored or None
            try:
                return alt.translate(req), alt.title
            except TranslateError:
                continue
        raise err


__all__ = ["FREE_TRANSLATORS", "TRANSLATORS", "RateLimit", "TranslateError", "TranslateRequest", "Translator",
           "create_translator", "translate_with_fallback"]
