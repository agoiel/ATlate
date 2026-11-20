"""ИИ-переводчики: Gemini и любой OpenAI-совместимый сервер.

OpenAI-совместимый вариант подходит для:
- OpenAI, OpenRouter, DeepSeek (нужен ключ);
- локальных моделей через Ollama (http://localhost:11434/v1) или LM Studio
  (http://localhost:1234/v1) — бесплатно, но нужна видеокарта.

Запрос всегда одного размера: текущая страница + немного прошлых реплик + термины
глоссария, найденные на странице. Поэтому длинная сессия чтения не дорожает.
"""

from __future__ import annotations

import json
import re

from .. import languages
from .base import TranslateError, TranslateRequest, Translator

SYSTEM_PROMPT = """You are an experienced translator of webtoons, manhwa, manga and manhua.
Translate speech bubbles from {src} into {tgt}.

Rules:
- Write natural, lively spoken {tgt}, the way a good scanlation team would. Keep each character's tone (rude, polite, childish, archaic).
- The bubbles are consecutive lines of the same scene, in reading order. Use them and the previous context to resolve who speaks, gender, and formal/informal address.
- The text comes from OCR and may contain recognition errors or broken line wraps. Silently fix obvious errors.
- Always use the glossary translations for names and terms; inflect them according to {tgt} grammar.
- Sound effects: translate briefly and expressively (e.g. "Бах!", "Шорх").
- Do not add notes, explanations or quotes. Do not merge or split bubbles.
{extra}- Answer ONLY with JSON: {answer} — exactly {n} translations in the same order."""


SCREEN_PROMPT = """You translate text captured from a computer screen (apps, articles, chats, games, menus)
from {src} into {tgt}.

Rules:
- The fragments are separate blocks of text in reading order. Translate each one accurately and naturally.
- The text comes from OCR and may contain recognition errors or broken line wraps. Silently fix obvious errors.
- Keep short UI labels short. Keep numbers, names, usernames, links and code as they are.
- Always use the glossary translations for names and terms.
- Do not add notes or explanations. Do not merge or split fragments.
{extra}- Answer ONLY with JSON: {answer} — exactly {n} translations in the same order."""

# Авто-глоссарий: модель заодно называет новые имена и термины. Отдельного запроса нет —
# это часть того же ответа, поэтому пополнение глоссария почти ничего не стоит.
TERMS_RULE = """- Glossary building: in "terms" list proper names (characters, places, organizations, skills, items, titles, ranks) and recurring special terms that appear in these lines but are NOT in the glossary above. For each give "source" — exactly as written in the original lines, in base form without grammatical particles or case endings; "target" — the {tgt} translation you used; "note" — for people: gender and who they are, up to 5 words; otherwise what it is (place, skill, item…). Never include common words, interjections, sound effects, ordinary phrases or whole sentences. If there are none, return an empty list.
"""
DETECT_RULE = """- The original language is not specified: detect it yourself and put its ISO 639-1 code into "source_language".
"""


def build_messages(req: TranslateRequest) -> tuple[str, str]:
    src_name = languages.src(req.src)["llm"]
    tgt_name = languages.tgt(req.tgt)["llm"]
    prompt = SCREEN_PROMPT if req.screen else SYSTEM_PROMPT
    detect = languages.src(req.src)["google"] == languages.AUTO
    extra, answer = "", ['"translations": ["...", "..."]']
    if detect:
        extra += DETECT_RULE
        answer.append('"source_language": "xx"')
    if req.collect_terms:
        extra += TERMS_RULE.format(tgt=tgt_name)
        answer.append('"terms": [{"source": "...", "target": "...", "note": "..."}]')
    system = prompt.format(src=src_name, tgt=tgt_name, n=len(req.texts), extra=extra,
                           answer="{" + ", ".join(answer) + "}")

    parts: list[str] = []
    if req.glossary:
        parts.append("Glossary:")
        for e in req.glossary:
            note = f"  ({e.note})" if e.note else ""
            parts.append(f"- {e.source} → {e.target}{note}")
        parts.append("")
    if req.history:
        parts.append("Previous lines (context only, do not translate):")
        for o, t in req.history:
            parts.append(f"- {o} → {t}")
        parts.append("")
    parts.append(f"Bubbles to translate ({len(req.texts)}):")
    parts.append(json.dumps(req.texts, ensure_ascii=False, indent=0))
    return system, "\n".join(parts)


