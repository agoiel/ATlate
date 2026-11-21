"""Проверка внутри собранного exe: то, что кладётся в сборку отдельными файлами, нашлось.

Собирается PyInstaller с теми же параметрами для данных, что и программа (см. release.yml):

    python -m PyInstaller --noconfirm --onedir --name frozen_check --add-data "libs;libs" ^
        --collect-data pyphen tools/frozen_check.py
    dist/frozen_check/frozen_check.exe

* FriBiDi из libs подгружается (app/__init__.py) — сложная вёрстка Pillow включена;
* словари переносов Pyphen на месте;
* «Голос в чат» (с --voice ПАПКА_МОДЕЛИ [WAV]): запись звука (PortAudio) и распознавание речи
  работают в сборке — CTranslate2, VAD (onnxruntime) и токенизатор нашлись, а PyAV не нужен.
Код возврата 1, если что-то не нашлось.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")    # собранный exe пишет в консоль в кодировке системы
if not getattr(sys, "frozen", False):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import app  # noqa: E402,F401 — подгружает FriBiDi до Pillow
import pyphen  # noqa: E402
from PIL import Image, ImageDraw, ImageFont  # noqa: E402 — как в Студии (features грузит модуль по имени,
                                             # и PyInstaller его без ImageFont в сборку не кладёт)


def _shaped(word: str, font_name: str) -> bool:
    """Слово рисуется иначе, чем простой вёрсткой, — значит, буквы соединены и стоят как надо."""
    def shot(font) -> bytes:
        im = Image.new("L", (260, 90), 0)
        ImageDraw.Draw(im).text((10, 10), word, font=font, fill=255)
        return im.tobytes()
    try:
        return shot(ImageFont.truetype(font_name, 40)) != shot(
            ImageFont.truetype(font_name, 40, layout_engine=ImageFont.Layout.BASIC))
    except OSError:
        return False


rows = [("собрано PyInstaller", bool(getattr(sys, "frozen", False))),
        ("FriBiDi подхватился — сложная вёрстка включена", bool(ImageFont.core.HAVE_RAQM)),
        ("арабский текст сверстан (Segoe UI)", _shaped("تذهب", "segoeui.ttf" if sys.platform == "win32"
                                                         else "DejaVuSans.ttf")),
        ("словари переносов на месте", "ru" in pyphen.LANGUAGES and "de" in pyphen.LANGUAGES)]


def _voice(model_dir: str, wav: str) -> list[tuple[str, bool]]:
    import numpy as np

    from app import voice
    out = [("«Голос в чат»: распознавание и запись звука есть в сборке", voice.available())]
    try:
        import sounddevice  # noqa: F401 — PortAudio подгружается при импорте
        out.append(("PortAudio подхватился", True))
    except Exception as e:  # noqa: BLE001
        print("     sounddevice:", e)
        out.append(("PortAudio подхватился", False))
    voice.model_dir = lambda _name: Path(model_dir)
    rec = voice.Recognizer()
    try:
        if wav:
            import wave
            with wave.open(wav) as w:
                a = np.frombuffer(w.readframes(w.getnframes()), np.int16).astype(np.float32) / 32768
                a = voice.resample(a, w.getframerate())
            h = rec.recognize(a, "base", "ru", "en")
            print(f"     распознано: {h.lang} {h.text!r}")
            out.append(("речь распознаётся (CTranslate2, VAD, токенизатор)", bool(h.text) and h.lang == "en"))
        else:
            noise = (np.random.default_rng(1).standard_normal(16000 * 2) * 0.05).astype(np.float32)
            lang, _p, _all = rec._load("base").detect_language(noise)
            out.append(("модель распознавания загружается и работает (CTranslate2)", bool(lang)))
        out.append(("тишина — пусто (VAD на месте)", rec.recognize(np.zeros(32000, np.float32), "base", "ru", "en").text == ""))
    except Exception as e:  # noqa: BLE001
        print("     распознавание:", type(e).__name__, e)
        out.append(("речь распознаётся (CTranslate2, VAD, токенизатор)", False))
    out.append(("PyAV не попал в сборку (не нужен)", "av" not in sys.modules or not getattr(sys.modules["av"], "__file__", None)))
    return out


if "--voice" in sys.argv:
    i = sys.argv.index("--voice")
    rows += _voice(sys.argv[i + 1], sys.argv[i + 2] if len(sys.argv) > i + 2 else "")
for name, ok in rows:
    print(f"{'OK  ' if ok else 'FAIL'} {name}")
print(f"FriBiDi: {app.fribidi or 'не загружался'}")
failed = sum(not ok for _, ok in rows)
print(f"failed: {failed}")
sys.exit(1 if failed else 0)
