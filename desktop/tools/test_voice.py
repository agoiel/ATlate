"""«Голос в чат»: конец фразы, выбор языка, распознавание речи, перевод в буфер обмена.

    QT_QPA_PLATFORM=offscreen python tools/test_voice.py out/ [--model ПАПКА] [--audio ПАПКА]

* Без сети и микрофона: конец фразы по громкости, выдумки Whisper на тишине, язык фразы из двух,
  коды языков чата для переводчиков, запрос к ИИ для чата, запасной бесплатный переводчик,
  словарь для чата (склонения, ложные совпадения, подсказка распознаванию, подстановка для обычных
  переводчиков и список для ИИ), подпись клавиши, звуки.
* Вкладки в окне: «Голос» открывает свою страницу, «Скриншот» и «Live» возвращают экранный
  переводчик; словари в списке — с моего языка.
* Модель распознавания: --model — готовая папка; без него быстрая модель скачивается в out/models
  (заодно проверяется скачивание). Фразы — WAV из --audio с именами из CASES; тишина и шум — пусто.
* Вся цепочка в окне: «Проверить» и «рация» (клавиша удерживается) → запись (подменённый микрофон
  отдаёт WAV) → перевод (подменённый переводчик) → буфер обмена и плашка; модель не скачана —
  понятная ошибка; фраза на языке чата — копируется без перевода.
Код возврата 1 при ошибках.
"""

from __future__ import annotations

import os
import sys
import time
import wave
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("ATLATE_NO_UPDATE_CHECK", "1")
os.environ.setdefault("ATLATE_UI_LANG", "ru")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

args = sys.argv[1:]


def _opt(name: str) -> str:
    if name in args:
        i = args.index(name)
        val = args[i + 1]
        del args[i:i + 2]
        return val
    return ""


MODEL_ARG, AUDIO_ARG = _opt("--model"), _opt("--audio")
out = Path(args[0] if args else "out")
out.mkdir(parents=True, exist_ok=True)

import app  # noqa: E402,F401
from app import languages, voice  # noqa: E402

rows: list[tuple[bool, str]] = []
# файл → (язык фразы, слова, которые должны распознаться)
CASES = {
    "en_bridge.wav": ("en", ["help", "bridge"]),
    "en_gg.wav": ("en", ["game", "again"]),
    "ru_wait.wav": ("ru", ["подождите", "вернусь"]),
    "ru_mid.wav": ("ru", ["привет", "мид"]),
}


def check(name: str, ok: bool, details: str = "") -> None:
    rows.append((bool(ok), name))
    print(f"{'OK  ' if ok else 'FAIL'} {name:66} {details}", flush=True)


def load_wav(path: Path) -> np.ndarray:
    with wave.open(str(path)) as w:
        rate, ch, width = w.getframerate(), w.getnchannels(), w.getsampwidth()
        raw = w.readframes(w.getnframes())
    a = np.frombuffer(raw, {2: np.int16, 4: np.int32}[width]).astype(np.float32)
    a /= 32768.0 if width == 2 else 2147483648.0
    if ch > 1:
        a = a.reshape(-1, ch).mean(axis=1)
    return voice.resample(a, rate)


rng = np.random.default_rng(11)


def noise(sec: float, level: float = 0.002) -> np.ndarray:
    return (rng.standard_normal(int(voice.RATE * sec)) * level).astype(np.float32)


def phrase(audio: np.ndarray, level: float = 0.002) -> np.ndarray:
    """Как с микрофона: тишина, фраза, тишина — с шумом."""
    return np.concatenate([noise(0.4, level), audio + noise(len(audio) / voice.RATE, level), noise(1.6, level)])


