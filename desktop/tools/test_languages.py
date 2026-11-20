"""Языки на латинице, определение языка переводчиком и авто-глоссарий — проверка без сети.

    python tools/test_languages.py out/

* что уходит каждому переводчику, когда язык определяет он сам (и как читается ответ) —
  запросы перехватываются, в сеть ничего не отправляется;
* распознавание Windows для языков на латинице без своего пакета (подходит английский);
* какой язык получает облако; автоопределение языка для латиницы;
* авто-глоссарий: ИИ-переводчик (подставной) называет термины, программа добавляет только
  настоящие, без повторов, и не трогает глоссарий с обычными переводчиками.

Код возврата 1, если что-то не так. Отчёт — в report_languages.txt.
"""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PIL import Image  # noqa: E402

from app import autolang, config, languages  # noqa: E402
from app import pipeline as pipeline_module  # noqa: E402
from app.glossary import Glossary, GlossaryEntry  # noqa: E402
from app.models import Box, TextLine  # noqa: E402
from app.ocr.base import OcrBackend  # noqa: E402
from app.ocr.windows_ocr import find_ocr_tag  # noqa: E402
from app.translators import TRANSLATORS  # noqa: E402
from app.translators.base import TranslateRequest, Translator  # noqa: E402

out = Path(sys.argv[1] if len(sys.argv) > 1 else "out")
out.mkdir(parents=True, exist_ok=True)
rows: list[tuple[bool, str, str]] = []


def check(name: str, ok: bool, details: str = "") -> None:
    rows.append((ok, name, details))
    print(f"{'OK  ' if ok else 'FAIL'} {name:62} {details}", flush=True)


# ---------------------------------------------------------------- переводчики: «определи язык сам»

class FakeResponse:
    def __init__(self, data):
        self._data = data
        self.status_code = 200
        self.text = json.dumps(data)

    def json(self):
        return self._data


# что вернул бы сервис, когда язык определяет он сам (по документации сервисов)
REPLIES = {
    "google_free": [[["Привет", "Hola", None, None]], None, "es"],
    "deepl": {"translations": [{"detected_source_language": "ES", "text": "Привет"}]},
    "microsoft": [{"detectedLanguage": {"language": "es", "score": 1.0}, "translations": [{"text": "Привет"}]}],
    "yandex": {"code": 200, "lang": "es-ru", "text": ["Привет"]},
    "duckduckgo": {"detected_language": "es", "translated": "Привет"},
    "lingva": {"translation": "Привет", "info": {"detectedSource": "es"}},
    "papago": {"message": {"result": {"srcLangType": "es", "translatedText": "Привет"}}},
}
SETTINGS = {"deepl": {"api_key": "x:fx"}, "microsoft": {"api_key": "x"}, "papago": {"client_id": "a", "client_secret": "b"}}


def capture(name: str, src: str):
    """Перевести одну строку, перехватив запрос. Возвращает (запрос, перевод, определённый язык)."""
    cls = TRANSLATORS[name]
    tr = cls(SETTINGS.get(name, {}))
    seen: dict = {}

    def fake(method, url, **kw):
        if "duckduckgo.com/" == url.split("://")[1][:15] and "translation.js" not in url:
            return type("R", (), {"text": "vqd='4-123'", "status_code": 200})()
        seen.update(url=url, **kw)
        return FakeResponse(REPLIES[name])

    tr._request = fake
    res = tr.translate(TranslateRequest(["Hola"], src, "ru"))
    return seen, res, tr.detected


def source_param(name: str, seen: dict):
    """Какой язык оригинала ушёл сервису (None — не указан, сервис определит сам)."""
    params, body, data = seen.get("params") or {}, seen.get("json") or {}, seen.get("data")
    if name == "google_free":
        return params.get("sl")
    if name == "deepl":
        return body.get("source_lang")
    if name in ("microsoft", "duckduckgo"):
        return params.get("from")
    if name == "yandex":
        return dict(data).get("lang") if data else None
    if name == "lingva":
        return seen["url"].split("/api/v1/")[1].split("/")[0]
    if name == "papago":
        return body.get("source")
    return "?"