def parse_reply(raw: str, n: int) -> tuple[list[str], str, list[dict]]:
    """Ответ модели → (переводы, язык оригинала, если модель определяла его сама, новые термины)."""
    data = _load_json(raw)
    detected, terms = "", []
    if isinstance(data, dict):
        detected = str(data.get("source_language") or "").strip()[:8]
        raw_terms = data.get("terms")
        if isinstance(raw_terms, list):
            terms = [t for t in raw_terms if isinstance(t, dict)]
    return _translations(data, n), detected, terms


def parse_translations(raw: str, n: int) -> list[str]:
    """Достаёт список переводов из ответа модели, даже если вокруг JSON есть мусор."""
    return _translations(_load_json(raw), n)


def _load_json(raw: str):
    text = raw.strip()
    text = re.sub(r"^```(?:json)?|```$", "", text, flags=re.MULTILINE).strip()
    data = None
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}|\[.*\]", text, flags=re.DOTALL)
        if m:
            try:
                data = json.loads(m.group(0))
            except json.JSONDecodeError:
                data = None
    return data


def _translations(data, n: int) -> list[str]:
    if isinstance(data, dict):
        data = data.get("translations") or next((v for v in data.values() if isinstance(v, list)), None)
    if not isinstance(data, list):
        raise TranslateError("ИИ вернул ответ не в формате JSON. Попробуйте ещё раз или смените модель.")
    out = [str(x).strip() for x in data]
    if len(out) < n:
        out += [""] * (n - len(out))
    return out[:n]


class GeminiTranslator(Translator):
    name = "gemini"
    title = "Gemini"
    uses_llm = True

    def translate(self, req: TranslateRequest) -> list[str]:
        key = self.settings.get("api_key", "").strip()
        if not key:
            raise TranslateError("Gemini: укажите API-ключ в настройках (aistudio.google.com).")
        model = self.settings.get("model", "gemini-2.5-flash-lite").strip()
        self.detected, self.terms = "", []
        system, user = build_messages(req)
        body = {
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": [{"role": "user", "parts": [{"text": user}]}],
            "generationConfig": {"temperature": 0.3, "responseMimeType": "application/json"},
        }
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
        r = self._post(url, json=body, headers={"x-goog-api-key": key})
        data = r.json()
        try:
            raw = "".join(p.get("text", "") for p in data["candidates"][0]["content"]["parts"])
        except (KeyError, IndexError) as e:
            reason = data.get("promptFeedback", {}).get("blockReason", "")
            raise TranslateError(f"Gemini: пустой ответ {reason}".strip()) from e
        out, self.detected, self.terms = parse_reply(raw, len(req.texts))
        return out


class OpenAICompatTranslator(Translator):
    name = "openai"
    title = "OpenAI-совместимый"
    uses_llm = True

    def translate(self, req: TranslateRequest) -> list[str]:
        base = self.settings.get("base_url", "").strip().rstrip("/")
        model = self.settings.get("model", "").strip()
        if not base or not model:
            raise TranslateError("Укажите адрес сервера и модель в настройках.")
        headers = {}
        key = self.settings.get("api_key", "").strip()
        if key:
            headers["Authorization"] = f"Bearer {key}"
        self.detected, self.terms = "", []
        system, user = build_messages(req)
        body = {
            "model": model,
            "temperature": 0.3,
            "messages": [{"role": "system", "content": system},
                         {"role": "user", "content": user}],
        }
        r = self._post(f"{base}/chat/completions", json=body, headers=headers)
        try:
            raw = r.json()["choices"][0]["message"]["content"]
        except (KeyError, IndexError, ValueError) as e:
            raise TranslateError("Сервер вернул неожиданный ответ.") from e
        out, self.detected, self.terms = parse_reply(raw, len(req.texts))
        return out


class ClaudeTranslator(Translator):
    """Claude (Anthropic API)."""

    name = "claude"
    title = "Claude"
    uses_llm = True

    def translate(self, req: TranslateRequest) -> list[str]:
        key = self.settings.get("api_key", "").strip()
        if not key:
            raise TranslateError("Claude: укажите API-ключ в настройках (platform.claude.com).")
        model = (self.settings.get("model") or "claude-haiku-5-5").strip()
        self.detected, self.terms = "", []
        system, user = build_messages(req)
        r = self._post("https://api.anthropic.com/v1/messages",
                       headers={"x-api-key": key, "anthropic-version": "2023-06-01"},
                       json={"model": model, "max_tokens": 4096, "temperature": 0.3, "system": system,
                             "messages": [{"role": "user", "content": user}]})
        try:
            raw = "".join(b.get("text", "") for b in r.json()["content"] if b.get("type") == "text")
        except (KeyError, ValueError) as e:
            raise TranslateError("Claude: неожиданный ответ сервера.") from e
        out, self.detected, self.terms = parse_reply(raw, len(req.texts))
        return out
