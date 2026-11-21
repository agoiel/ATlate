"""«Голос в чат» (только ПК): фраза в микрофон → текст → перевод → буфер обмена.

Для онлайн-игр: нажали клавишу, сказали фразу по-русски — перевод на языке чата уже в буфере,
остаётся вставить его в чат (Ctrl+V). Сама программа ничего не вставляет и не нажимает:
имитация нажатий может не понравиться античиту, а буфер обмена безопасен.

* Речь распознаёт Whisper (faster-whisper) прямо на компьютере: бесплатно и без интернета.
  Модель скачивается один раз, когда функцию включают (models/whisper-*).
* Язык фразы выбирается из двух: «мой язык» и язык чата. На коротких фразах («го», «gg»)
  определение из всех языков часто ошибается, а из двух — почти никогда. Если фразу сказали
  сразу на языке чата, она копируется как есть, без перевода.
* Whisper на тишине и шуме иногда «слышит» то, чего не говорили («Продолжение следует…»,
  «Субтитры сделал…»). Такие куски отбрасываются: участки без речи вырезает VAD (Silero),
  а известные выдумки отсеивает фильтр.
* Переводит тот же переводчик, что выбран для манхвы; если бесплатный не ответил — другой
  бесплатный, как и при обычном переводе.
* Словарь для чата — обычный глоссарий (с какого языка говорю → на язык чата): игровые слова,
  герои, ники. Его слова подсказываются распознаванию, а в перевод попадают как записаны —
  у обычных переводчиков подстановкой в текст, у ИИ — списком в запросе. Русские слова
  склоняются, поэтому «катку», «мидом», «лесу» тоже находятся.

Тут нет ничего от Qt: окно и горячая клавиша — в ui/voice_chat.py.
"""

from __future__ import annotations

import importlib.util
import os
import re
import shutil
import sys
import threading
import types
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from . import i18n, languages
from .config import DATA_ROOT, GLOSSARY_DIR
from .glossary import Glossary, GlossaryEntry
from .i18n import tr

RATE = 16000                    # Whisper слушает 16 кГц, моно
MODELS_DIR = DATA_ROOT / "models"
# Модели распознавания: «быстрая» и «точная». Размер — для подписи в настройках.
MODELS = {
    "base": {"repo": "Systran/faster-whisper-base", "mb": 145},
    "small": {"repo": "Systran/faster-whisper-small", "mb": 484},
}
MODEL_FILES = ("config.json", "tokenizer.json", "vocabulary.txt", "model.bin")
# Зеркало Hugging Face можно задать стандартной переменной HF_ENDPOINT (например, hf-mirror.com)
HF = os.environ.get("HF_ENDPOINT", "https://huggingface.co").rstrip("/")

MIN_SECONDS = 0.3               # короче — случайное нажатие
HOLD_LIMIT = 30.0               # удерживать клавишу дольше — запись всё равно остановится
TAP_LIMIT = 20.0                # без удержания: самая длинная фраза
NO_SPEECH_WAIT = 6.0            # без удержания: столько ждём начала речи
END_SILENCE = 0.8               # без удержания: пауза после речи, которая заканчивает фразу


class VoiceError(RuntimeError):
    """Понятное сообщение для игрока: что не так и что сделать."""


def available() -> bool:
    """Есть ли в этой сборке распознавание речи и запись звука."""
    return all(importlib.util.find_spec(m) is not None for m in ("faster_whisper", "ctranslate2", "sounddevice"))


def my_language(cfg) -> str:
    """Язык, на котором говорит игрок: из настроек, иначе — язык интерфейса."""
    code = cfg.get("voice_my_lang") or ""
    if code in languages.voice_languages():
        return code
    return "en" if i18n.english else "ru"


def chat_language(cfg) -> str:
    code = cfg.get("voice_chat_lang") or "en"
    return code if code in languages.voice_languages() else "en"


# ================================================================ модель распознавания
def model_dir(name: str) -> Path:
    return MODELS_DIR / f"whisper-{name}"


def model_ready(name: str) -> bool:
    d = model_dir(name)
    return all((d / f).is_file() and (d / f).stat().st_size > 0 for f in MODEL_FILES)