def translators() -> None:
    want_auto = {"google_free": "auto", "deepl": None, "microsoft": None, "yandex": "ru",
                 "duckduckgo": None, "lingva": "auto", "papago": "auto"}
    want_es = {"google_free": "es", "deepl": "ES", "microsoft": "es", "yandex": "es-ru",
               "duckduckgo": "es", "lingva": "es", "papago": "es"}
    for name in REPLIES:
        seen, res, det = capture(name, "latin")
        sent = source_param(name, seen)
        check(f"{name}: латиница — язык определяет сервис", sent == want_auto[name] and det.lower().startswith("es")
              and res == ["Привет"], f"ушло {sent!r}, сервис определил {det!r}")
        seen, res, det = capture(name, "es")
        sent = source_param(name, seen)
        check(f"{name}: выбран испанский — язык указан", sent == want_es[name] and det == "",
              f"ушло {sent!r}")
    # филиппинский: DeepL его не знает — понятная ошибка, а не тихий неверный перевод
    from app.translators.base import TranslateError
    try:
        capture("deepl", "fil")
        check("deepl: филиппинский — понятная ошибка", False, "ошибки не было")
    except TranslateError as e:
        check("deepl: филиппинский — понятная ошибка", "не поддерживает" in str(e), str(e)[:60])
    seen, _res, _det = capture("google_free", "fil")
    check("google: филиппинский уходит как tl", source_param("google_free", seen) == "tl")


# ---------------------------------------------------------------- распознавание и языки облаков

def ocr_and_detect() -> None:
    cases = [("es", ["en-US", "ru"], "en-US"), ("de", ["de-DE", "en-US"], "de-DE"), ("fil", ["en-GB"], "en-GB"),
             ("latin", ["fr-FR"], "fr-FR"), ("id", ["ru"], None), ("ko", ["en-US"], None), ("ko", ["ko", "en-US"], "ko")]
    for lang, have, want in cases:
        got = find_ocr_tag(lang, have)
        check(f"Windows OCR: {lang} при пакетах {have}", got == want, f"→ {got}")
    det = [("Hola, ¿dónde estás?", "es", "es"), ("Hola, ¿dónde estás?", "ko", "latin"),
           ("어디 가?", "es", "ko"), ("Selamat pagi", "latin", "latin"), ("Good morning", "en", "en")]
    for text, sel, want in det:
        got = languages.detect(text, sel)
        check(f"язык облака: {text[:14]!r} при выбранном {sel}", got == want, f"→ {got}")
    got, _ = autolang.choose({"ko": "", "ja": "", "zh": "", "zh-tw": "",
                              "latin": "Selamat pagi, apa kabar hari ini teman"}, "ko")
    check("Авто-язык: текст на латинице — язык определит переводчик", got == "latin", f"→ {got}")
    check("названия языков от переводчиков", [languages.name_of(c) for c in ("ES", "pt-BR", "tl", "xx")]
          == ["Испанский", "Португальский", "Филиппинский", "xx"])


# ---------------------------------------------------------------- авто-глоссарий

PAGES = [
    ["강진우는 북쪽 성문 길드에서 민준이를 만났다.", "민준아! 헌터 협회에 가자!"],
    ["민준은 헌터 협회에 갔다.", "용의 심장을 찾아라!"],
]
TERMS = [
    [{"source": "민준", "target": "Минджун", "note": "мужчина, друг"},
     {"source": "헌터 협회", "target": "Ассоциация охотников", "note": "организация"},
     {"source": "북쪽 성문 길드", "target": "Гильдия Северных врат"},
     {"source": "가자!", "target": "Пошли!"},                     # фраза
     {"source": "강진우", "target": "Кан Джин У"}],               # уже есть (запись человека важнее)
    [{"source": "민준이", "target": "Минджун"},                    # повтор с частицей
     {"source": "용의 심장", "target": "Сердце дракона", "note": "предмет"},
     {"source": "드래곤 하트", "target": "Сердце дракона"}],         # в тексте такого нет
]


class FakeLLM(Translator):
    name = "fake_llm"
    uses_llm = True

    def __init__(self, settings):
        super().__init__(settings)
        self.page = 0
        self.requests: list[TranslateRequest] = []

    def translate(self, req):
        self.requests.append(req)
        self.terms = TERMS[self.page] if req.collect_terms else []
        self.detected = ""
        return [f"<{t[:4]}>" for t in req.texts]