# ================================================================ без модели
def offline() -> None:
    # конец фразы
    def run(sig: np.ndarray) -> tuple[str, float]:
        e = voice.Endpoint()
        for i in range(0, len(sig), 640):
            st = e.feed(sig[i:i + 640])
            if st != "listen":
                return st, (i + 640) / voice.RATE
        return "listen", len(sig) / voice.RATE

    t = np.arange(int(voice.RATE * 1.5)) / voice.RATE
    talk = (np.sin(2 * np.pi * 220 * t) * (0.5 + 0.5 * np.sin(2 * np.pi * 3 * t)) * 0.1).astype(np.float32)
    st, at = run(np.concatenate([noise(0.5), talk, noise(3)]))
    check("конец фразы: пауза после речи заканчивает запись", st == "done" and 2.6 <= at <= 3.2, f"{st} на {at:.2f} с")
    st, at = run(np.concatenate([noise(0.5, 0.01), talk + noise(1.5, 0.01), noise(3, 0.01)]))
    check("конец фразы: и при шумном вентиляторе", st == "done" and at <= 3.3, f"{st} на {at:.2f} с")
    st, at = run(np.concatenate([noise(0.5, 0.0004), talk * 0.15, noise(3, 0.0004)]))
    check("конец фразы: и при тихом микрофоне", st == "done" and at <= 3.3, f"{st} на {at:.2f} с")
    st, at = run(noise(10, 0.01))
    check("речи нет — запись останавливается через 6 с", st == "silent" and abs(at - voice.NO_SPEECH_WAIT) < 0.1,
          f"{st} на {at:.2f} с")
    st, at = run(np.tile(np.concatenate([talk, noise(0.3)]), 20))
    check("бесконечная речь — не дольше 20 с", st == "done" and abs(at - voice.TAP_LIMIT) < 0.1, f"{st} на {at:.2f} с")

    # выдумки Whisper
    phantoms = ["Редактор субтитров А.Синецкая Корректор А.Егорова", "Продолжение следует...", "Спасибо за просмотр!",
                "Субтитры сделал DimaTorzok", "Thanks for watching!", "...", "♪"]
    real = ["Спасибо за игру", "gg", "Корректор у нас сломался, идём в лес без него", "Thanks, wait for me"]
    check("выдумки на тишине отбрасываются", all(voice.is_phantom(t) for t in phantoms),
          str([t for t in phantoms if not voice.is_phantom(t)]))
    check("обычные фразы — нет", not any(voice.is_phantom(t) for t in real), str([t for t in real if voice.is_phantom(t)]))

    # язык из двух
    ch = voice.choose_language
    ok = (ch({"ru": 0.02, "en": 0.42, "de": 0.3}, "ru", "en") == "en"
          and ch({"ru": 0.9, "en": 0.05}, "ru", "en") == "ru"
          and ch({"de": 0.9}, "ru", "en") == "ru"                   # третий язык — значит, мой
          and ch({"zh": 0.8}, "ru", "zh-tw") == "zh-tw"
          and ch({"en": 0.9}, "en", "en") == "en")
    check("язык фразы выбирается из моего и языка чата", ok)

    # языки переводчиков
    ok = (languages.src("ru")["google"] == "ru" and languages.src("ko")["google"] == "ko"
          and languages.tgt("ko")["deepl"] == "KO" and languages.tgt("zh-tw")["deepl"] == "ZH-HANT"
          and languages.tgt("zh")["microsoft"] == "zh-Hans" and languages.tgt("ru")["google"] == "ru"
          and languages.whisper_code("zh-tw") == "zh")
    check("языки чата: коды для переводчиков и распознавания", ok)
    vl = list(languages.voice_languages())
    check("языки чата: корейский, японский, китайский сразу после английского",
          vl[vl.index("en") + 1:vl.index("en") + 5] == ["ko", "ja", "zh", "zh-tw"]
          and "ko" not in languages.TARGET_LANGUAGES, str(vl[:10]))

    from app.translators import TranslateRequest
    from app.translators.llm import build_messages
    system, user = build_messages(TranslateRequest(texts=["го на мид"], src="ru", tgt="ko", chat=True))
    check("ИИ-переводчик: свой запрос для чата игры", "game chat" in system and "Korean" in system
          and "Russian" in system and "Messages to translate" in user, system.splitlines()[0][:60])

    # запасной переводчик
    from app.config import Config
    from app.translators import TRANSLATORS, TranslateError
    cfg = Config(out / "cfg_fallback.json")
    cfg["translator"] = "google_free"
    saved = dict(TRANSLATORS)

    class Down:
        title = "Down"

        def __init__(self, _s):
            pass

        def translate(self, req):
            raise TranslateError("нет связи")

    class Up(Down):
        title = "Up"

        def translate(self, req):
            assert req.chat and req.src == "ru" and req.tgt == "en"
            return ["hi " + req.texts[0]]

    try:
        TRANSLATORS.update(google_free=Down, yandex=Up, lingva=Down, duckduckgo=Down)
        import app.translators as tmod
        real_create = tmod.create_translator
        tmod.create_translator = lambda name, c: TRANSLATORS[name](None)
        text, by = voice.translate(cfg, "привет", "ru", "en")
        check("бесплатный переводчик не ответил — переводит другой бесплатный", (text, by) == ("hi привет", "Up"),
              f"{text!r} {by}")
    finally:
        TRANSLATORS.clear()
        TRANSLATORS.update(saved)
        tmod.create_translator = real_create

    glossary_checks(cfg)

    from app.hotkeys import key_down, pretty
    check("подпись клавиши: <alt>+v → Alt+V", pretty("<alt>+v") == "Alt+V" and pretty("<ctrl>+<shift>+t") ==
          "Ctrl+Shift+T" and pretty("<f8>") == "F8")
    if sys.platform != "win32":
        check("состояние клавиши вне Windows неизвестно", key_down("<alt>+v") is None)
    tones = [voice.tone(k) for k in ("start", "done", "error")]
    check("звуки: короткие и негромкие", all(0.1 < len(x) / 44100 < 0.4 and np.abs(x).max() <= 0.25 for x in tones))
    check("8 кГц → 16 кГц и 48 кГц → 16 кГц", len(voice.resample(np.zeros(8000), 8000)) == 16000
          and len(voice.resample(np.zeros(48000), 48000)) == 16000)


