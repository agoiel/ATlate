"""Снимок выделенной области экрана.

Область хранится в «логических» координатах Qt относительно своего монитора.
Снимок делается через Qt целым монитором и затем обрезается — так правильно
учитывается масштаб Windows (125 %, 150 % и т.д.).
"""

from __future__ import annotations

import sys
from dataclasses import asdict, dataclass

import numpy as np
from PIL import Image
from PySide6.QtCore import QRect
from PySide6.QtGui import QGuiApplication, QImage, QPixmap, QScreen


@dataclass
class Region:
    screen: str
    x: int
    y: int
    w: int
    h: int

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict | None) -> "Region | None":
        if not d:
            return None
        try:
            return cls(str(d["screen"]), int(d["x"]), int(d["y"]), int(d["w"]), int(d["h"]))
        except (KeyError, TypeError, ValueError):
            return None


def find_screen(name: str) -> QScreen:
    for s in QGuiApplication.screens():
        if s.name() == name:
            return s
    return QGuiApplication.primaryScreen()


def grab_screen(screen: QScreen) -> QPixmap:
    pix = screen.grabWindow(0)
    k = pix.width() / max(1, screen.geometry().width())
    pix.setDevicePixelRatio(k)
    return pix


def qimage_to_pil(img: QImage) -> Image.Image:
    img = img.convertToFormat(QImage.Format.Format_RGB888)
    w, h, bpl = img.width(), img.height(), img.bytesPerLine()
    buf = np.frombuffer(bytes(img.constBits()), dtype=np.uint8)
    arr = buf[: bpl * h].reshape(h, bpl)[:, : w * 3].reshape(h, w, 3)
    return Image.fromarray(arr.copy(), "RGB")


def grab_region_raw(region: Region) -> tuple[QImage, float]:
    """Снимок области как QImage — без перевода в картинку PIL (для Live: следить за страницей)."""
    screen = find_screen(region.screen)
    pix = screen.grabWindow(0)
    k = pix.width() / max(1, screen.geometry().width())
    rect = QRect(round(region.x * k), round(region.y * k), round(region.w * k), round(region.h * k))
    rect = rect.intersected(QRect(0, 0, pix.width(), pix.height()))
    return pix.copy(rect).toImage(), k


def grab_region(region: Region) -> tuple[Image.Image, float]:
    """Снимок области. Возвращает (картинка, коэффициент пикселей на логический пиксель)."""
    img, k = grab_region_raw(region)
    return qimage_to_pil(img), k


def gray_thumb(img: QImage, width: int = 64) -> np.ndarray:
    """Маленькая серая копия кадра — по ней Live сравнивает кадры. Уменьшает сам Qt, без копии кадра в PIL."""
    from PySide6.QtCore import Qt
    h = max(1, int(width * img.height() / max(1, img.width())))
    small = img.scaled(width, h, Qt.AspectRatioMode.IgnoreAspectRatio,
                       Qt.TransformationMode.SmoothTransformation).convertToFormat(QImage.Format.Format_Grayscale8)
    bpl = small.bytesPerLine()
    buf = np.frombuffer(bytes(small.constBits()), dtype=np.uint8)[: bpl * small.height()]
    return buf.reshape(small.height(), bpl)[:, : small.width()].astype(np.int16)


def exclude_from_capture(widget) -> bool:
    """Скрыть окно от снимков экрана (Windows 10 2004+).

    Тогда перевод поверх облаков не попадает в следующий снимок,
    и режим «Авто» работает без мигания.
    """
    if sys.platform != "win32":
        return False
    try:
        import ctypes
        hwnd = int(widget.winId())
        wda_exclude_from_capture = 0x11
        return bool(ctypes.windll.user32.SetWindowDisplayAffinity(hwnd, wda_exclude_from_capture))
    except Exception:  # noqa: BLE001
        return False