class FakeMT(FakeLLM):
    name = "fake_mt"
    uses_llm = False


class TextOcr(OcrBackend):
    name = "fake"
    lines: list[str] = []

    def recognize(self, image, lang):
        return [TextLine(Box(40, 40 + 120 * i, 400, 30), t) for i, t in enumerate(self.lines)]


def glossary() -> None:
    gdir = out / "glossaries"
    shutil.rmtree(gdir, ignore_errors=True)
    gdir.mkdir(parents=True)
    pipeline_module.GLOSSARY_DIR = gdir
    Glossary(name="Тайтл", source_lang="ko", auto=True,
             entries=[GlossaryEntry("강진우", "Кан Джину", "главный герой")]).save(gdir / "title.json")
    shutil.copy(Path(__file__).resolve().parents[1] / "glossaries" / "universal_ko.json", gdir / "universal_ko.json")
    uni_before = (gdir / "universal_ko.json").read_bytes()

    cfg_path = out / "config_languages.json"
    cfg_path.unlink(missing_ok=True)
    cfg = config.Config(cfg_path)
    cfg.update({"source_lang": "ko", "glossary": "title.json", "context_size": 0, "request_interval": 0,
                "ocr_mode": "fast", "skip_cut_bubbles": False, "content_mode": "screen"})
    pipe = pipeline_module.Pipeline(cfg)
    ocr = TextOcr(cfg)
    pipe._ocr, pipe._ocr_name = ocr, cfg["ocr_engine"]
    llm = FakeLLM({})
    pipe._translator, pipe._translator_key = llm, cfg["translator"]
    img = Image.new("RGB", (900, 400), "white")

    added = []
    for i, page in enumerate(PAGES):
        llm.page, ocr.lines = i, page
        r = pipe.process(img)
        added.append(r.glossary_added)
    g = Glossary.load(gdir / "title.json")
    auto = [(e.source, e.target) for e in g.entries if e.auto]
    want = [("민준", "Минджун"), ("헌터 협회", "Ассоциация охотников"), ("북쪽 성문 길드", "Гильдия Северных врат"),
            ("용의 심장", "Сердце дракона")]
    check("авто-глоссарий: добавлены только настоящие термины", auto == want, f"{auto}")
    check("авто-глоссарий: число добавленных по страницам", added == [3, 1], f"{added}")
    check("авто-глоссарий: запись человека не перезаписана",
          next(e.target for e in g.entries if e.source == "강진우") == "Кан Джину")
    check("авто-глоссарий: ИИ просили называть термины", all(r.collect_terms for r in llm.requests))
    sent = [e.source for e in llm.requests[-1].glossary]
    check("авто-глоссарий: найденное уже уходит переводчику как глоссарий",
          "민준" in sent and "헌터 협회" in sent, f"{sent}")
    data = json.loads((gdir / "title.json").read_text(encoding="utf-8"))
    check("файл: у записей человека нет лишних полей", "auto" not in data["entries"][0] and data.get("auto") is True)

    # обычный переводчик — глоссарий только используется
    before = (gdir / "title.json").read_bytes()
    mt = FakeMT({})
    pipe._translator = mt
    pipe.cache.clear()
    llm.page, ocr.lines = 1, ["민준은 새로운 검을 얻었다."]
    pipe.process(img)
    check("обычный переводчик: глоссарий не пополняется",
          (gdir / "title.json").read_bytes() == before and not mt.requests[-1].collect_terms)

    # авто выключено — не пополняется
    g = Glossary.load(gdir / "title.json")
    g.auto = False
    g.save()
    pipe._translator = llm
    pipe.cache.clear()
    ocr.lines = ["민준은 용의 심장을 들었다."]
    r = pipe.process(img)
    check("«пополнять сам» выключено: ИИ не просят называть термины",
          not llm.requests[-1].collect_terms and r.glossary_added == 0)

    # универсальный глоссарий никогда не пополняется
    cfg["glossary"] = "universal_ko.json"
    g = Glossary.load(gdir / "universal_ko.json")
    g.auto = True       # даже если флаг кто-то поставил вручную
    g.save()
    uni_before = (gdir / "universal_ko.json").read_bytes()
    pipe.cache.clear()
    llm.page, ocr.lines = 0, PAGES[0]
    pipe.process(img)
    check("универсальный глоссарий не пополняется",
          (gdir / "universal_ko.json").read_bytes() == uni_before and not llm.requests[-1].collect_terms)

    # латиница: ИИ просят определить язык, программа показывает, какой
    from app.translators.llm import build_messages, parse_reply
    system, _ = build_messages(TranslateRequest(["Hola amigo"], "latin", "ru", collect_terms=True))
    check("ИИ: при латинице просят назвать язык и термины",
          '"source_language"' in system and '"terms"' in system)
    trans, det, terms = parse_reply('{"translations": ["Привет"], "source_language": "es", "terms": []}', 1)
    check("ИИ: ответ с языком и терминами читается", trans == ["Привет"] and det == "es" and terms == [])