def glossary_checks(cfg) -> None:
    from app.config import GLOSSARY_DIR
    from app.glossary import Glossary, GlossaryEntry

    g = Glossary(name="t", source_lang="ru", target_lang="en", entries=[GlossaryEntry(a, b) for a, b in [
        ("мид", "mid"), ("катка", "match"), ("лес", "jungle"), ("пуш", "push"), ("топ", "top"), ("го", "go"),
        ("гг", "gg"), ("гг вп", "gg wp"), ("ульта", "ult")]])
    cases = {
        "Го на мид!": "go на mid!",
        "Ещё одну катку?": "Ещё одну match?",
        "Встретимся в лесу": "Встретимся в jungle",
        "Мидом пойдём, ульту не трать": "mid пойдём, ult не трать",
        "гг вп всем": "gg wp всем",
        "Пушка сломалась": "Пушка сломалась",          # не «push»
        "Не надо топать": "Не надо топать",            # не «top»
        "В этом году": "В этом году",                  # «го» — только целым словом
        "лестница": "лестница",
    }
    bad = {t: voice.apply_terms(t, voice.find_terms(g, t)) for t in cases}
    bad = {t: r for t, r in bad.items() if r != cases[t]}
    check("словарь: склонения находятся, похожие слова — нет", not bad, str(bad))
    hw = voice.hotwords("ru", ["катка", "Мид", "дпс"])
    check("словарь: его слова подсказываются распознаванию, без повторов", hw and "дпс" in hw
          and hw.lower().count("мид") == 1 and len(hw) <= 302, repr(hw))
    check("словарь: подсказка не длиннее ~300 знаков", len(voice.hotwords("ru", [f"слово{i}" for i in range(200)])) <= 302)
    check("словарь подходит только к своим языкам", voice.glossary_fits(g, "ru", "en")
          and not voice.glossary_fits(g, "ru", "ko") and not voice.glossary_fits(g, "en", "en"))

    bundled = Glossary.load(ROOT / "glossaries" / "game_ru_en.json")
    check("встроенный словарь игрового сленга: русский → английский", bundled.source_lang == "ru"
          and bundled.target_lang == "en" and len(bundled.entries) >= 30
          and voice.apply_terms("го мид", voice.find_terms(bundled, "го мид")) == "go mid", f"{len(bundled.entries)} слов")
    cfg["voice_glossary"] = "game_ru_en.json"
    if (GLOSSARY_DIR / "game_ru_en.json").exists():
        check("выбранный словарь берётся, только если языки совпадают",
              voice.chat_glossary(cfg, "ru", "en") is not None and voice.chat_glossary(cfg, "ru", "ja") is None)

    # обычному переводчику — подстановка в текст, ИИ — список в запросе
    seen = {}

    class Machine:
        title, uses_llm = "M", False

        def __init__(self, _s):
            pass

        def translate(self, req):
            seen["m"] = (list(req.anchored or []), [e.source for e in req.glossary])
            return ["ok"]

    class Llm(Machine):
        title, uses_llm = "L", True

        def translate(self, req):
            seen["l"] = (req.anchored, sorted(e.source for e in req.glossary))
            return ["ok"]

    import app.translators as tmod
    real_create = tmod.create_translator
    try:
        tmod.create_translator = lambda name, c: Machine(None)
        voice.translate(cfg, "Го на мид, катку выиграем", "ru", "en", g)
        tmod.create_translator = lambda name, c: Llm(None)
        voice.translate(cfg, "Го на мид, катку выиграем", "ru", "en", g)
    finally:
        tmod.create_translator = real_create
    check("обычный переводчик получает фразу со словами из словаря", seen.get("m", ([""],))[0] ==
          ["go на mid, match выиграем"], str(seen.get("m")))
    check("ИИ-переводчик получает слова словаря списком, фразу — как есть",
          seen.get("l") == (None, ["го", "катка", "мид"]), str(seen.get("l")))


