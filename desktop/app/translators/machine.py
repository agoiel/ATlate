"""Обычные машинные переводчики: Google (без ключа), DeepL, Microsoft."""

from __future__ import annotations

import re
import uuid
from urllib.parse import quote

from .. import languages
from ..i18n import tr
from .base import TranslateError, TranslateRequest, Translator


AUTO = languages.AUTO


def _require(code: str, service: str, tgt: str) -> str:
    """Код языка для сервиса; пустой — сервис этот язык не поддерживает."""
    if not code:
        raise TranslateError(tr("{0} не поддерживает перевод на язык «{1}». Выберите другой переводчик.",
                                service, languages.tgt(tgt)['name']))
    return code


def _source(code: str, service: str, src: str) -> str:
    """Код языка оригинала для сервиса. AUTO — пусть сервис определит язык сам; пустой — не поддерживает."""
    if not code:
        raise TranslateError(tr("{0} не поддерживает перевод с языка «{1}». Выберите другой переводчик или язык "
                                "«Латиница (язык определит переводчик)».", service, languages.src(src)['name']))
    return code


class GoogleFreeTranslator(Translator):
    """Бесплатный веб-эндпоинт Google Translate.

    Ключ не нужен, удобно для начала. Это неофициальный способ: Google может
    ограничить его без предупреждения (особенно при VPN) — тогда переключитесь
    на DeepL или Microsoft. Все облака страницы уходят одним запросом.
    """

    name = "google_free"
    title = tr("Google (без ключа)")
    URL = "https://translate.googleapis.com/translate_a/single"

    def _one(self, text: str, sl: str, tl: str) -> str:
        try:
            r = self._get(self.URL, params={"client": "gtx", "sl": sl, "tl": tl, "dt": "t", "q": text})
        except TranslateError as e:
            if e.code == 429:
                raise TranslateError(
                    tr("Google временно ограничил бесплатные запросы с вашего адреса. Чаще всего так бывает "
                       "при включённом VPN. Отключите VPN, подождите несколько минут или выберите другой "
                       "переводчик (DeepL, Gemini)."), 429) from e
            raise
        try:
            data = r.json()
            if sl == AUTO and len(data) > 2 and isinstance(data[2], str):
                self.detected = data[2]         # Google сообщает, какой язык он определил
            return "".join(seg[0] for seg in data[0] if seg and seg[0])
        except (ValueError, TypeError, IndexError) as e:
            raise TranslateError(tr("Google: неожиданный ответ сервера.")) from e

    def translate(self, req: TranslateRequest) -> list[str]:
        self.detected = ""
        texts = [t.replace("\n", " ") for t in (req.anchored or req.texts)]
        sl, tl = languages.src(req.src)["google"], languages.tgt(req.tgt)["google"]
        # делим на порции, чтобы адрес запроса не был слишком длинным
        chunks: list[list[int]] = []
        size = 0
        for i, t in enumerate(texts):
            n = len(quote(t)) + 3
            if not chunks or size + n > 5000:
                chunks.append([])
                size = 0
            chunks[-1].append(i)
            size += n
        out = [""] * len(texts)
        for chunk in chunks:
            parts = [p.strip() for p in self._one("\n".join(texts[i] for i in chunk), sl, tl).split("\n")]
            if len(parts) == len(chunk):
                for i, p in zip(chunk, parts):
                    out[i] = p
            else:
                # Google склеил строки — переводим эту порцию по одной
                for i in chunk:
                    out[i] = self._one(texts[i], sl, tl).strip()
        return out


class DeepLTranslator(Translator):
    """DeepL API. Бесплатный план — 500 000 символов в месяц (ключ оканчивается на :fx)."""

    name = "deepl"
    title = "DeepL"

    def translate(self, req: TranslateRequest) -> list[str]:
        key = self.settings.get("api_key", "").strip()
        if not key:
            raise TranslateError(tr("DeepL: укажите API-ключ в настройках."))
        host = "api-free.deepl.com" if key.endswith(":fx") else "api.deepl.com"
        self.detected = ""
        sl = _source(languages.src(req.src)["deepl"], "DeepL", req.src)
        body = {
            "text": req.anchored or req.texts,
            "target_lang": _require(languages.tgt(req.tgt)["deepl"], "DeepL", req.tgt),
        }
        if sl != AUTO:
            body["source_lang"] = sl        # без него DeepL определяет язык сам
        # Контекст DeepL не переводит и не тарифицирует — передаём прошлые реплики
        if req.history:
            body["context"] = "\n".join(o for o, _ in req.history[-8:])
        r = self._post(f"https://{host}/v2/translate", json=body,
                       headers={"Authorization": f"DeepL-Auth-Key {key}"})
        items = r.json().get("translations", [])
        if sl == AUTO and items:
            self.detected = items[0].get("detected_source_language", "")
        return [t["text"] for t in items]