def glossary_speed() -> None:
    """Поиск терминов: тот же результат, что у простого способа «шаблон на каждую запись», но быстро."""
    import random
    import re
    import time

    def slow_find(g, texts):
        joined = "\n".join(texts)
        return [e.source for e in g._sorted() if re.compile(g._term(e), re.I).search(joined)]

    def slow_anchor(g, text):
        for e in g._sorted():
            text = re.compile(g._term(e) + r"(?![하했한할해합함됐된될되돼])", re.I).sub(
                lambda _m, t=e.target: t, text)
        return text

    random.seed(7)
    diffs = pages = 0
    for name in ("universal_ko.json", "universal_en.json"):
        g = Glossary.load(Path(__file__).resolve().parents[1] / "glossaries" / name)
        srcs = [e.source for e in g.entries]
        for _ in range(200):
            words = random.sample(srcs, random.randint(1, 6))
            glue = random.choice([" ", "", "가 ", "님이 ", "s ", "했다 ", "keeper ", ", "])
            texts = [glue.join(words) + random.choice(["!", "했다", "", "s"])]
            pages += 1
            diffs += slow_find(g, texts) != [e.source for e in g.find(texts)]
            diffs += any(slow_anchor(g, t) != g.apply_anchors(t) for t in texts)
    check("глоссарий: поиск и подстановка — как у простого способа", diffs == 0, f"страниц {pages}, расхождений {diffs}")

    # японский и китайский: пробелов нет — имя посреди фразы тоже должно находиться
    cjk = Glossary(entries=[GlossaryEntry("田中", "Танака"), GlossaryEntry("王都", "столица"),
                            GlossaryEntry("王", "король"), GlossaryEntry("민준", "Минджун")])
    cases = [("強くなった田中さん", ["田中"]), ("我们去王都吧", ["王都"]), ("大王国很大", []), ("王说", ["王"]),
             ("어제 민준이 왔다", ["민준"]), ("인민준비", [])]
    got = [[e.source for e in cjk.find([t])] for t, _ in cases]
    check("глоссарий: японские и китайские имена посреди фразы", got == [w for _, w in cases], f"{got}")
    check("глоссарий: подстановка посреди японской фразы", cjk.apply_anchors("強くなった田中さん") == "強くなったТанакаさん")

    syl = "가나다라마바사아자차카타파하강진우민준서연지호"
    big = Glossary(entries=[GlossaryEntry("".join(random.choice(syl) for _ in range(random.randint(2, 4))), "x")
                            for _ in range(1500)])
    texts = ["강진우는 북쪽 성문 길드에서 민준이를 만났다. 어디 가는 거야?"] * 8
    big.find(texts)
    t = time.perf_counter()
    for _ in range(10):
        big.find(texts)
        [big.apply_anchors(x) for x in texts]
    ms = (time.perf_counter() - t) * 100
    check("глоссарий на 1500 записей: поиск на странице быстрый", ms < 50, f"{ms:.1f} мс на страницу (раньше ~1200 мс)")


# ---------------------------------------------------------------- сбои сервисов

class Resp:
    def __init__(self, status: int, data=None, headers=None):
        self.status_code, self._data, self.headers = status, data, headers or {}
        self.text = json.dumps(data) if data is not None else ""

    def json(self):
        return self._data


