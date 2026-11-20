from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field

import requests

from ..glossary import GlossaryEntry

TIMEOUT = 30


class TranslateError(RuntimeError):
    """Ошибка перевода с понятным текстом."""

    def __init__(self, message: str, code: int = 0):
        super().__init__(message)
        self.code = code


class RateLimit:
    """Между двумя запросами к сервису перевода проходит не меньше заданной паузы."""

    interval = 1.0
    _last = 0.0
    _lock = threading.Lock()

    @classmethod
    def wait(cls) -> None:
        with cls._lock:
            delay = cls._last + cls.interval - time.monotonic()
            if delay > 0:
                time.sleep(delay)
            cls._last = time.monotonic()


@dataclass
class TranslateRequest:
    texts: list[str]                      # облака текущей страницы по порядку
    src: str                              # код языка оригинала (ko, ja, ...)
    tgt: str                              # код языка перевода (ru, ...)
    history: list[tuple[str, str]] = field(default_factory=list)   # прошлые реплики (оригинал, перевод)
    glossary: list[GlossaryEntry] = field(default_factory=list)     # термины, найденные на странице
    anchored: list[str] | None = None     # тексты с подставленными якорями (для обычных переводчиков)
    screen: bool = False                  # обычный текст с экрана, а не облака манхвы
    collect_terms: bool = False           # ИИ-переводчик заодно называет новые имена и термины (авто-глоссарий)


class Translator:
    name = "base"
    title = "Базовый"
    uses_llm = False

    def __init__(self, settings: dict):
        self.settings = settings or {}
        self.http = requests.Session()
        # Итоги последнего перевода, кроме самих переводов:
        self.detected = ""                # язык оригинала, если переводчик определял его сам (код ISO)
        self.terms: list[dict] = []       # новые имена и термины от ИИ-переводчика (для авто-глоссария)

    def translate(self, req: TranslateRequest) -> list[str]:
        raise NotImplementedError

    # ---------------------------------------------------------------- утилиты
    def _post(self, url: str, **kw) -> requests.Response:
        return self._request("post", url, **kw)

    def _get(self, url: str, **kw) -> requests.Response:
        return self._request("get", url, **kw)

    def _request(self, method: str, url: str, **kw) -> requests.Response:
        for attempt in (1, 2):
            RateLimit.wait()
            try:
                r = self.http.request(method, url, timeout=TIMEOUT, **kw)
            except requests.RequestException as e:
                raise TranslateError(f"{self.title}: нет связи ({e.__class__.__name__}).") from e
            if r.status_code == 429 and attempt == 1:
                # Сервис попросил притормозить: ждём и пробуем ещё раз
                time.sleep(max(3.0, RateLimit.interval * 2))
                continue
            self._check(r)
            return r
        raise AssertionError("unreachable")

    def _check(self, r: requests.Response) -> None:
        if r.status_code < 400:
            return
        hint = {
            401: "неверный API-ключ",
            403: "доступ запрещён — проверьте ключ и регион",
            429: "слишком много запросов или закончился лимит",
            456: "закончился месячный лимит символов",
        }.get(r.status_code, "")
        text = r.text.lstrip()
        # HTML-страницы ошибок не показываем — в них нет ничего полезного
        body = "" if text.startswith("<") else text[:200].replace("\n", " ")
        raise TranslateError(f"{self.title}: ошибка {r.status_code} — {hint}\n{body}".strip(), r.status_code)