def download_model(name: str, progress: Callable[[int, int], None] | None = None,
                   cancelled: Callable[[], bool] | None = None) -> None:
    """Скачать модель в models/whisper-<name>. Сначала во временную папку: оборванная загрузка
    не выдаётся за готовую модель. progress(скачано, всего) — в байтах."""
    import requests

    info = MODELS[name]
    total = info["mb"] * 1_000_000
    done = 0
    dest = model_dir(name)
    tmp = dest.with_name(dest.name + ".part")
    shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir(parents=True, exist_ok=True)
    try:
        with requests.Session() as http:
            for fname in MODEL_FILES:
                url = f"{HF}/{info['repo']}/resolve/main/{fname}"
                with http.get(url, stream=True, timeout=30) as r:
                    r.raise_for_status()
                    size = int(r.headers.get("Content-Length") or 0)
                    if fname == "model.bin" and size:
                        total = done + size
                    got = 0
                    with open(tmp / fname, "wb") as f:
                        for chunk in r.iter_content(1 << 20):
                            if cancelled is not None and cancelled():
                                raise VoiceError(tr("Загрузка отменена."))
                            f.write(chunk)
                            got += len(chunk)
                            done += len(chunk)
                            if progress is not None:
                                progress(done, max(total, done))
                    if size and got != size:
                        raise VoiceError(tr("Файл модели скачался не полностью — попробуйте ещё раз."))
        shutil.rmtree(dest, ignore_errors=True)
        tmp.replace(dest)
    except VoiceError:
        shutil.rmtree(tmp, ignore_errors=True)
        raise
    except Exception as e:  # noqa: BLE001 — сеть, диск
        shutil.rmtree(tmp, ignore_errors=True)
        raise VoiceError(tr("Не удалось скачать модель распознавания: {0}", e)) from e


def delete_model(name: str) -> None:
    shutil.rmtree(model_dir(name), ignore_errors=True)


def _faster_whisper():
    """Импорт faster-whisper. PyAV ему нужен только чтобы читать звуковые файлы; мы отдаём звук
    готовым массивом, поэтому в сборку PyAV не входит (это ~60 МБ), а его импорт обходим заглушкой.
    Любой модуль faster_whisper — только через эту функцию: импорт пакета сразу тянет PyAV."""
    try:
        import av  # noqa: F401
    except ImportError:
        sys.modules.setdefault("av", types.ModuleType("av"))
    import faster_whisper
    import faster_whisper.vad
    return faster_whisper