def tabs() -> None:
    """Вкладки «Скриншот | Live | Голос» в настоящем окне."""
    import tempfile

    from PySide6.QtWidgets import QApplication

    from app.config import Config
    from app.ui.main_window import MainWindow

    qa = QApplication.instance() or QApplication(sys.argv)
    cfg = Config(Path(tempfile.mkdtemp()) / "config.json")
    cfg.update(translate_mode="shot", ui_page="screen", voice_my_lang="ru", voice_chat_lang="en")
    win = MainWindow(cfg)
    win.resize(1000, 900)
    win.show()
    for _ in range(5):
        qa.processEvents()
    check("сначала открыт экранный переводчик", win.screen_page.isVisible() and not win.voice_page.isVisible()
          and win.screen_actions.isVisible() and not win.voice_actions.isVisible())
    win._on_mode_switch("voice")
    for _ in range(5):
        qa.processEvents()
    check("«Голос»: своя страница и свои кнопки в шапке, режим перевода не тронут",
          win.voice_page.isVisible() and not win.screen_page.isVisible() and win.voice_actions.isVisible()
          and cfg["ui_page"] == "voice" and cfg["translate_mode"] == "shot"
          and win.mode_switch.buttons["voice"].isChecked())
    if voice.available():
        names = [win.voice_gloss_combo.itemText(i) for i in range(win.voice_gloss_combo.count())]
        check("словари в списке — с моего языка", any("слов" in n and "сленг" in n.lower() for n in names)
              and not any("корейск" in n.lower() for n in names), str(names))
    win._rebuild()
    for _ in range(5):
        qa.processEvents()
    check("после пересборки окна вкладка «Голос» остаётся", win.voice_page.isVisible())
    win._on_mode_switch("live")
    for _ in range(5):
        qa.processEvents()
    check("«Live» возвращает экранный переводчик и включает режим Live",
          win.screen_page.isVisible() and not win.voice_page.isVisible() and cfg["ui_page"] == "screen"
          and cfg["translate_mode"] == "live" and win.mode_switch.buttons["live"].isChecked())
    win.close()