class MicrosoftTranslator(Translator):
    """Azure AI Translator. Бесплатный уровень F0 — 2 млн символов в месяц."""

    name = "microsoft"
    title = "Microsoft"
    URL = "https://api.cognitive.microsofttranslator.com/translate"

    def translate(self, req: TranslateRequest) -> list[str]:
        key = self.settings.get("api_key", "").strip()
        if not key:
            raise TranslateError(tr("Microsoft: укажите API-ключ в настройках."))
        headers = {"Ocp-Apim-Subscription-Key": key, "Content-Type": "application/json"}
        region = self.settings.get("region", "").strip()
        if region and region != "global":
            headers["Ocp-Apim-Subscription-Region"] = region
        self.detected = ""
        sl = _source(languages.src(req.src)["microsoft"], "Microsoft", req.src)
        params = {"api-version": "3.0",
                  "to": _require(languages.tgt(req.tgt)["microsoft"], "Microsoft", req.tgt)}
        if sl != AUTO:
            params["from"] = sl             # без него Microsoft определяет язык сам
        r = self._post(self.URL, params=params, headers=headers,
                       json=[{"Text": t} for t in (req.anchored or req.texts)])
        items = r.json()
        if sl == AUTO and items:
            self.detected = (items[0].get("detectedLanguage") or {}).get("language", "")
        return [item["translations"][0]["text"] for item in items]


def _join_translate(texts: list[str], max_chars: int, one) -> list[str]:
    """Переводит облака порциями: склеивает через перевод строки и делит ответ обратно."""
    clean = [t.replace("\n", " ") for t in texts]
    chunks: list[list[int]] = []
    size = 0
    for i, t in enumerate(clean):
        if not chunks or size + len(t) + 1 > max_chars:
            chunks.append([])
            size = 0
        chunks[-1].append(i)
        size += len(t) + 1
    out = [""] * len(clean)
    for chunk in chunks:
        parts = [p.strip() for p in one("\n".join(clean[i] for i in chunk)).split("\n") if p.strip()]
        if len(parts) == len(chunk):
            for i, part in zip(chunk, parts):
                out[i] = part
        else:
            for i in chunk:
                out[i] = one(clean[i]).strip()
    return out


def _simple(google_code: str) -> str:
    return "zh" if google_code in ("zh-CN", "zh-TW") else google_code


class YandexTranslator(Translator):
    """Яндекс: без ключа — неофициальный бесплатный способ, с ключом — Yandex Cloud Translate."""

    name = "yandex"
    title = tr("Яндекс")

    def __init__(self, settings: dict):
        super().__init__(settings)
        self.ucid = uuid.uuid4().hex

    def translate(self, req: TranslateRequest) -> list[str]:
        self.detected = ""
        texts = req.anchored or req.texts
        sl = _simple(languages.src(req.src)["google"])
        tl = _simple(languages.tgt(req.tgt)["google"])
        key = self.settings.get("api_key", "").strip()
        if key:
            folder = self.settings.get("folder_id", "").strip()
            if not folder:
                raise TranslateError(tr("Яндекс: для ключа Yandex Cloud укажите ID каталога."))
            body = {"folderId": folder, "targetLanguageCode": tl, "texts": texts}
            if sl != AUTO:
                body["sourceLanguageCode"] = sl     # без него Яндекс определяет язык сам
            r = self._post("https://translate.api.cloud.yandex.net/translate/v2/translate",
                           headers={"Authorization": f"Api-Key {key}"}, json=body)
            items = r.json().get("translations", [])
            if sl == AUTO and items:
                self.detected = items[0].get("detectedLanguageCode", "")
            return [t.get("text", "") for t in items]
        # только язык перевода — тогда Яндекс определяет язык оригинала сам
        pair = tl if sl == AUTO else f"{sl}-{tl}"
        data = [("text", t.replace("\n", " ")) for t in texts] + [("lang", pair)]
        r = self._post("https://translate.yandex.net/api/v1/tr.json/translate",
                       params={"ucid": self.ucid, "srv": "android", "format": "text"}, data=data)
        try:
            js = r.json()
            out = js["text"]
            if sl == AUTO and isinstance(js.get("lang"), str):
                self.detected = js["lang"].split("-")[0]
        except (ValueError, KeyError) as e:
            raise TranslateError(tr("Яндекс: неожиданный ответ. Бесплатный способ мог перестать работать — "
                                    "укажите ключ Yandex Cloud или выберите другой переводчик.")) from e
        return [str(x).strip() for x in out] + [""] * (len(texts) - len(out))


