"""Выделение области мышкой поверх замороженного снимка экрана (как «Ножницы»)."""

from __future__ import annotations

from PySide6.QtCore import QPoint, QRect, Qt, Signal
from PySide6.QtGui import QColor, QCursor, QFont, QGuiApplication, QPainter, QPen, QRegion
from PySide6.QtWidgets import QWidget

from ..capture import Region, grab_screen
from ..i18n import tr


class SelectionWindow(QWidget):
    selected = Signal(object)   # Region
    cancelled = Signal()

    def __init__(self, full_width: bool = False):
        self.full_width = full_width   # выделяется только высота, ширина — весь экран
        super().__init__(None, Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint
                         | Qt.WindowType.Tool)
        self.screen_obj = QGuiApplication.screenAt(QCursor.pos()) or QGuiApplication.primaryScreen()
        self.shot = grab_screen(self.screen_obj)
        self.setGeometry(self.screen_obj.geometry())
        self.setCursor(Qt.CursorShape.CrossCursor)
        self.setMouseTracking(True)
        self._start: QPoint | None = None
        self._end: QPoint | None = None

    def start(self) -> None:
        self.show()
        self.setGeometry(self.screen_obj.geometry())
        self.raise_()
        self.activateWindow()
        self.setFocus()

    # ------------------------------------------------------------ рисование
    def _rect(self) -> QRect:
        if self._start is None or self._end is None:
            return QRect()
        r = QRect(self._start, self._end).normalized()
        if self.full_width:
            r = QRect(0, r.y(), self.width(), r.height())
        return r

    def paintEvent(self, _):
        p = QPainter(self)
        p.drawPixmap(0, 0, self.shot)
        r = self._rect()
        # затемняем всё, кроме выделенной области
        dim = QRegion(self.rect())
        if not r.isNull():
            dim = dim.subtracted(QRegion(r))
        p.setClipRegion(dim)
        p.fillRect(self.rect(), QColor(33, 24, 22, 140))
        p.setClipping(False)
        if not r.isNull():
            p.setPen(QPen(QColor(172, 134, 83), 2))
            p.drawRect(r)
            p.setFont(QFont("Segoe UI", 10))
            p.setPen(QColor(255, 255, 255))
            p.drawText(r.x() + 4, r.y() - 6, f"{r.width()} × {r.height()}")
        else:
            p.setFont(QFont("Segoe UI", 14))
            p.setPen(QColor(255, 255, 255))
            p.drawText(self.rect().adjusted(0, 40, 0, 0), Qt.AlignmentFlag.AlignHCenter,
                       (tr("Проведите мышкой сверху вниз по высоте области  ·  Esc — отмена") if self.full_width
                        else tr("Выделите область чтения мышкой  ·  Esc — отмена")))
        p.end()

    # ------------------------------------------------------------ мышь и клавиши
    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self._start = self._end = e.position().toPoint()
            self.update()
        elif e.button() == Qt.MouseButton.RightButton:
            self._cancel()

    def mouseMoveEvent(self, e):
        if self._start is not None:
            self._end = e.position().toPoint()
            self.update()

    def mouseReleaseEvent(self, e):
        if e.button() != Qt.MouseButton.LeftButton or self._start is None:
            return
        r = self._rect()
        self.close()
        if (not self.full_width and r.width() < 20) or r.height() < 20:
            self.cancelled.emit()
            return
        self.selected.emit(Region(self.screen_obj.name(), r.x(), r.y(), r.width(), r.height()))

    def keyPressEvent(self, e):
        if e.key() == Qt.Key.Key_Escape:
            self._cancel()

    def _cancel(self):
        self.close()
        self.cancelled.emit()