def services() -> None:
    import requests

    from app.translators import base as tb
    from app.translators.base import TranslateError
    from app.translators.llm import ClaudeTranslator
    from app.translators.machine import GoogleFreeTranslator

    sleeps: list[float] = []
    real_sleep = tb.time.sleep
    tb.time.sleep = lambda s: sleeps.append(round(s, 1))
    tb.RateLimit.interval = 0
    try:
        def run(answers):
            """Перевести через Google, когда сервер отвечает по очереди answers."""
            sleeps.clear()
            tr = GoogleFreeTranslator({})
            it = iter(answers)

            def fake(method, url, **kw):
                a = next(it)
                if isinstance(a, Exception):
                    raise a
                return a
            tr.http.request = fake
            return tr.translate(TranslateRequest(["Hola"], "es", "ru"))

        ok = Resp(200, [[["Привет", "Hola"]], None, "es"])
        check("сбой 503, 503, потом ответ — перевод есть", run([Resp(503), Resp(503), ok]) == ["Привет"],
              f"паузы {sleeps}")
        run([Resp(529, headers={"Retry-After": "30"}), ok])
        check("сервис просит подождать 30 с — ждём не больше 10", sleeps == [10.0], f"паузы {sleeps}")
        check("оборвалась связь, потом ответ — перевод есть",
              run([requests.ConnectionError("reset"), ok]) == ["Привет"], f"паузы {sleeps}")
        try:
            run([Resp(503)] * 3)
            check("сервис лежит — понятная ошибка", False, "ошибки не было")
        except TranslateError as e:
            check("сервис лежит — понятная ошибка", "перегружен" in str(e) and len(sleeps) == 2, str(e)[:60])
        try:
            run([requests.Timeout("slow"), ok])
            check("долгое ожидание не повторяется", False, "ошибки не было")
        except TranslateError as e:
            check("долгое ожидание не повторяется", "не ответил" in str(e) and not sleeps, str(e)[:60])

        # ИИ: ответ оборвался на длинной странице — страница переводится по частям
        calls: list[int] = []

        class FakeClaude(ClaudeTranslator):
            def _post(self, url, **kw):
                texts = json.loads(kw["json"]["messages"][0]["content"].split("translate (")[1].split(":", 1)[1])
                calls.append(len(texts))
                if len(texts) > 3:      # длинная страница: ответ обрывается
                    return Resp(200, {"stop_reason": "max_tokens",
                                      "content": [{"type": "text", "text": '{"translations": ["a", "b'}]})
                return Resp(200, {"stop_reason": "end_turn", "content": [{"type": "text", "text": json.dumps(
                    {"translations": [f"<{x}>" for x in texts], "terms": [{"source": x, "target": x} for x in texts]})}]})

        texts = [f"line {i}" for i in range(10)]
        tr = FakeClaude({"api_key": "k"})
        res = tr.translate(TranslateRequest(texts, "en", "ru", collect_terms=True))
        check("ИИ: оборванный ответ — страница переведена по частям, порядок верный",
              res == [f"<{t}>" for t in texts] and len(tr.terms) == 10, f"запросы по {calls}")
        calls.clear()

        class Short(ClaudeTranslator):
            def _post(self, url, **kw):
                n = kw["json"]["messages"][0]["content"].count('"line')
                calls.append(n)
                items = ["x"] * (n if n == 1 else n - 1)        # длинный ответ теряет одну строку
                return Resp(200, {"stop_reason": "end_turn",
                                  "content": [{"type": "text", "text": json.dumps({"translations": items})}]})

        res = Short({"api_key": "k"}).translate(TranslateRequest(texts[:4], "en", "ru"))
        check("ИИ: в ответе не все переводы — дозапрос по частям", res == ["x"] * 4, f"запросы по {calls}")
    finally:
        tb.time.sleep = real_sleep

    # запасные бесплатные переводчики
    from app.translators import TRANSLATORS

    class Down(Translator):
        title = "Google (без ключа)"

        def translate(self, req):
            raise TranslateError("Google: ошибка 429")

    class Up(Translator):
        title = "Яндекс"

        def translate(self, req):
            return [f"[Я] {t}" for t in req.texts]

    saved = dict(TRANSLATORS)
    cfg_path = out / "config_fallback.json"
    cfg_path.unlink(missing_ok=True)
    cfg = config.Config(cfg_path)
    cfg.update({"source_lang": "en", "context_size": 0, "request_interval": 0, "ocr_mode": "fast",
                "skip_cut_bubbles": False, "content_mode": "screen", "glossary": "", "translator": "google_free"})
    try:
        TRANSLATORS.update({"yandex": Up, "lingva": Up, "duckduckgo": Up})
        pipe = pipeline_module.Pipeline(cfg)
        ocr = TextOcr(cfg)
        ocr.lines = ["Where are you going?"]
        pipe._ocr, pipe._ocr_name = ocr, cfg["ocr_engine"]
        pipe._translator, pipe._translator_key = Down({}), "google_free"
        img = Image.new("RGB", (600, 200), "white")
        r = pipe.process(img)
        got = [b.translation for b in r.bubbles]
        check("бесплатный переводчик не ответил — перевёл другой бесплатный",
              got == ["[Я] Where are you going?"] and "перевёл Яндекс" in r.fallback and not r.error,
              r.fallback or r.error)
        cfg["free_fallback"] = False
        pipe.cache.clear()
        r = pipe.process(img)
        check("запасные выключены — честная ошибка", bool(r.error) and not r.fallback, r.error[:40])
        cfg["free_fallback"] = True
        pipe.cache.clear()
        cfg["translator"] = "deepl"         # переводчик с ключом не подменяем
        pipe._translator, pipe._translator_key = Down({}), "deepl"
        r = pipe.process(img)
        check("переводчик с ключом не подменяется бесплатным", bool(r.error) and not r.fallback, r.error[:40])
    finally:
        TRANSLATORS.clear()
        TRANSLATORS.update(saved)


