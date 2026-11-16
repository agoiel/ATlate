"""Встроенный OCR Windows 10/11 (тот же, что использует MORT).

Плюсы: быстрый, лёгкий.
Нужен языковой пакет OCR. Если его нет, программа сама предложит его докачать
(см. app/win_setup.py) — Windows один раз спросит права администратора.
"""

from __future__ import annotations

import asyncio
import sys
import threading

from PIL import Image

from .. import languages
from ..models import Box, TextLine
from ..angle import words_angle
from .base import OcrBackend, OcrError, OcrLanguageMissing

# Имена компонентов Windows с OCR для каждого языка
OCR_CAPABILITY = {
    "ko": "ko-KR", "ja": "ja-JP", "zh": "zh-CN", "zh-tw": "zh-TW", "en": "en-US",
}


def capability_name(lang: str) -> str:
    return f"Language.OCR~~~{OCR_CAPABILITY.get(lang, 'en-US')}~0.0.1.0"


def available_ocr_tags() -> list[str]:
    """Языки, для которых в Windows установлен OCR."""
    from winrt.windows.media.ocr import OcrEngine
    return [l.language_tag for l in OcrEngine.available_recognizer_languages]


def find_ocr_tag(lang: str, available: list[str]) -> str | None:
    for want in (t.lower() for t in languages.src(lang)["windows"]):
        for av in available:
            if av.lower() == want or av.lower().startswith(want + "-"):
                return av
    return None


def _ensure_com() -> None:
    """Распознавание идёт в фоновом потоке — подключаем его к COM (иначе WinRT может упасть)."""
    try:
        import ctypes
        ctypes.windll.ole32.CoInitializeEx(None, 0)  # COINIT_MULTITHREADED; повторный вызов безопасен
    except Exception:  # noqa: BLE001
        pass


class WindowsOcrBackend(OcrBackend):
    name = "windows"
    # распознавание Windows асинхронное и работает из нескольких потоков;
    # у каждого потока свои экземпляры распознавателя
    supports_parallel = True

    def __init__(self, cfg):
        super().__init__(cfg)
        if sys.platform != "win32":
            raise OcrError("Windows OCR работает только в Windows. Выберите EasyOCR в настройках.")
        _ensure_com()
        try:
            from winrt.windows.globalization import Language  # noqa: F401
            from winrt.windows.graphics.imaging import BitmapPixelFormat, SoftwareBitmap  # noqa: F401
            from winrt.windows.media.ocr import OcrEngine  # noqa: F401
            from winrt.windows.security.cryptography import CryptographicBuffer  # noqa: F401
        except ImportError as e:
            raise OcrError(
                "Не установлены модули winrt для Windows OCR.\n"
                "Выполните: pip install -r requirements.txt\n"
                f"({e})"
            ) from e
        self._local = threading.local()

    # ------------------------------------------------------------------
    def _engine(self, lang: str):
        engines = getattr(self._local, "engines", None)
        if engines is None:
            engines = self._local.engines = {}
        if lang in engines:
            return engines[lang]
        from winrt.windows.globalization import Language
        from winrt.windows.media.ocr import OcrEngine

        available = available_ocr_tags()
        tag = find_ocr_tag(lang, available)
        if tag is None:
            raise OcrLanguageMissing(
                f"В Windows не установлен OCR для языка «{languages.src(lang)['name']}».\n"
                f"Установленные: {', '.join(available) or 'нет'}.",
                lang,
            )
        engine = OcrEngine.try_create_from_language(Language(tag))
        if engine is None:
            raise OcrError(f"Не удалось запустить Windows OCR для «{tag}».")
        engines[lang] = engine
        return engine

    def available_languages(self, codes: list[str]) -> list[str]:
        """Только языки, для которых в Windows установлено распознавание."""
        try:
            available = available_ocr_tags()
        except Exception:  # noqa: BLE001
            return []
        return [c for c in codes if find_ocr_tag(c, available) is not None]

    # ------------------------------------------------------------------
    def recognize(self, image: Image.Image, lang: str) -> list[TextLine]:
        from winrt.windows.graphics.imaging import BitmapPixelFormat, SoftwareBitmap
        from winrt.windows.media.ocr import OcrEngine
        from winrt.windows.security.cryptography import CryptographicBuffer

        _ensure_com()
        engine = self._engine(lang)
        k = float(self.cfg.get("ocr_upscale", 1.5))
        max_dim = int(OcrEngine.max_image_dimension)
        w, h = image.size
        if max(w, h) * k > max_dim:
            k = max_dim / max(w, h)
        img = self.upscale(image, k).convert("RGBA")

        async def run():
            buf = CryptographicBuffer.create_from_byte_array(img.tobytes())
            bitmap = SoftwareBitmap.create_copy_from_buffer(
                buf, BitmapPixelFormat.RGBA8, img.width, img.height)
            return await engine.recognize_async(bitmap)

        try:
            result = asyncio.run(run())
        except Exception as e:  # noqa: BLE001
            raise OcrError(f"Ошибка Windows OCR: {e}") from e

        joiner = languages.src(lang)["joiner"]
        out: list[TextLine] = []
        for line in result.lines:
            words = list(line.words)
            if not words:
                continue
            box = None
            rects = []
            for wd in words:
                r = wd.bounding_rect
                b = Box(r.x, r.y, r.width, r.height)
                rects.append((b.x, b.y, b.w, b.h))
                box = b if box is None else box.union(b)
            text = joiner.join(wd.text for wd in words)
            # рамки слов прямые, но их центры идут вдоль строки — по ним виден наклон текста
            out.append(TextLine(box=box.scaled(1 / k), text=text, conf=1.0, angle=words_angle(rects)))
        return out