def cpu_threads() -> int:
    """Половина ядер, но не больше четырёх: распознавание идёт, пока работает игра."""
    return max(1, min(4, (os.cpu_count() or 2) // 2))


# ================================================================ распознавание
# Что Whisper «слышит» в тишине и шуме: титры и концовки видео, на которых его учили.
_PHANTOMS = tuple(
    "продолжение следует|субтитры сделал|субтитры создавал|субтитры подогнал|субтитры делал|редактор субтитров|"
    "корректор|спасибо за просмотр|подписывайтесь на канал|ставьте лайки|"
    "thank you for watching|thanks for watching|subtitles by|please subscribe|like and subscribe|amara org".split("|"))


# Игровые слова, которые Whisper иначе пишет как обычные («мид» → «МИТ»). Подсказка, а не замена:
# Whisper берёт слово отсюда, только если его действительно сказали.
HOTWORDS = {
    "ru": "Мид, топ, лес, гг, афк, го, хил, танк, саппорт, катка.",
    "en": "Mid, top, jungle, gg, afk, heal, tank, support.",
}


def hotwords(lang: str, extra: list[str] = ()) -> str | None:
    """Подсказка распознаванию: игровые слова языка и слова из словаря для чата (не больше ~300 знаков —
    длинную подсказку Whisper всё равно обрежет)."""
    words: list[str] = []
    base = HOTWORDS.get(lang, "")
    for w in [x.strip(" .") for x in base.split(",")] + [x.strip() for x in extra]:
        if w and w.lower() not in (v.lower() for v in words):
            words.append(w)
    out = ""
    for w in words:
        if len(out) + len(w) + 2 > 300:
            break
        out = f"{out}, {w}" if out else w
    return (out[:1].upper() + out[1:] + ".") if out else None


def _norm(text: str) -> str:
    return " ".join(re.sub(r"[^\w]+", " ", text.lower()).split())


def is_phantom(text: str) -> bool:
    """Выдумка Whisper вместо речи: нет ни одной буквы, титры («Редактор субтитров А. Синецкая
    Корректор…») или концовка видео («Спасибо за просмотр»)."""
    n = _norm(text)
    if not n or not any(ch.isalpha() for ch in n):
        return True
    if "субтитр" in n or "subtitles by" in n or "amara org" in n:
        return True
    return any(n.startswith(p) and len(n.split()) <= len(p.split()) + 6 for p in _PHANTOMS)


def choose_language(probs: dict[str, float], mine: str, chat: str) -> str:
    """На каком языке сказана фраза: из двух — моего и языка чата (вероятности от Whisper)."""
    if languages.whisper_code(mine) == languages.whisper_code(chat):
        return mine
    pm = probs.get(languages.whisper_code(mine), 0.0)
    pc = probs.get(languages.whisper_code(chat), 0.0)
    return chat if pc > pm else mine


@dataclass
class Heard:
    text: str               # что распознано (пусто — речи нет)
    lang: str               # на каком языке (наш код)


class Recognizer:
    """Whisper на процессоре. Модель загружается при первой фразе и остаётся в памяти,
    пока функция включена (около 200 МБ для быстрой модели)."""

    def __init__(self):
        self._model = None
        self._name = ""
        self._lock = threading.Lock()

    def unload(self) -> None:
        with self._lock:
            self._model, self._name = None, ""

    def preload(self, name: str) -> None:
        """Загрузить модель заранее (в фоне), чтобы первая фраза не ждала лишнюю секунду."""
        try:
            with self._lock:
                self._load(name)
        except Exception:  # noqa: BLE001 — не вышло сейчас — загрузится при первой фразе с понятной ошибкой
            pass

    def _load(self, name: str):
        if self._model is None or self._name != name:
            if not model_ready(name):
                raise VoiceError(tr("Модель распознавания не скачана — скачайте её в карточке «Голос в чат»."))
            self._model = _faster_whisper().WhisperModel(str(model_dir(name)), device="cpu", compute_type="int8",
                                                         cpu_threads=cpu_threads())
            self._name = name
        return self._model

    def recognize(self, audio: np.ndarray, name: str, mine: str, chat: str, words: list[str] = ()) -> Heard:
        """words — слова из словаря для чата (на моём языке): их легче узнать в речи."""
        vad = _faster_whisper().vad
        with self._lock:
            model = self._load(name)
            # Сначала VAD (Silero) вырезает всё, где нет речи: на тишине и шуме Whisper выдумывает
            # текст. Нет речи — нечего и распознавать.
            chunks = vad.get_speech_timestamps(audio, vad.VadOptions(min_silence_duration_ms=500, speech_pad_ms=200))
            if not chunks:
                return Heard("", mine)
            speech = np.concatenate([audio[c["start"]:c["end"]] for c in chunks]).astype(np.float32)
            _lang, _p, probs = model.detect_language(speech)
            lang = choose_language(dict(probs), mine, chat)
            segments, _info = model.transcribe(
                speech, language=languages.whisper_code(lang), beam_size=5, condition_on_previous_text=False,
                without_timestamps=True, vad_filter=False, hotwords=hotwords(lang, words if lang == mine else ()))
            segments = list(segments)
        kept = [s for s in segments if not (s.no_speech_prob > 0.6 and s.avg_logprob < -1.0)]
        text = " ".join(s.text.strip() for s in kept).strip()
        return Heard("" if is_phantom(text) else text, lang)


# ================================================================ конец фразы
class Endpoint:
    """Где кончилась фраза, когда клавишу не удерживают: по громкости, кадрами по 30 мс.

    Порог речи подстраивается под шум микрофона: «тишина» — это уровень, ниже которого звук
    держится большую часть времени. Речь — три кадра подряд заметно громче. Фраза окончена,
    когда после речи END_SILENCE секунд тихо. Если речи не было NO_SPEECH_WAIT секунд — тоже
    стоп: тогда решает распознавание (VAD), было ли что-то сказано.
    """

    FRAME = 480

    def __init__(self, silence: float = END_SILENCE, wait: float = NO_SPEECH_WAIT, limit: float = TAP_LIMIT):
        self.silence, self.wait, self.limit = silence, wait, limit
        self.frames = 0
        self.floor: float | None = None
        self.heard = False
        self._loud_run = 0
        self._quiet = 0
        self._rest = np.zeros(0, np.float32)
        self.level = 0.0                    # громкость последнего кадра 0…1 — для индикатора

    def threshold(self) -> float:
        return max(0.003, (self.floor or 0.0) * 3.5)

    def feed(self, chunk: np.ndarray) -> str:
        """Новый кусок звука (16 кГц) → 'listen' (слушаем дальше), 'done' (фраза кончилась)
        или 'silent' (речи так и не было)."""
        buf = np.concatenate([self._rest, np.asarray(chunk, np.float32).ravel()])
        n = len(buf) // self.FRAME
        self._rest = buf[n * self.FRAME:]
        sec = self.FRAME / RATE
        for frame in buf[:n * self.FRAME].reshape(n, self.FRAME) if n else ():
            rms = float(np.sqrt(np.mean(frame * frame)))
            self.frames += 1
            self.level = min(1.0, rms * 12)
            if self.floor is None or rms < self.floor:
                self.floor = rms                        # тише — сразу новый уровень шума
            else:
                self.floor += (rms - self.floor) * 0.005    # громче — подстраиваемся медленно
            if rms > self.threshold():
                self._loud_run += 1
                self._quiet = 0
                if self._loud_run >= 3:
                    self.heard = True
            else:
                self._loud_run = 0
                self._quiet += 1
            if self.heard and self._quiet * sec >= self.silence:
                return "done"
            if not self.heard and self.frames * sec >= self.wait:
                return "silent"
            if self.frames * sec >= self.limit:
                return "done"
        return "listen"


# ================================================================ запись с микрофона
def resample(audio: np.ndarray, rate: int) -> np.ndarray:
    """В 16 кГц. Линейной интерполяции для распознавания речи достаточно; перед уменьшением
    частоты — сглаживание, чтобы высокие частоты не превращались в шум."""
    audio = np.asarray(audio, np.float32).ravel()
    if rate == RATE or not len(audio):
        return audio
    if rate > RATE:
        k = max(1, int(round(rate / RATE)))
        if k > 1:
            audio = np.convolve(audio, np.ones(k, np.float32) / k, mode="same")
    n = int(len(audio) * RATE / rate)
    return np.interp(np.linspace(0, len(audio) - 1, n), np.arange(len(audio)), audio).astype(np.float32)


def input_devices() -> list[str]:
    """Микрофоны (по одному разу: Windows показывает каждый через несколько звуковых систем)."""
    try:
        import sounddevice as sd
        api = sd.default.hostapi
        out: list[str] = []
        for d in sd.query_devices():
            if d["max_input_channels"] > 0 and d["hostapi"] == api and d["name"] not in out:
                out.append(d["name"])
        return out
    except Exception:  # noqa: BLE001 — нет PortAudio или звуковой карты
        return []


def _device_index(name: str):
    if not name:
        return None
    try:
        import sounddevice as sd
        api = sd.default.hostapi
        for i, d in enumerate(sd.query_devices()):
            if d["name"] == name and d["max_input_channels"] > 0 and d["hostapi"] == api:
                return i
    except Exception:  # noqa: BLE001
        pass
    return None                     # микрофон отключили — берём системный


class Recorder:
    """Запись с микрофона в фоне (PortAudio через sounddevice)."""

    def __init__(self, device: str = ""):
        self.device = device
        self.rate = RATE
        self._chunks: list[np.ndarray] = []
        self._taken = 0
        self._lock = threading.Lock()
        self._stream = None

    def start(self) -> None:
        import sounddevice as sd

        def callback(indata, _frames, _time, _status):
            with self._lock:
                self._chunks.append(indata[:, 0].copy())

        dev = _device_index(self.device)
        try:
            self._stream = sd.InputStream(samplerate=RATE, channels=1, dtype="float32", device=dev,
                                          blocksize=self.rate // 50, callback=callback)
            self.rate = RATE
        except Exception:  # noqa: BLE001 — микрофон не умеет 16 кГц: пишем как умеет, потом пересчитаем
            try:
                self.rate = int(sd.query_devices(dev, "input")["default_samplerate"])
                self._stream = sd.InputStream(samplerate=self.rate, channels=1, dtype="float32", device=dev,
                                              callback=callback)
            except Exception as e:  # noqa: BLE001
                raise VoiceError(tr("Микрофон не открылся: {0}", e)) from e
        try:
            self._stream.start()
        except Exception as e:  # noqa: BLE001
            self._stream = None
            raise VoiceError(tr("Микрофон не открылся: {0}", e)) from e

    def take(self) -> np.ndarray:
        """Что записалось с прошлого раза (16 кГц) — для определения конца фразы."""
        with self._lock:
            new = self._chunks[self._taken:]
            self._taken = len(self._chunks)
        return resample(np.concatenate(new), self.rate) if new else np.zeros(0, np.float32)

    def stop(self) -> np.ndarray:
        """Остановить запись и вернуть всю фразу (16 кГц)."""
        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception:  # noqa: BLE001
                pass
            self._stream = None
        with self._lock:
            chunks, self._chunks, self._taken = self._chunks, [], 0
        return resample(np.concatenate(chunks), self.rate) if chunks else np.zeros(0, np.float32)


# ================================================================ звуковые сигналы
_TONES = {
    "start": ((660, 0.06), (880, 0.08)),        # слушаю
    "done": ((988, 0.06), (1319, 0.10)),        # перевод в буфере
    "error": ((392, 0.10), (262, 0.16)),        # не получилось
}


def tone(kind: str, rate: int = 44100) -> np.ndarray:
    parts = []
    for freq, dur in _TONES[kind]:
        t = np.arange(int(rate * dur)) / rate
        w = np.sin(2 * np.pi * freq * t) * 0.22
        fade = min(len(w) // 4, int(rate * 0.01))
        if fade:
            w[:fade] *= np.linspace(0, 1, fade)
            w[-fade:] *= np.linspace(1, 0, fade)
        parts.append(w)
        parts.append(np.zeros(int(rate * 0.02)))
    return np.concatenate(parts).astype(np.float32)


def beep(kind: str) -> None:
    """Короткий сигнал: в полноэкранной игре окно программы не видно, а звук слышно."""
    try:
        import sounddevice as sd
        sd.play(tone(kind), 44100)
    except Exception:  # noqa: BLE001 — нет звука — не беда
        pass


# ================================================================ словарь для чата
def glossary_fits(g: Glossary, mine: str, chat: str) -> bool:
    """Словарь подходит, если он с моего языка (или язык не указан) и на язык чата (или на любой)."""
    src_ok = not g.source_lang or languages.whisper_code(g.source_lang) == languages.whisper_code(mine)
    return src_ok and g.fits(chat)


def chat_glossary(cfg, mine: str, chat: str) -> Glossary | None:
    """Выбранный словарь для чата, если он есть и подходит к языкам."""
    name = cfg.get("voice_glossary") or ""
    if not name:
        return None
    try:
        g = Glossary.load(GLOSSARY_DIR / name)
    except (OSError, ValueError):
        return None
    return g if glossary_fits(g, mine, chat) else None


_CYR_ENDING = "аеёиоуыэюяйьмхвг"         # буквы русских окончаний: катк-у, мид-ом, лес-ами, хорош-его
_CJK = re.compile("[\u3040-\u30FF\u3400-\u4DBF\u4E00-\u9FFF\uAC00-\uD7AF]")


def _term_pattern(source: str) -> str:
    """Шаблон слова из словаря в распознанной фразе.

    * Кириллица: слово может склоняться. Последняя гласная (или ь, й) у слов от четырёх букв —
      часть окончания (катка → катк-у), дальше до трёх букв окончания. Буквы окончания — только
      гласные и м, х, в, г: «пушка» не станет «push», «топать» — «top».
    * Латиница: множественное число (heals, tank's).
    * Короткие (1–2 буквы: «го», «гг») — только целым словом, иначе «го» нашлось бы в «год».
    * Иероглифы и хангыль: просто как есть (там нет пробелов, а окончания пишутся слитно).
    """
    s = source.strip()
    if _CJK.match(s):
        return re.escape(s)
    tail = ""
    last = s[-1:].lower()
    if len(s) >= 3 and re.match("[а-яё]", last):
        if len(s) >= 4 and last in "аяоеыиуюьй":
            s = s[:-1]
        tail = f"[{_CYR_ENDING}]{{0,3}}"
    elif len(s) >= 3 and re.match("[a-z]", last):
        tail = "(?:s|es|'s)?"
    return r"(?<![^\W_])" + re.escape(s) + tail + r"(?![^\W_])"


def find_terms(g: Glossary | None, text: str) -> list[tuple[int, int, GlossaryEntry]]:
    """Слова словаря во фразе: (начало, конец, запись), без пересечений; длинные термины важнее
    («гг вп» раньше «гг»)."""
    if g is None:
        return []
    entries = sorted((e for e in g.entries if e.source.strip() and e.target.strip()),
                     key=lambda e: len(e.source.strip()), reverse=True)
    found: list[tuple[int, int, GlossaryEntry]] = []
    for e in entries:
        for m in re.finditer(_term_pattern(e.source), text, re.IGNORECASE):
            if not any(m.start() < b and a < m.end() for a, b, _e in found):
                found.append((m.start(), m.end(), e))
    return sorted(found, key=lambda t: t[0])


def apply_terms(text: str, found: list[tuple[int, int, GlossaryEntry]]) -> str:
    """Подставить переводы слов словаря прямо во фразу — обычный переводчик оставит их как есть."""
    out, pos = [], 0
    for a, b, e in found:
        out += [text[pos:a], e.target.strip()]
        pos = b
    return "".join(out + [text[pos:]])


# ================================================================ перевод
def translate(cfg, text: str, src: str, tgt: str, glossary: Glossary | None = None) -> tuple[str, str]:
    """Перевести фразу для чата → (перевод, кто перевёл). Слова словаря обычным переводчикам
    подставляются в текст, ИИ — уходят списком; бесплатный не ответил — переводит другой бесплатный."""
    from . import translators

    req = translators.TranslateRequest(texts=[text], src=src, tgt=tgt, chat=True)
    found = find_terms(glossary, text)
    if found:
        seen: dict[int, GlossaryEntry] = {id(e): e for _a, _b, e in found}
        req.glossary = list(seen.values())
    out, by = translators.translate_with_fallback(cfg, req, [apply_terms(text, found)] if found else None)
    return (out or [""])[0].strip(), by


@dataclass
class Phrase:
    heard: str              # что распознано
    spoken: str             # на каком языке сказано
    text: str               # что лежит в буфере обмена: перевод или сама фраза
    translated: bool        # False — фразу сказали сразу на языке чата
    by: str = ""            # кто перевёл
    terms: list[str] = field(default_factory=list)    # какие слова словаря нашлись во фразе


def process(audio: np.ndarray, cfg, recognizer: Recognizer) -> Phrase:
    """Записанная фраза → распознать → перевести. Вызывается в фоновом потоке."""
    from .translators import TranslateError

    if len(audio) < RATE * MIN_SECONDS:
        raise VoiceError(tr("Слишком короткая запись — скажите фразу ещё раз."))
    mine, chat = my_language(cfg), chat_language(cfg)
    glossary = chat_glossary(cfg, mine, chat)
    words = [e.source.strip() for e in glossary.entries if e.source.strip()] if glossary else []
    heard = recognizer.recognize(audio, cfg.get("voice_model", "base"), mine, chat, words)
    if not heard.text:
        raise VoiceError(tr("Речь не распознана — скажите фразу ещё раз."))
    if languages.whisper_code(heard.lang) == languages.whisper_code(chat):
        return Phrase(heard.text, heard.lang, heard.text, False)
    try:
        text, by = translate(cfg, heard.text, heard.lang, chat, glossary)
    except TranslateError as e:
        raise VoiceError(str(e)) from e
    if not text:
        raise VoiceError(tr("Переводчик вернул пустой ответ."))
    terms = [e.source for _a, _b, e in find_terms(glossary, heard.text)]
    return Phrase(heard.text, heard.lang, text, True, by, terms)