# ================================================================ модель
def model() -> Path | None:
    if MODEL_ARG:
        d = Path(MODEL_ARG)
        voice.MODELS_DIR = d.parent
        if d.name != "whisper-base":
            voice.model_dir = lambda name, d=d: d
        return d
    voice.MODELS_DIR = out / "models"
    if voice.model_ready("base"):
        return voice.model_dir("base")
    seen = []
    t0 = time.time()
    try:
        voice.download_model("base", lambda a, b: seen.append((a, b)))
    except voice.VoiceError as e:
        check("модель распознавания скачивается", False, str(e))
        return None
    ok = voice.model_ready("base") and seen and seen[-1][0] == seen[-1][1]
    check("модель распознавания скачивается", ok, f"{seen[-1][0] / 1e6:.0f} МБ за {time.time() - t0:.0f} с")
    return voice.model_dir("base") if ok else None


def recognition(audio_dir: Path | None) -> None:
    rec = voice.Recognizer()
    for name, sig in (("тишина", np.zeros(voice.RATE * 2, np.float32)), ("шум", noise(3, 0.02)),
                      ("гул 50 Гц", (np.sin(2 * np.pi * 50 * np.arange(voice.RATE * 3) / voice.RATE) * 0.05
                                     ).astype(np.float32))):
        h = rec.recognize(sig, "base", "ru", "en")
        check(f"{name}: ничего не выдумано", h.text == "", repr(h.text))
    if audio_dir is None:
        return
    for fname, (lang, words) in CASES.items():
        path = audio_dir / fname
        if not path.exists():
            continue
        t0 = time.time()
        h = rec.recognize(phrase(load_wav(path)), "base", "ru", "en")
        low = h.text.lower()
        check(f"{fname}: язык и слова", h.lang == lang and all(w in low for w in words),
              f"{h.lang} {h.text!r} {time.time() - t0:.1f} с")


# ================================================================ вся цепочка в окне
class FakeRecorder:
    """Микрофон, который «слышит» заданный звук — кусками, как настоящий."""
    audio = np.zeros(0, np.float32)

    def __init__(self, _device=""):
        self.pos = 0

    def start(self):
        self.t0 = time.monotonic()

    def take(self):
        upto = min(len(self.audio), int((time.monotonic() - self.t0) * voice.RATE * 4))   # вчетверо быстрее
        chunk, self.pos = self.audio[self.pos:upto], max(self.pos, upto)
        return chunk

    def stop(self):
        return self.audio[:max(self.pos, 1)]