def storage() -> None:
    """Настройки и данные: рядом с программой, а если туда писать нельзя — в папке пользователя."""
    import os
    root = out / "storage"
    shutil.rmtree(root, ignore_errors=True)
    prog, home = root / "prog", root / "appdata"
    (prog / "glossaries").mkdir(parents=True)
    (prog / "titles").mkdir()
    (prog / "config.json").write_text('{"translator": "deepl"}', encoding="utf-8")
    (prog / "titles" / "t.json").write_text("{}", encoding="utf-8")
    (prog / "glossaries" / "g.json").write_text('{"name": "из программы"}', encoding="utf-8")
    (home / "ATlate" / "glossaries").mkdir(parents=True)
    (home / "ATlate" / "glossaries" / "g.json").write_text('{"name": "свой"}', encoding="utf-8")
    old = os.environ.get("APPDATA")
    os.environ["APPDATA"] = str(home)
    try:
        data = config.data_root(prog, writable=lambda _p: False)
    finally:
        if old is None:
            os.environ.pop("APPDATA", None)
        else:
            os.environ["APPDATA"] = old
    moved = (data == home / "ATlate" and json.loads((data / "config.json").read_text())["translator"] == "deepl"
             and (data / "titles" / "t.json").exists()
             and json.loads((data / "glossaries" / "g.json").read_text())["name"] == "свой")
    check("папка программы закрыта для записи — данные в папке пользователя", moved, str(data))
    check("папка программы доступна — всё рядом с программой", config.data_root(prog) == prog)
    c = config.Config(data / "config.json")
    c["translator"] = "gemini"
    c.save()
    check("настройки сохраняются через временный файл",
          json.loads((data / "config.json").read_text())["translator"] == "gemini"
          and not (data / "config.tmp").exists() and not c.save_error)
    bad = config.Config(root / "x.json")
    bad.path = Path("/proc/atlate-no-access/config.json")
    bad.save()
    check("сохранить не удалось — программа не падает", bool(bad.save_error))


def main() -> int:
    translators()
    services()
    storage()
    ocr_and_detect()
    glossary()
    glossary_speed()
    failed = [r for r in rows if not r[0]]
    lines = [f"{'OK  ' if ok else 'FAIL'} {name} {details}" for ok, name, details in rows]
    (out / "report_languages.txt").write_text("\n".join(lines + [f"failed: {len(failed)}"]), encoding="utf-8")
    print(f"failed: {len(failed)}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
