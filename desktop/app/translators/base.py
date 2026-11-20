from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field

import requests

from ..glossary import GlossaryEntry
from ..i18n import tr

TIMEOUT = 30
# Временные сбои сервиса: перегружен, ошибка на его стороне. Такие запросы повторяются с паузой.
RETRY_STATUS = {500, 502, 503, 504, 529}
RETRIES = 3                 # всего попыток на один запрос


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
    # Итоги перевода, кроме самих переводов (заполняет переводчик). Хранятся в запросе, а не в
    # переводчике: Студия переводит одним переводчиком в нескольких потоках сразу.
    detected: str = ""                    # язык оригинала, если переводчик определял его сам (код ISO)
    terms: list[dict] = field(default_factory=list)   # новые имена и термины от ИИ-переводчика


class Translator:
    name = "base"
    title = tr("Базовый")
    uses_llm = False
    timeout = TIMEOUT       # сколько ждать ответа на один запрос, секунды

    def __init__(self, settings: dict):
        self.settings = settings or {}
        self.http = requests.Session()

    def translate(self, req: TranslateRequest) -> list[str]:
        raise NotImplementedError

    # ---------------------------------------------------------------- утилиты
    def _post(self, url: str, **kw) -> requests.Response:
        return self._request("post", url, **kw)

    def _get(self, url: str, **kw) -> requests.Response:
        return self._request("get", url, **kw)

    def _request(self, method: str, url: str, **kw) -> requests.Response:
        """Запрос с повторами при временных сбоях.

        Раньше повторялся только ответ «слишком много запросов», а перегрузка сервиса
        (у Gemini и Claude такое бывает) или оборванное соединение сразу давали ошибку
        на всю страницу.
        """
        for attempt in range(1, RETRIES + 1):
            RateLimit.wait()
            try:
                r = self.http.request(method, url, timeout=self.timeout, **kw)
            except requests.Timeout as e:
                # долгое ожидание не повторяем: это ещё столько же секунд
                raise TranslateError(tr("{0}: сервис не ответил за {1} с.", self.title, self.timeout)) from e
            except requests.RequestException as e:
                if attempt < RETRIES:
                    time.sleep(1.5 * attempt)       # оборвалась связь — пробуем ещё раз
                    continue
                raise TranslateError(tr("{0}: нет связи ({1}).", self.title, e.__class__.__name__)) from e
            if r.status_code == 429 and attempt == 1:
                # Сервис попросил притормозить: ждём и пробуем ещё раз
                time.sleep(max(3.0, RateLimit.interval * 2))
                continue
            if r.status_code in RETRY_STATUS and attempt < RETRIES:
                time.sleep(self._retry_delay(r, attempt))
                continue
            self._check(r)
            return r
        raise AssertionError("unreachable")

    @staticmethod
    def _retry_delay(r: requests.Response, attempt: int) -> float:
        """Пауза перед повтором: сколько попросил сервис (Retry-After), но не больше 10 с."""
        try:
            asked = float(r.headers.get("Retry-After", ""))
        except (TypeError, ValueError):
            asked = 0.0
        return min(10.0, max(asked, 2.0 * attempt))

    def _check(self, r: requests.Response) -> None:
        if r.status_code < 400:
            return
        hint = {
            401: tr("неверный API-ключ"),
            403: tr("доступ запрещён — проверьте ключ и регион"),
            429: tr("слишком много запросов или закончился лимит"),
            456: tr("закончился месячный лимит символов"),
            500: tr("ошибка на стороне сервиса — попробуйте позже"),
            502: tr("ошибка на стороне сервиса — попробуйте позже"),
            503: tr("сервис перегружен — попробуйте позже"),
            504: tr("сервис не успел ответить — попробуйте позже"),
            529: tr("сервис перегружен — попробуйте позже"),
        }.get(r.status_code, "")
        text = r.text.lstrip()
        # HTML-страницы ошибок не показываем — в них нет ничего полезного
        body = "" if text.startswith("<") else text[:200].replace("\n", " ")
        raise TranslateError(tr("{0}: ошибка {1} — {2}\n{3}",
                                self.title, r.status_code, hint, body).strip(), r.status_code)