def chain(audio_dir: Path | None, have_model: bool) -> None:
    from PySide6.QtWidgets import QApplication

    from app.config import Config
    from app.ui import voice_chat
    from app.ui.voice_chat import VoiceController

    qa = QApplication.instance() or QApplication(sys.argv)
    cfg = Config(out / "cfg_voice.json")
    cfg.update(voice_enabled=True, voice_my_lang="ru", voice_chat_lang="en", voice_sounds=False, voice_model="base",
               voice_glossary="game_ru_en.json")
    voice.Recorder = FakeRecorder
    calls = []

    def fake_translate(c, text, src, tgt, glossary=None):
        calls.append((text, src, tgt, glossary))
        return f"[{tgt}] {text}", "Тест"

    voice.translate = fake_translate
    ctl = VoiceController(cfg)
    results, errors = [], []
    ctl.finished.connect(results.append)
    ctl.failed.connect(errors.append)

    def wait(cond, sec: float = 60.0) -> bool:
        end = time.time() + sec
        while time.time() < end:
            qa.processEvents()
            if cond():
                return True
            time.sleep(0.01)
        return False

    # модели нет — понятная ошибка
    real_ready = voice.model_ready
    voice.model_ready = lambda name: False
    ctl.press("")
    voice.model_ready = real_ready
    check("модель не скачана — плашка с подсказкой, запись не начинается",
          ctl.state == "idle" and errors and "скачайте" in errors[-1] and ctl.toast.kind == "error", errors[-1:])
    if not have_model:
        return
    wav = audio_dir / "ru_wait.wav" if audio_dir is not None else None
    if wav is None or not wav.exists():
        wav = audio_dir / "en_bridge.wav" if audio_dir is not None else None
    if wav is None or not wav.exists():
        return
    spoken = "ru" if wav.name.startswith("ru") else "en"
    FakeRecorder.audio = phrase(load_wav(wav))

    # «Проверить»: нажал один раз — запись остановилась сама, когда фраза кончилась
    results.clear()
    ctl.press("")
    check("кнопка «Проверить»: идёт запись, плашка «Слушаю…»", ctl.state == "listening" and ctl.hold is False
          and ctl.toast.isVisible() and ctl.toast.kind == "listen")
    done = wait(lambda: results or errors[1:])
    clip = QApplication.clipboard().text()
    if spoken == "ru":
        ok = done and results and results[-1].translated and clip.startswith("[en] ") and "подождите" in clip.lower()
    else:
        ok = done and results and not results[-1].translated and "bridge" in clip.lower()
    check("фраза → распознана → перевод в буфере обмена", ok, repr(clip))
    if spoken == "ru":
        check("словарь для чата дошёл до переводчика", calls and calls[-1][3] is not None
              and calls[-1][3].path.name == "game_ru_en.json",
              calls[-1][3].path.name if calls and calls[-1][3] is not None else "нет")
    check("плашка: перевод и бледной строкой — что сказано", ctl.toast.kind == "done" and ctl.toast.body == clip
          and (ctl.toast.note == results[-1].heard if spoken == "ru" else ctl.toast.note == ""),
          f"{ctl.toast.title!r}")

    # «рация»: клавиша удерживается 1 с, потом её отпускают — запись кончается сразу
    held_until = time.monotonic() + 1.0
    voice_chat.key_down = lambda combo: time.monotonic() < held_until
    results.clear()
    ctl.press("<alt>+v")
    wait(lambda: ctl.state != "listening", 10)
    check("удерживают клавишу — «рация»: запись до отпускания", ctl.hold is True
          and time.monotonic() - held_until < 0.5, f"hold={ctl.hold}")
    wait(lambda: results or ctl.state == "idle")

    # сказано сразу на языке чата — без перевода
    calls.clear()
    results.clear()
    cfg["voice_chat_lang"] = spoken
    voice_chat.key_down = lambda combo: False
    ctl.press("<alt>+v")
    wait(lambda: results or ctl.state == "idle")
    check("фраза на языке чата — копируется без перевода", results and not results[-1].translated and not calls
          and QApplication.clipboard().text() == results[-1].heard, repr(QApplication.clipboard().text()))
    ctl.shutdown()


def main() -> int:
    offline()
    audio_dir = Path(AUDIO_ARG) if AUDIO_ARG else None
    have = voice.available() and model() is not None
    if not voice.available():
        print("     faster-whisper или sounddevice не установлены — распознавание не проверяется")
    if have:
        recognition(audio_dir)
    chain(audio_dir, have)
    tabs()
    failed = [r for r in rows if not r[0]]
    print(f"failed: {len(failed)}")
    return 1 if failed else 0


def _close_qt() -> None:
    """Закрыть окна Qt, пока Python жив (на Windows иначе падение при выходе — см. test_i18n)."""
    try:
        from PySide6.QtCore import QCoreApplication, QEvent
        from PySide6.QtWidgets import QApplication
    except ImportError:
        return
    qa = QApplication.instance()
    if qa is None:
        return
    for w in qa.topLevelWidgets():
        w.close()
        w.deleteLater()
    QCoreApplication.sendPostedEvents(None, int(QEvent.Type.DeferredDelete.value))
    qa.processEvents()


if __name__ == "__main__":
    code = main()
    _close_qt()
    sys.stdout.flush()
    os._exit(code)
