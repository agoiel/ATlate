from __future__ import annotations

from PIL import Image

from ..models import TextLine


class OcrError(RuntimeError):
    """Ошибка распознавания с понятным для пользователя текстом."""


class OcrLanguageMissing(OcrError):
    """В Windows не установлен OCR для нужного языка — его можно докачать."""

    def __init__(self, message: str, lang: str):
        super().__init__(message)
        self.lang = lang


class OcrBackend:
    name = "base"
    # можно ли распознавать несколько картинок одновременно из разных потоков
    supports_parallel = False

    def __init__(self, cfg):
        self.cfg = cfg

    def recognize(self, image: Image.Image, lang: str) -> list[TextLine]:
        """Возвращает строки текста в координатах исходной картинки."""
        raise NotImplementedError

    def available_languages(self, codes: list[str]) -> list[str]:
        """Какие из языков этот движок может распознавать прямо сейчас (для автоопределения языка)."""
        return list(codes)

    @staticmethod
    def upscale(image: Image.Image, k: float) -> Image.Image:
        if k is None or abs(k - 1.0) < 0.01:
            return image
        w, h = image.size
        return image.resize((max(1, int(w * k)), max(1, int(h * k))), Image.LANCZOS)