class DuckDuckGoTranslator(Translator):
    """Переводчик DuckDuckGo (внутри — Microsoft Bing). Неофициальный, без ключа."""

    name = "duckduckgo"
    title = "DuckDuckGo"
    UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36"

    def translate(self, req: TranslateRequest) -> list[str]:
        page = self._get("https://duckduckgo.com/", params={"q": "translate", "ia": "web"},
                         headers={"User-Agent": self.UA}).text
        m = re.search(r"vqd=[\"']?([\d-]+)", page)
        if not m:
            raise TranslateError(tr("DuckDuckGo: не удалось начать сессию перевода. Попробуйте позже."))
        self.detected = ""
        sl = _source(languages.src(req.src)["microsoft"], "DuckDuckGo", req.src)
        params = {"vqd": m.group(1), "query": "translate",
                  "to": languages.tgt(req.tgt)["microsoft"] or languages.tgt(req.tgt)["google"]}
        if sl != AUTO:
            params["from"] = sl             # без него язык определяется сам

        def one(text: str) -> str:
            r = self._post("https://duckduckgo.com/translation.js", params=params, data=text.encode("utf-8"),
                           headers={"Content-Type": "text/plain", "User-Agent": self.UA})
            try:
                js = r.json()
                if sl == AUTO and js.get("detected_language"):
                    self.detected = str(js["detected_language"])
                return js["translated"]
            except (ValueError, KeyError) as e:
                raise TranslateError(tr("DuckDuckGo: неожиданный ответ сервера.")) from e

        return _join_translate(req.anchored or req.texts, 1500, one)


class LingvaTranslator(Translator):
    """Lingva — открытый посредник к Google Переводчику. Выручает, когда Google ограничивает запросы."""

    name = "lingva"
    title = "Lingva"
    CODES = {"zh-CN": "zh", "zh-TW": "zh_HANT"}

    def translate(self, req: TranslateRequest) -> list[str]:
        base = (self.settings.get("base_url") or "https://lingva.ml").strip().rstrip("/")
        self.detected = ""
        g_src, g_tgt = languages.src(req.src)["google"], languages.tgt(req.tgt)["google"]
        sl, tl = self.CODES.get(g_src, g_src), self.CODES.get(g_tgt, g_tgt)

        def one(text: str) -> str:
            r = self._get(f"{base}/api/v1/{sl}/{tl}/{quote(text, safe='')}")
            try:
                data = r.json()
            except ValueError as e:
                raise TranslateError(tr("Lingva: сервер ответил не JSON. Попробуйте другой адрес сервера.")) from e
            if "error" in data:
                raise TranslateError(f"Lingva: {data['error']}")
            if sl == AUTO:
                self.detected = (data.get("info") or {}).get("detectedSource", "") or self.detected
            return data.get("translation", "")

        return _join_translate(req.anchored or req.texts, 1500, one)


class PapagoTranslator(Translator):
    """Papago (Naver Cloud) — сильнее всех в корейском. Нужны Client ID и Client Secret."""

    name = "papago"
    title = "Papago"

    def translate(self, req: TranslateRequest) -> list[str]:
        cid = self.settings.get("client_id", "").strip()
        secret = self.settings.get("client_secret", "").strip()
        if not cid or not secret:
            raise TranslateError(tr("Papago: укажите Client ID и Client Secret в настройках."))
        headers = {"X-NCP-APIGW-API-KEY-ID": cid, "X-NCP-APIGW-API-KEY": secret}
        self.detected = ""
        sl, tl = languages.src(req.src)["google"], languages.tgt(req.tgt)["google"]

        def one(text: str) -> str:
            r = self._post("https://papago.apigw.ntruss.com/nmt/v1/translation", headers=headers,
                           json={"source": sl, "target": tl, "text": text})
            try:
                res = r.json()["message"]["result"]
                if sl == AUTO and res.get("srcLangType"):
                    self.detected = res["srcLangType"]
                return res["translatedText"]
            except (ValueError, KeyError) as e:
                raise TranslateError(tr("Papago: неожиданный ответ сервера.")) from e

        return _join_translate(req.anchored or req.texts, 4500, one)
