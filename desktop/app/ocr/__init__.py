"""Движки распознавания текста (OCR)."""

from __future__ import annotations

from . import enhance
from .base import OcrBackend, OcrError, OcrLanguageMissing


def easyocr_available() -> bool:
    """Установлен ли EasyOCR. В собранную программу он не входит (ему нужен PyTorch на 1–2 ГБ)."""
    import importlib.util
    try:
        return importlib.util.find_spec("easyocr") is not None
    except (ImportError, ValueError):
        return False


def create_ocr(name: str, cfg) -> OcrBackend:
    if name == "easyocr":
        from .easyocr_backend import EasyOcrBackend
        return EasyOcrBackend(cfg)
    from .windows_ocr import WindowsOcrBackend
    return WindowsOcrBackend(cfg)


__all__ = ["easyocr_available", "enhance", "OcrBackend", "OcrError", "OcrLanguageMissing", "create_ocr"]
