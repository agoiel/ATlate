"""Окно Студии: перевод главы целиком — найти текст, перевести, стереть оригинал, вписать перевод.

Слева — страницы главы, в центре — страница (оригинал, без текста или с переводом),
справа — блоки текста страницы: оригинал, перевод и настройки каждого блока.
"""

from __future__ import annotations

import shutil
import traceback
from pathlib import Path

import numpy as np
from PIL import Image
from PySide6.QtCore import QObject, QPointF, QRectF, QRunnable, QSize, Qt, QThreadPool, QTimer, QUrl, Signal
from PySide6.QtGui import (QBrush, QColor, QDesktopServices, QFont, QIcon, QImage, QKeySequence, QPainter,
                           QPen, QPixmap, QShortcut)
from PySide6.QtWidgets import (QApplication, QFileDialog, QFrame, QGraphicsItem, QGraphicsPixmapItem,
                               QGraphicsRectItem, QGraphicsScene, QGraphicsView, QHBoxLayout, QLabel,
                               QListWidget, QListWidgetItem, QMenu, QMessageBox, QPlainTextEdit, QProgressBar,
                               QScrollArea, QSplitter, QVBoxLayout, QWidget)

from .. import angle, languages, sysload
from ..bubbles import _ring_stats
from ..models import Box
from ..pipeline import Pipeline
from ..ui import theme
from ..ui.widgets import (Card, ElideLabel, ScrollContent, Segmented, Select, Stepper, SwitchRow, Toggle, caption,
                          label, pill)
from . import engine, fonts
from .project import ARCHIVE_EXT, IMAGE_EXT, Block, Page, Project

KIND_NAMES = {"bubble": "Облако", "box": "Плашка", "free": "Надпись"}


def to_qimage(arr: np.ndarray) -> QImage:
    arr = np.ascontiguousarray(arr[..., :3])
    h, w = arr.shape[:2]
    return QImage(arr.data, w, h, 3 * w, QImage.Format.Format_RGB888).copy()


# ==================================================================== фоновые задачи

class _Signals(QObject):
    progress = Signal(int, int, str)      # сделано, всего, что сейчас
    page_done = Signal(int)
    finished = Signal(str)
    rendered = Signal(int, int, object)   # страница, номер запроса, (Rendered, картинки для показа)
    failed = Signal(str)
    thumb = Signal(int, object)           # страница, QImage миниатюры


class _ChapterJob(QRunnable):
    """Обработать страницы по очереди: найти текст, перевести.

    Картинки здесь не рисуются (это делается только для открытой страницы и при выгрузке),
    программа работает с пониженным приоритетом, а пауза и остановка срабатывают
    в любой момент — между проходами распознавания, а не только между страницами.
    """

    def __init__(self, pipeline: Pipeline, project: Project, indices: list[int], retranslate: bool):
        super().__init__()
        self.pipeline, self.project, self.indices, self.retranslate = pipeline, project, indices, retranslate
        self.signals = _Signals()
        self.control = sysload.Control()
        self.priority = sysload.LowPriority()

    @property
    def cancelled(self) -> bool:
        return self.control.cancelled

    def _level(self) -> str:
        return self.pipeline.cfg.get("studio_load", sysload.DEFAULT_LOAD)

    def _checkpoint(self) -> None:
        # нагрузку можно поменять прямо во время перевода — приоритет подстраивается сразу
        self.priority.set(sysload.wants_low_priority(self._level()))
        self.control.checkpoint()

    def run(self):
        done = 0
        errors = 0
        try:
            for i in self.indices:
                page = self.project.pages[i]
                self.signals.progress.emit(done, len(self.indices), page.file)
                try:
                    self._checkpoint()
                    engine.prepare_page(self.pipeline, self.project, page, reset=self.retranslate,
                                        checkpoint=self._checkpoint)
                except sysload.Cancelled:
                    break
                except Exception as e:  # noqa: BLE001
                    page.status = "error"
                    page.error = str(e) or traceback.format_exc(limit=1)
                if page.status == "error":
                    errors += 1
                try:
                    self.project.save()
                except OSError:
                    pass
                done += 1
                self.signals.page_done.emit(i)
        finally:
            self.priority.release()
        self.signals.progress.emit(done, len(self.indices), "")
        msg = f"Остановлено. Готово страниц: {done} из {len(self.indices)}." if self.cancelled \
            else f"Готово: страниц {done}."
        if errors:
            msg += f" С ошибками: {errors} — подробности на странице."
        self.signals.finished.emit(msg)


class _RenderJob(QRunnable):
    """Нарисовать открытую страницу. Клин берётся готовый, если блоки не двигали."""

    def __init__(self, project: Project, page_index: int, token: int, original: np.ndarray | None,
                 clean: np.ndarray | None):
        super().__init__()
        self.project, self.index, self.token, self.original = project, page_index, token, original
        self.clean = clean
        self.signals = _Signals()

    def run(self):
        try:
            r = engine.render(self.project, self.project.pages[self.index], self.original, self.clean)
            # перевод в QImage тоже здесь, а не в окне: на длинной ленте это заметная работа
            images = {"clean": to_qimage(r.clean), "result": to_qimage(np.asarray(r.result))}
        except Exception:  # noqa: BLE001
            self.signals.failed.emit(traceback.format_exc())
            return
        self.signals.rendered.emit(self.index, self.token, (r, images))


class _ExportJob(QRunnable):
    def __init__(self, project: Project, level: str):
        super().__init__()
        self.project, self.level = project, level
        self.signals = _Signals()

    def run(self):
        pages = self.project.pages
        failed = 0
        prio = sysload.LowPriority()
        prio.set(sysload.wants_low_priority(self.level))
        try:
            for i, page in enumerate(pages):
                self.signals.progress.emit(i, len(pages), page.file)
                try:
                    engine.export_page(self.project, page)
                except Exception:  # noqa: BLE001
                    failed += 1
        finally:
            prio.release()
        self.signals.progress.emit(len(pages), len(pages), "")
        msg = f"Выгружено страниц: {len(pages) - failed}."
        if failed:
            msg += f" Не удалось: {failed}."
        self.signals.finished.emit(msg)


THUMB_W, THUMB_H = 168, 232


def make_thumb(path: Path) -> QImage:
    """Миниатюра страницы; у длинной ленты — её верх."""
    with Image.open(path) as im:
        k = min(THUMB_W / im.width, THUMB_H / im.height)
        size = (max(1, int(im.width * k)), max(1, int(im.height * k)))
        # JPEG сразу читается уменьшенным — не нужно разжимать страницу целиком
        im.draft("RGB", size)
        small = im.convert("RGB").resize(size)
    return to_qimage(np.asarray(small)[:THUMB_H])


class _ThumbJob(QRunnable):
    """Миниатюры страниц в фоне: раньше они читались в окне и подвешивали его."""

    def __init__(self, project: Project):
        super().__init__()
        self.project = project
        self.signals = _Signals()
        self.stopped = False

    def run(self):
        for i, page in enumerate(list(self.project.pages)):
            if self.stopped:
                return
            try:
                img = make_thumb(self.project.image_path(page))
            except (OSError, ValueError):
                continue
            self.signals.thumb.emit(i, img)


# ==================================================================== страница с блоками

class BlockItem(QGraphicsRectItem):
    """Рамка блока на странице: выделяется, двигается, растягивается за правый нижний угол."""

    HANDLE = 14

    def __init__(self, view: "PageView", block: Block, number: int):
        super().__init__(0, 0, block.box.w, block.box.h)
        self.view, self.block, self.number = view, block, number
        self.setPos(block.box.x, block.box.y)
        self.setFlags(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable | QGraphicsItem.GraphicsItemFlag.ItemIsMovable)
        self.setAcceptHoverEvents(True)
        self.setCursor(Qt.CursorShape.SizeAllCursor)
        self._resizing = False
        self.overflow = False

    def paint(self, p: QPainter, _opt, _w=None):
        c = theme.palette()
        r = self.rect()
        on = self.block.enabled
        color = QColor(c.primary if on else "#9A8A80")
        if self.overflow:
            color = QColor("#C0392B")
        sel = self.isSelected()
        if sel:
            p.fillRect(r, QColor(color.red(), color.green(), color.blue(), 28))
        pen = QPen(color, 2.5 if sel else 1.6, Qt.PenStyle.SolidLine if sel else Qt.PenStyle.DashLine)
        pen.setCosmetic(True)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRect(r)
        if abs(self.block.angle) >= 0.5:
            # наклонённый текст: тонкой линией — как он стоит на самом деле
            length, thick = angle.oriented_size(r.width(), r.height(), self.block.src_angle)
            tilt_pen = QPen(color, 1.2)
            tilt_pen.setCosmetic(True)
            p.save()
            p.setPen(tilt_pen)
            p.translate(r.center())
            p.rotate(-self.block.angle)
            p.drawRect(QRectF(-length / 2, -thick / 2, length, thick))
            p.restore()
        # номер блока
        k = 1 / max(0.05, self.view.transform().m11())
        d = 22 * k
        badge = QRectF(r.left() - d / 2, r.top() - d / 2, d, d)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(color)
        p.drawEllipse(badge)
        f = QFont()
        f.setPixelSize(max(1, int(12 * k)))
        f.setBold(True)
        p.setFont(f)
        p.setPen(QColor(c.on_primary))
        p.drawText(badge, int(Qt.AlignmentFlag.AlignCenter.value), str(self.number))
        if sel:
            h = self.HANDLE * k
            p.setBrush(color)
            p.drawRect(QRectF(r.right() - h / 2, r.bottom() - h / 2, h, h))

    def _on_handle(self, pos: QPointF) -> bool:
        k = 1 / max(0.05, self.view.transform().m11())
        h = self.HANDLE * k
        r = self.rect()
        return abs(pos.x() - r.right()) <= h and abs(pos.y() - r.bottom()) <= h

    def hoverMoveEvent(self, e):
        self.setCursor(Qt.CursorShape.SizeFDiagCursor if self.isSelected() and self._on_handle(e.pos())
                       else Qt.CursorShape.SizeAllCursor)
        super().hoverMoveEvent(e)

    def mousePressEvent(self, e):
        if self.isSelected() and self._on_handle(e.pos()):
            self._resizing = True
            e.accept()
            return
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e):
        if self._resizing:
            r = self.rect()
            self.prepareGeometryChange()
            self.setRect(QRectF(0, 0, max(12, e.pos().x()), max(12, e.pos().y())))
            if r != self.rect():
                self.update()
            return
        super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e):
        was_resizing = self._resizing
        self._resizing = False
        super().mouseReleaseEvent(e)
        pos, r = self.pos(), self.rect()
        new = Box(pos.x(), pos.y(), r.width(), r.height())
        old = self.block.box
        if was_resizing or abs(new.x - old.x) > 0.5 or abs(new.y - old.y) > 0.5:
            self.view.on_geometry(self.block, new)


class PageView(QGraphicsView):
    selected = Signal(str)            # id блока
    geometry_changed = Signal(str)
    add_requested = Signal(object)    # Box нового блока
    delete_requested = Signal(str)

    def __init__(self):
        super().__init__()
        self.setObjectName("pageView")
        self.scene_ = QGraphicsScene(self)
        self.setScene(self.scene_)
        self.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.SmoothPixmapTransform)
        self.setDragMode(QGraphicsView.DragMode.NoDrag)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.pix = QGraphicsPixmapItem()
        self.pix.setTransformationMode(Qt.TransformationMode.SmoothTransformation)
        self.scene_.addItem(self.pix)
        self.items_: dict[str, BlockItem] = {}
        self.show_boxes = True
        self.add_mode = False
        self._rubber: QGraphicsRectItem | None = None
        self._start: QPointF | None = None
        self._fit = True
        self.scene_.selectionChanged.connect(self._on_selection)

    # ------------------------------------------------------------ содержимое
    def set_image(self, img: QImage, keep_view: bool = True) -> None:
        self.pix.setPixmap(QPixmap.fromImage(img))
        self.scene_.setSceneRect(QRectF(0, 0, img.width(), img.height()))
        if not keep_view:
            self._fit = True
            self.fit_width()

    def set_blocks(self, blocks: list[Block], overflow: set[str] | None = None) -> None:
        sel = self.selected_id()
        for it in self.items_.values():
            self.scene_.removeItem(it)
        self.items_.clear()
        for n, b in enumerate(blocks, 1):
            it = BlockItem(self, b, n)
            it.overflow = bool(overflow and b.id in overflow)
            it.setVisible(self.show_boxes)
            self.scene_.addItem(it)
            self.items_[b.id] = it
        if sel and sel in self.items_:
            self.items_[sel].setSelected(True)

    def set_overflow(self, overflow: set[str]) -> None:
        for bid, it in self.items_.items():
            it.overflow = bid in overflow
            it.update()

    def set_boxes_visible(self, on: bool) -> None:
        self.show_boxes = on
        for it in self.items_.values():
            it.setVisible(on)

    def selected_id(self) -> str:
        for bid, it in self.items_.items():
            if it.isSelected():
                return bid
        return ""

    def select(self, bid: str, center: bool = True) -> None:
        for k, it in self.items_.items():
            it.setSelected(k == bid)
        if center and bid in self.items_:
            self.ensureVisible(self.items_[bid], 60, 60)

    def on_geometry(self, block: Block, new: Box) -> None:
        old = block.box
        sx = new.w / max(1.0, old.w)
        sy = new.h / max(1.0, old.h)
        block.lines = [Box(new.x + (ln.x - old.x) * sx, new.y + (ln.y - old.y) * sy, ln.w * sx, ln.h * sy)
                       for ln in block.lines]
        if block.area is not None:
            block.area = new
        block.line_h *= sy
        block.box = new
        self.geometry_changed.emit(block.id)

    def _on_selection(self) -> None:
        self.selected.emit(self.selected_id())

    # ------------------------------------------------------------ масштаб
    def fit_width(self) -> None:
        r = self.scene_.sceneRect()
        if r.width() <= 0:
            return
        k = (self.viewport().width() - 8) / r.width()
        self.setTransform(self.transform().fromScale(k, k))
        self.horizontalScrollBar().setValue(0)
        self.verticalScrollBar().setValue(0)

    def zoom(self, factor: float) -> None:
        self._fit = False
        k = self.transform().m11() * factor
        if 0.05 <= k <= 8:
            self.scale(factor, factor)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        if self._fit:
            pos = self.verticalScrollBar().value()
            self.fit_width()
            self.verticalScrollBar().setValue(pos)

    def wheelEvent(self, e):
        if e.modifiers() & Qt.KeyboardModifier.ControlModifier:
            self.zoom(1.15 if e.angleDelta().y() > 0 else 1 / 1.15)
            e.accept()
            return
        super().wheelEvent(e)

    # ------------------------------------------------------------ новый блок рамкой
    def start_add(self) -> None:
        self.add_mode = True
        self.viewport().setCursor(Qt.CursorShape.CrossCursor)

    def _stop_add(self) -> None:
        self.add_mode = False
        self.viewport().unsetCursor()
        if self._rubber is not None:
            self.scene_.removeItem(self._rubber)
            self._rubber = None
        self._start = None

    def mousePressEvent(self, e):
        if self.add_mode and e.button() == Qt.MouseButton.LeftButton:
            self._start = self.mapToScene(e.position().toPoint())
            self._rubber = QGraphicsRectItem()
            pen = QPen(QColor(theme.palette().primary), 2, Qt.PenStyle.DashLine)
            pen.setCosmetic(True)
            self._rubber.setPen(pen)
            self._rubber.setBrush(QBrush(QColor(172, 134, 83, 40)))
            self.scene_.addItem(self._rubber)
            return
        if self.add_mode:
            self._stop_add()
            return
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e):
        if self.add_mode and self._start is not None and self._rubber is not None:
            p = self.mapToScene(e.position().toPoint())
            self._rubber.setRect(QRectF(self._start, p).normalized())
            return
        super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e):
        if self.add_mode and self._rubber is not None:
            r = self._rubber.rect().intersected(self.scene_.sceneRect())
            self._stop_add()
            if r.width() >= 10 and r.height() >= 10:
                self.add_requested.emit(Box(r.x(), r.y(), r.width(), r.height()))
            return
        super().mouseReleaseEvent(e)

    def keyPressEvent(self, e):
        if e.key() in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace) and self.selected_id():
            self.delete_requested.emit(self.selected_id())
            return
        if e.key() == Qt.Key.Key_Escape and self.add_mode:
            self._stop_add()
            return
        super().keyPressEvent(e)


# ==================================================================== карточка блока

def _text_edit(text: str, lines: int, placeholder: str) -> QPlainTextEdit:
    ed = QPlainTextEdit(text)
    ed.setPlaceholderText(placeholder)
    fm = ed.fontMetrics()
    ed.setFixedHeight(int(fm.lineSpacing() * lines + 22))
    ed.setTabChangesFocus(True)
    return ed


class BlockCard(QFrame):
    changed = Signal(str, bool)       # id, нужно ли перерисовать страницу
    clicked = Signal(str)
    retranslate = Signal(str)
    delete = Signal(str)

    def __init__(self, block: Block, number: int):
        super().__init__()
        self.block = block
        self.setObjectName("blockCard")
        self.setProperty("selected", "false")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 10, 12, 12)
        lay.setSpacing(6)

        head = QHBoxLayout()
        head.setSpacing(8)
        num = QLabel(str(number))
        num.setObjectName("blockNumber")
        num.setFixedSize(24, 24)
        num.setAlignment(Qt.AlignmentFlag.AlignCenter)
        head.addWidget(num)
        kind = ElideLabel(KIND_NAMES.get(block.kind, block.kind) + (" · добавлен вручную" if block.manual else ""),
                          "fieldLabel")
        head.addWidget(kind, 1)
        self.enabled = Toggle(block.enabled)
        self.enabled.setToolTip("Стирать оригинал и вписывать перевод")
        self.enabled.toggled.connect(self._on_enabled)
        head.addWidget(self.enabled)
        lay.addLayout(head)

        self.source = _text_edit(block.source, 2, "Оригинал (можно поправить и перевести заново)")
        self.source.setObjectName("sourceEdit")
        self.source.textChanged.connect(self._on_source)
        lay.addWidget(self.source)
        self.translation = _text_edit(block.translation, 3, "Перевод")
        self.translation.textChanged.connect(self._on_translation)
        lay.addWidget(self.translation)

        # подробности — только у выбранного блока
        self.more = QWidget()
        ml = QVBoxLayout(self.more)
        ml.setContentsMargins(0, 4, 0, 0)
        ml.setSpacing(8)
        row = QHBoxLayout()
        row.setSpacing(8)
        self.clean = self._select([("auto", "Стирать: авто"), ("fill", "Залить цветом фона"),
                                   ("inpaint", "Дорисовать фон"), ("none", "Не стирать")], block.clean)
        self.clean.currentIndexChanged.connect(lambda _i: self._set("clean", self.clean.currentData()))
        row.addWidget(self.clean, 1)
        sizes = [(0, "Размер: авто")] + [(s, f"{s} px") for s in range(10, 74, 2)]
        self.size = self._select(sizes, block.size)
        self.size.currentIndexChanged.connect(lambda _i: self._set("size", int(self.size.currentData())))
        row.addWidget(self.size, 1)
        ml.addLayout(row)
        row2 = QHBoxLayout()
        row2.setSpacing(8)
        self.color = self._select([("", "Цвет: авто"), ("#141414", "Чёрный"), ("#FAFAFA", "Белый"),
                                   ("#C0392B", "Красный")], block.color)
        self.color.currentIndexChanged.connect(lambda _i: self._set("color", self.color.currentData()))
        row2.addWidget(self.color, 1)
        self.outline = self._select([("auto", "Обводка: авто"), ("on", "С обводкой"), ("off", "Без обводки")],
                                    block.outline)
        self.outline.currentIndexChanged.connect(lambda _i: self._set("outline", self.outline.currentData()))
        row2.addWidget(self.outline, 1)
        ml.addLayout(row2)
        self.in_box = SwitchRow("Вписывать в рамку блока", "Иначе — во всё облако целиком",
                                block.area is not None)
        self.in_box.toggled.connect(self._on_in_box)
        ml.addWidget(self.in_box)
        self.tilt = Stepper("Наклон текста", "Плюс — правый край выше", float(block.angle),
                            1, -90, 90, lambda v: "ровно" if abs(v) < 0.5 else f"{v:+.0f}°")
        ml.addWidget(self.tilt)       # что делать с новым углом, решает окно: ему видна картинка
        btns = QHBoxLayout()
        btns.setSpacing(8)
        again = pill("Перевести заново", "tonal", "refresh")
        again.clicked.connect(lambda: self.retranslate.emit(self.block.id))
        btns.addWidget(again)
        btns.addStretch(1)
        rm = pill("Удалить", "outline", "trash")
        rm.clicked.connect(lambda: self.delete.emit(self.block.id))
        btns.addWidget(rm)
        ml.addLayout(btns)
        self.more.setVisible(False)
        lay.addWidget(self.more)
        self.overflow = caption("Перевод не помещается — уменьшите текст или растяните рамку блока.")
        self.overflow.setStyleSheet("color: #C0392B;")
        self.overflow.setVisible(False)
        lay.addWidget(self.overflow)

    @staticmethod
    def _select(items, current) -> Select:
        s = Select()
        for key, title in items:
            s.addItem(title, key)
        s.setCurrentIndex(max(0, s.findData(current)))
        return s

    def set_selected(self, on: bool) -> None:
        self.setProperty("selected", "true" if on else "false")
        self.style().unpolish(self)
        self.style().polish(self)
        self.more.setVisible(on)

    def set_overflow(self, on: bool) -> None:
        self.overflow.setVisible(on)

    def set_translation(self, text: str) -> None:
        self.translation.blockSignals(True)
        self.translation.setPlainText(text)
        self.translation.blockSignals(False)

    def mousePressEvent(self, e):
        self.clicked.emit(self.block.id)
        super().mousePressEvent(e)

    def _set(self, key: str, value) -> None:
        setattr(self.block, key, value)
        self.changed.emit(self.block.id, True)

    def _on_enabled(self, on: bool) -> None:
        self._set("enabled", on)

    def _on_in_box(self, on: bool) -> None:
        self.block.area = self.block.box if on else None
        self.changed.emit(self.block.id, True)

    def _on_source(self) -> None:
        self.block.source = self.source.toPlainText().strip()
        self.changed.emit(self.block.id, False)

    def _on_translation(self) -> None:
        self.block.translation = self.translation.toPlainText().strip()
        self.changed.emit(self.block.id, True)


# ==================================================================== окно

class StudioWindow(QWidget):
    """Отдельное окно Студии. settings_changed — языки или переводчик поменяли здесь."""

    settings_changed = Signal()

    def __init__(self, cfg, parent=None):
        super().__init__(parent, Qt.WindowType.Window)
        self.cfg = cfg
        self.pipeline = Pipeline(cfg)
        self.project: Project | None = None
        self.index = -1
        self.pool = QThreadPool(self)
        self.pool.setMaxThreadCount(2)
        self.job: _ChapterJob | None = None
        self._jobs: set = set()
        # миниатюры читаются отдельно, по одной, чтобы не мешать переводу и отрисовке
        self.thumb_pool = QThreadPool(self)
        self.thumb_pool.setMaxThreadCount(1)
        self._thumb_job: _ThumbJob | None = None
        self._thumbs: dict[int, QPixmap] = {}
        self._render_token = 0
        self._render_running = False       # отрисовка идёт — новая подождёт её окончания
        self._render_again = False
        self._rendered = None
        self._images: dict[str, QImage] = {}
        self._clean_cache: tuple | None = None   # (страница, ключ клина, картинка без текста)
        self._original: np.ndarray | None = None
        self._busy_pages: set[int] = set()
        self.view_mode = "result"
        self.cards: dict[str, BlockCard] = {}
        self._progress_text = ""

        self.setObjectName("root")
        self.setWindowTitle("ATlate — Студия перевода")
        self.setWindowIcon(QIcon(str(theme.resource("icon.png"))))
        self.setAcceptDrops(True)
        self.resize(1320, 860)

        self.render_timer = QTimer(self)
        self.render_timer.setSingleShot(True)
        self.render_timer.timeout.connect(self._render_now)
        self.save_timer = QTimer(self)
        self.save_timer.setSingleShot(True)
        self.save_timer.timeout.connect(self._save)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(self._build_header())
        self.stack_empty = self._build_empty()
        self.body = self._build_body()
        root.addWidget(self.stack_empty, 1)
        root.addWidget(self.body, 1)
        self.body.setVisible(False)
        root.addWidget(self._build_footer())

        QShortcut(QKeySequence("Ctrl+S"), self, activated=self._save)
        app = QApplication.instance()
        if app is not None:
            # выход из программы, пока перевод на паузе, не должен зависнуть
            app.aboutToQuit.connect(self._shutdown)
        QShortcut(QKeySequence(Qt.Key.Key_PageDown), self, activated=lambda: self.go(self.index + 1))
        QShortcut(QKeySequence(Qt.Key.Key_PageUp), self, activated=lambda: self.go(self.index - 1))
        QShortcut(QKeySequence("Ctrl+0"), self, activated=self.view.fit_width)

    # ---------------------------------------------------------------- шапка
    def _build_header(self) -> QWidget:
        bar = QFrame()
        bar.setObjectName("studioBar")
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(16, 10, 16, 10)
        lay.setSpacing(10)
        icon = QLabel()
        icon.setPixmap(theme.pixmap("brush", theme.CREAM, 26))
        lay.addWidget(icon)
        col = QVBoxLayout()
        col.setSpacing(0)
        title = QLabel("Студия перевода")
        title.setObjectName("studioTitle")
        col.addWidget(title)
        self.chapter = ElideLabel("Откройте главу: папку с картинками или архив", "studioSub")
        col.addWidget(self.chapter)
        lay.addLayout(col, 1)

        open_btn = pill("Открыть", "heroOutline", "folder")
        menu = QMenu(open_btn)
        menu.addAction("Папку с главой…", self.open_folder)
        menu.addAction("Архив .zip / .cbz…", self.open_archive)
        menu.addAction("Картинки…", self.open_images)
        open_btn.setMenu(menu)
        lay.addWidget(open_btn)
        self.run_btn = pill("Перевести главу", "hero", "play",
                            "Найти текст на всех страницах, перевести, стереть оригинал и вписать перевод")
        self.run_btn.clicked.connect(self.run_chapter)
        lay.addWidget(self.run_btn)
        self.export_btn = pill("Выгрузить", "heroOutline", "download", "Сохранить готовые страницы в папку")
        self.export_btn.clicked.connect(self.export)
        lay.addWidget(self.export_btn)
        return bar

    def _build_empty(self) -> QWidget:
        w = QWidget()
        w.setObjectName("studioEmpty")
        outer = QVBoxLayout(w)
        outer.addStretch(1)
        card = QFrame()
        card.setObjectName("dropZone")
        card.setMaximumWidth(620)
        cl = QVBoxLayout(card)
        cl.setContentsMargins(36, 32, 36, 32)
        cl.setSpacing(12)
        ic = QLabel()
        ic.setPixmap(theme.pixmap("brush", theme.palette().primary, 48))
        ic.setAlignment(Qt.AlignmentFlag.AlignCenter)
        cl.addWidget(ic)
        t = QLabel("Перетащите сюда папку с главой")
        t.setObjectName("cardTitle")
        t.setAlignment(Qt.AlignmentFlag.AlignCenter)
        cl.addWidget(t)
        s = caption("Или архив .zip / .cbz, или несколько картинок. Студия найдёт текст на каждой странице, "
                    "переведёт его, сотрёт оригинал и впишет перевод. Всё можно поправить вручную, "
                    "а готовые страницы — выгрузить в папку.")
        s.setAlignment(Qt.AlignmentFlag.AlignCenter)
        cl.addWidget(s)
        row = QHBoxLayout()
        row.addStretch(1)
        for text, kind, icon, fn in (("Папка", "primary", "folder", self.open_folder),
                                     ("Архив", "tonal", "book", self.open_archive),
                                     ("Картинки", "tonal", "image", self.open_images)):
            b = pill(text, kind, icon)
            b.clicked.connect(fn)
            row.addWidget(b)
        row.addStretch(1)
        cl.addLayout(row)
        h = QHBoxLayout()
        h.addStretch(1)
        h.addWidget(card, 4)
        h.addStretch(1)
        outer.addLayout(h)
        outer.addStretch(2)
        return w

    # ---------------------------------------------------------------- основная часть
    def _build_body(self) -> QWidget:
        split = QSplitter(Qt.Orientation.Horizontal)
        split.setObjectName("studioSplit")
        split.setChildrenCollapsible(False)

        # страницы
        self.pages = QListWidget()
        self.pages.setObjectName("pageList")
        self.pages.setIconSize(QSize(84, 116))
        self.pages.setSpacing(4)
        self.pages.setUniformItemSizes(True)
        self.pages.currentRowChanged.connect(self.go)
        self.pages.setMinimumWidth(120)
        self.pages.setMaximumWidth(170)
        split.addWidget(self.pages)

        # страница
        center = QWidget()
        cl = QVBoxLayout(center)
        cl.setContentsMargins(12, 10, 12, 0)
        cl.setSpacing(8)
        tools = QHBoxLayout()
        tools.setSpacing(8)
        self.mode = Segmented([("original", "Оригинал"), ("clean", "Без текста"), ("result", "Перевод")], "result")
        self.mode.setObjectName("segmentedPlain")
        self.mode.changed.connect(self._set_view_mode)
        tools.addWidget(self.mode)
        self.boxes_toggle = pill("Рамки", "tonal", "select", "Показать или скрыть рамки блоков")
        self.boxes_toggle.setCheckable(True)
        self.boxes_toggle.setChecked(True)
        self.boxes_toggle.toggled.connect(lambda on: self.view.set_boxes_visible(on))
        tools.addWidget(self.boxes_toggle)
        add = pill("Блок", "tonal", "plus", "Обвести рамкой текст, который не нашёлся")
        add.clicked.connect(lambda: self.view.start_add())
        tools.addWidget(add)
        tools.addStretch(1)
        self.page_btn = pill("Перевести страницу", "outline", "refresh",
                             "Найти текст на этой странице заново и перевести")
        self.page_btn.clicked.connect(self.run_page)
        tools.addWidget(self.page_btn)
        cl.addLayout(tools)
        self.view = PageView()
        self.view.selected.connect(self._on_view_selected)
        self.view.geometry_changed.connect(lambda _bid: self._changed(True))
        self.view.add_requested.connect(self._add_block)
        self.view.delete_requested.connect(self._delete_block)
        cl.addWidget(self.view, 1)
        self.page_note = caption("")
        self.page_note.setVisible(False)
        cl.addWidget(self.page_note)
        split.addWidget(center)

        # блоки и оформление
        side = QScrollArea()
        side.setObjectName("studioSide")
        side.setWidgetResizable(True)
        side.setFrameShape(QFrame.Shape.NoFrame)
        side.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        content = ScrollContent()
        content.setObjectName("content")
        self.side_lay = QVBoxLayout(content)
        self.side_lay.setContentsMargins(4, 10, 14, 14)
        self.side_lay.setSpacing(10)
        self.side_lay.addWidget(self._build_languages())
        self.side_lay.addWidget(self._build_style())
        self.blocks_title = label("БЛОКИ СТРАНИЦЫ", "fieldLabel")
        self.side_lay.addWidget(self.blocks_title)
        self.blocks_box = QWidget()
        self.blocks_lay = QVBoxLayout(self.blocks_box)
        self.blocks_lay.setContentsMargins(0, 0, 0, 0)
        self.blocks_lay.setSpacing(8)
        self.side_lay.addWidget(self.blocks_box)
        self.side_lay.addStretch(1)
        side.setWidget(content)
        side.setMinimumWidth(340)
        self.side = side
        split.addWidget(side)
        split.setStretchFactor(0, 0)
        split.setStretchFactor(1, 1)
        split.setStretchFactor(2, 0)
        split.setSizes([150, 760, 400])
        return split

    def _build_languages(self) -> Card:
        card = Card("Перевод", "Общие с главным окном настройки", "lang", collapsible=True, collapsed=True)
        src = Select()
        for k, v in languages.SOURCE_LANGUAGES.items():
            src.addItem(v["name"], k)
        src.setCurrentIndex(max(0, src.findData(self.cfg["source_lang"])))
        src.currentIndexChanged.connect(lambda _i: self._set_cfg("source_lang", src.currentData()))
        tgt = Select()
        for k, v in languages.TARGET_LANGUAGES.items():
            tgt.addItem(v["name"], k)
        tgt.setCurrentIndex(max(0, tgt.findData(self.cfg["target_lang"])))
        tgt.currentIndexChanged.connect(lambda _i: self._set_cfg("target_lang", tgt.currentData()))
        from ..ui.main_window import TRANSLATOR_INFO
        tr = Select()
        for key, title, _sub, _grp in TRANSLATOR_INFO:
            tr.addItem(title, key)
        tr.setCurrentIndex(max(0, tr.findData(self.cfg["translator"])))
        tr.currentIndexChanged.connect(lambda _i: self._set_cfg("translator", tr.currentData()))
        load = Select()
        for k, v in sysload.LOAD_NAMES.items():
            load.addItem(v, k)
        load.setCurrentIndex(max(0, load.findData(self.cfg.get("studio_load", sysload.DEFAULT_LOAD))))
        load.setToolTip("Сколько ресурсов компьютера отдавать переводу главы. Можно менять прямо во время перевода")
        load.currentIndexChanged.connect(lambda _i: self._set_load(load.currentData()))
        for cap_text, w in (("С языка", src), ("На язык", tgt), ("Переводчик", tr), ("Нагрузка на ПК", load)):
            card.add(label(cap_text.upper(), "fieldLabel"))
            card.add(w)
        card.add(caption("Ключи переводчиков, глоссарий и тайтл — в главном окне."))
        return card

    def _build_style(self) -> Card:
        card = Card("Оформление", "Шрифт и размер текста перевода", "tune", collapsible=True, collapsed=True)
        self.font_select = Select()
        self._fill_fonts()
        self.font_select.currentIndexChanged.connect(self._on_font)
        card.add(label("ШРИФТ", "fieldLabel"))
        card.add(self.font_select)
        add = pill("Добавить шрифт…", "link", "plus", "Скопировать файл шрифта .ttf/.otf в папку fonts программы")
        add.clicked.connect(self._add_font)
        card.add(add)
        self.min_size = Stepper("Самый мелкий", "Мельче не уменьшается", 12, 1, 6, 40, lambda v: f"{int(v)} px")
        self.max_size = Stepper("Самый крупный", "Крупнее не бывает", 36, 2, 12, 96, lambda v: f"{int(v)} px")
        self.spacing = Stepper("Между строками", "", 1.1, 0.05, 0.8, 1.8, lambda v: f"×{v:.2f}")
        for st in (self.min_size, self.max_size, self.spacing):
            st.changed.connect(lambda _v: self._on_style())
            card.add(st)
        self.caps = SwitchRow("ЗАГЛАВНЫМИ БУКВАМИ", "Как принято во многих переводах", False)
        self.caps.toggled.connect(lambda _v: self._on_style())
        card.add(self.caps)
        self.bold = SwitchRow("Жирнее", "Тонкие шрифты читаются лучше", True)
        self.bold.toggled.connect(lambda _v: self._on_style())
        card.add(self.bold)
        self.tilt = SwitchRow("Повторять наклон", "Перевод под тем же углом, что и оригинал", True)
        self.tilt.toggled.connect(lambda _v: self._on_style())
        card.add(self.tilt)
        self.fmt = Select()
        for k, t in (("png", "Выгружать в PNG"), ("jpg", "Выгружать в JPG"), ("same", "Формат как у оригинала")):
            self.fmt.addItem(t, k)
        self.fmt.currentIndexChanged.connect(lambda _i: self._on_style())
        card.add(self.fmt)
        return card

    def _build_footer(self) -> QWidget:
        bar = QFrame()
        bar.setObjectName("studioFooter")
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(16, 6, 16, 6)
        lay.setSpacing(10)
        self.status = ElideLabel("Готов к работе.", "caption")
        lay.addWidget(self.status, 1)
        self.progress = QProgressBar()
        self.progress.setFixedWidth(220)
        self.progress.setTextVisible(False)
        self.progress.setVisible(False)
        lay.addWidget(self.progress)
        self.pause_btn = pill("Пауза", "outline", "pause",
                              "Приостановить перевод — компьютер сразу освобождается. Продолжить можно в любой момент")
        self.pause_btn.setVisible(False)
        self.pause_btn.clicked.connect(self.toggle_pause)
        lay.addWidget(self.pause_btn)
        self.stop_btn = pill("Остановить", "outline", "stop", "Прервать перевод. Готовые страницы сохранятся")
        self.stop_btn.setVisible(False)
        self.stop_btn.clicked.connect(self.stop)
        lay.addWidget(self.stop_btn)
        self.open_out = pill("Открыть папку", "link", "folder")
        self.open_out.setVisible(False)
        self.open_out.clicked.connect(self._open_out_dir)
        lay.addWidget(self.open_out)
        return bar

    # ---------------------------------------------------------------- открытие
    def open_folder(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "Папка с главой")
        if path:
            self.load(Path(path))

    def open_archive(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Архив главы", "", "Архивы (*.zip *.cbz)")
        if path:
            self.load(Path(path))

    def open_images(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(self, "Страницы главы", "",
                                                "Изображения (*.png *.jpg *.jpeg *.webp *.bmp)")
        if paths:
            files = [Path(p) for p in paths]
            self.load(files[0].parent, files)

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e):
        paths = [Path(u.toLocalFile()) for u in e.mimeData().urls() if u.isLocalFile()]
        if not paths:
            return
        if len(paths) == 1 and (paths[0].is_dir() or paths[0].suffix.lower() in ARCHIVE_EXT):
            self.load(paths[0])
        else:
            imgs = [p for p in paths if p.suffix.lower() in IMAGE_EXT]
            if imgs:
                self.load(imgs[0].parent, imgs)

    def load(self, path: Path, files: list[Path] | None = None) -> None:
        if self.job is not None:
            return
        try:
            proj = Project.open(path, files)
        except Exception as e:  # noqa: BLE001
            QMessageBox.warning(self, "Студия", f"Не удалось открыть главу:\n{e}")
            return
        if not proj.pages:
            QMessageBox.information(self, "Студия", "В этой папке нет картинок (png, jpg, webp).")
            return
        self.project = proj
        self._apply_style_to_controls()
        self.chapter.setText(f"{proj.title} · страниц: {len(proj.pages)} · {proj.root}")
        self.stack_empty.setVisible(False)
        self.body.setVisible(True)
        self.pages.blockSignals(True)
        self.pages.clear()
        for i, p in enumerate(proj.pages):
            it = QListWidgetItem(str(i + 1))
            it.setTextAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignBottom)
            it.setToolTip(p.file)
            self.pages.addItem(it)
        self.pages.blockSignals(False)
        self._start_thumbs()
        self._clean_cache = None
        self.index = -1
        self.pages.setCurrentRow(0)
        done = sum(1 for p in proj.pages if p.status == "done")
        self._status(f"Открыто страниц: {len(proj.pages)}" + (f", уже переведено: {done}." if done else
                                                              ". Нажмите «Перевести главу»."))

    # ---------------------------------------------------------------- миниатюры
    def _start_thumbs(self) -> None:
        if self._thumb_job is not None:
            self._thumb_job.stopped = True
        self._thumbs.clear()
        job = _ThumbJob(self.project)
        job.signals.thumb.connect(lambda i, img, j=job: self._on_thumb(j, i, img))
        job.setAutoDelete(False)
        self._thumb_job = job
        self.thumb_pool.start(job)

    def _on_thumb(self, job: _ThumbJob, i: int, img: QImage) -> None:
        if job is not self._thumb_job or job.stopped:
            return
        self._thumbs[i] = QPixmap.fromImage(img)
        self._update_thumb(i)

    def _update_thumb(self, i: int) -> None:
        """Значок состояния поверх готовой миниатюры (картинку заново не читаем)."""
        if self.project is None or i >= self.pages.count() or i not in self._thumbs:
            return
        page = self.project.pages[i]
        pm = QPixmap(self._thumbs[i])
        p = QPainter(pm)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        color = {"done": "#3E9B5B", "error": "#C0392B"}.get(page.status)
        if i in self._busy_pages:
            color = theme.SAND
        if color:
            p.setPen(QPen(QColor("white"), 3))
            p.setBrush(QColor(color))
            p.drawEllipse(pm.width() - 30, 8, 22, 22)
        p.end()
        self.pages.item(i).setIcon(QIcon(pm))

    # ---------------------------------------------------------------- страница
    def go(self, i: int) -> None:
        if self.project is None or not 0 <= i < len(self.project.pages) or i == self.index:
            return
        self.index = i
        if self.pages.currentRow() != i:
            self.pages.setCurrentRow(i)
        page = self.project.pages[i]
        try:
            self._original = engine.load_image(self.project.image_path(page))
        except OSError as e:
            self._status(f"Не удалось открыть {page.file}: {e}")
            return
        self._rendered = None
        self._images = {"original": to_qimage(self._original)}
        self.view.set_image(self._images["original"], keep_view=False)
        self._fill_cards()
        self._render_now()

    def _page(self) -> Page | None:
        if self.project is None or not 0 <= self.index < len(self.project.pages):
            return None
        return self.project.pages[self.index]

    def _fill_cards(self) -> None:
        page = self._page()
        while self.blocks_lay.count():
            it = self.blocks_lay.takeAt(0)
            if it.widget():
                it.widget().hide()
                it.widget().setParent(None)
                it.widget().deleteLater()
        self.cards.clear()
        if page is None:
            return
        busy = self.index in self._busy_pages
        self.view.set_blocks([] if busy else page.blocks)
        if busy:
            self.blocks_title.setText("СТРАНИЦА ОБРАБАТЫВАЕТСЯ…")
            return
        if page.status == "new":
            self.blocks_title.setText("ТЕКСТ ЕЩЁ НЕ ИСКАЛИ — НАЖМИТЕ «ПЕРЕВЕСТИ СТРАНИЦУ»")
        else:
            self.blocks_title.setText(f"БЛОКИ СТРАНИЦЫ · {len(page.blocks)}")
        note = page.error if page.status == "error" else ""
        self.page_note.setText(f"Ошибка: {note}" if note else "")
        self.page_note.setVisible(bool(note))
        for n, b in enumerate(page.blocks, 1):
            card = BlockCard(b, n)
            card.changed.connect(lambda _bid, redraw: self._changed(redraw))
            card.clicked.connect(lambda bid: self.view.select(bid))
            card.retranslate.connect(self._retranslate_block)
            card.tilt.changed.connect(lambda v, blk=b: self._on_tilt(blk, float(v)))
            card.delete.connect(self._delete_block)
            self.blocks_lay.addWidget(card)
            self.cards[b.id] = card

    def _on_view_selected(self, bid: str) -> None:
        for k, c in self.cards.items():
            c.set_selected(k == bid)
        if bid in self.cards:
            QTimer.singleShot(0, lambda: self.side.ensureWidgetVisible(self.cards[bid], 0, 40))

    def _set_view_mode(self, mode: str) -> None:
        self.view_mode = mode
        self._show_rendered()

    # ---------------------------------------------------------------- правки
    def _changed(self, redraw: bool) -> None:
        self.save_timer.start(800)
        if redraw:
            self.render_timer.start(250)

    def _on_tilt(self, b: Block, deg: float) -> None:
        """Наклон поменяли вручную. Если оригинал и правда так наклонён — запомнить и его наклон."""
        b.angle = deg
        if self._original is not None:
            found = angle.adopt(self._original, b.box, b.lines, b.kind, b.bg, deg, b.src_angle)
            if found is not None:
                b.src_angle, b.line_h = found
        if b.id in self.view.items_:
            self.view.items_[b.id].update()
        self._changed(True)

    def _add_block(self, box: Box) -> None:
        page = self._page()
        if page is None or self._original is None:
            return
        uniform, color = _ring_stats(self._original, box, pad=6)
        kind = ("bubble" if sum(color) / 3 >= 170 else "box") if uniform >= 0.8 else "free"
        b = Block.new(box, lines=[box], kind=kind, bg=color, manual=True)
        # распознать текст в рамке, если получится
        try:
            arr = self._original[int(box.y):int(box.y2), int(box.x):int(box.x2)]
            lines = self.pipeline.recognize(Image.fromarray(arr), self.cfg["source_lang"], "fast", False)[0]
            if lines:
                from ..bubbles import _join_lines
                joiner = languages.src(self.cfg["source_lang"])["joiner"]
                b.source = _join_lines(lines, joiner)
                for ln in lines:
                    ln.box = Box(ln.box.x + box.x, ln.box.y + box.y, ln.box.w, ln.box.h)
                b.lines = [ln.box for ln in lines]
                b.lang = languages.detect(b.source, self.cfg["source_lang"])
                engine.find_angle(self._original, b, lines, joiner, self.cfg["source_lang"])
        except Exception:  # noqa: BLE001
            pass
        page.blocks.append(b)
        if page.status == "new":
            page.status = "done"
        if b.source:
            err = engine.translate(self.pipeline, [b])
            if err:
                self._status(err)
        self._fill_cards()
        self.view.select(b.id)
        self._changed(True)

    def _delete_block(self, bid: str) -> None:
        page = self._page()
        if page is None:
            return
        page.blocks = [b for b in page.blocks if b.id != bid]
        self._fill_cards()
        self._changed(True)

    def _retranslate_block(self, bid: str) -> None:
        page = self._page()
        if page is None:
            return
        b = next((x for x in page.blocks if x.id == bid), None)
        if b is None or not b.source:
            return
        self._status("Перевожу блок…")
        QApplication.processEvents()
        err = engine.translate(self.pipeline, [b], force=True)
        self._status(err or "Блок переведён заново.")
        if bid in self.cards:
            self.cards[bid].set_translation(b.translation)
        self._changed(True)

    # ---------------------------------------------------------------- отрисовка
    def _render_now(self) -> None:
        page = self._page()
        if page is None or self.index in self._busy_pages:
            self._show_rendered()
            return
        if self._render_running:
            # не копим отрисовки: пока рисуется одна, все новые правки дождутся её и попадут в следующую
            self._render_again = True
            return
        self._render_again = False
        self._render_running = True
        self._render_token += 1
        key = engine.clean_key(page.blocks)
        cc = self._clean_cache
        clean = cc[2] if cc is not None and cc[0] == self.index and cc[1] == key else None
        job = _RenderJob(self.project, self.index, self._render_token, self._original, clean)
        job.clean_key = key
        job.signals.rendered.connect(self._on_rendered)
        job.signals.failed.connect(self._on_render_failed)
        job.setAutoDelete(False)
        self._jobs.add(job)
        self.pool.start(job)

    def _render_finished(self) -> None:
        self._render_running = False
        if self._render_again:
            self._render_now()

    def _on_render_failed(self, tb: str) -> None:
        self.last_error = tb
        self._status("Не удалось нарисовать страницу: " + tb.strip().splitlines()[-1])
        self._render_finished()

    def _on_rendered(self, index: int, token: int, payload) -> None:
        # старые задачи отпускаем позже, не во время их же сигнала
        QTimer.singleShot(0, lambda: self._drop_jobs(token))
        r, images = payload
        job = next((j for j in self._jobs if isinstance(j, _RenderJob) and j.token == token), None)
        if job is not None:
            self._clean_cache = (index, job.clean_key, r.clean)
        if index != self.index or token != self._render_token:
            self._render_finished()
            return
        self._rendered = r
        self._images.update(images)
        over = {bid for bid, lay in r.layouts.items() if lay.overflow}
        self.view.set_overflow(over)
        for bid, card in self.cards.items():
            card.set_overflow(bid in over)
        self._show_rendered()
        self._render_finished()

    def _drop_jobs(self, token: int) -> None:
        self._jobs = {j for j in self._jobs if not (isinstance(j, _RenderJob) and j.token <= token)}

    def _show_rendered(self) -> None:
        if self._original is None:
            return
        mode = self.view_mode if self._rendered is not None else "original"
        img = self._images.get(mode) or self._images.get("original")
        if img is not None:
            self.view.set_image(img)

    # ---------------------------------------------------------------- обработка
    def run_chapter(self) -> None:
        if self.project is None:
            self.open_folder()
            return
        todo = [i for i, p in enumerate(self.project.pages) if p.status != "done"]
        retranslate = False
        if not todo:
            ans = QMessageBox.question(self, "Студия", "Все страницы уже переведены. Перевести главу заново?\n"
                                       "Ручные правки текста перевода будут заменены.")
            if ans != QMessageBox.StandardButton.Yes:
                return
            todo = list(range(len(self.project.pages)))
            retranslate = True
        self._start(todo, retranslate)

    def run_page(self) -> None:
        page = self._page()
        if page is None:
            return
        if page.status != "new" and page.blocks:
            ans = QMessageBox.question(self, "Студия", "Найти текст на странице заново?\n"
                                       "Блоки и правки этой страницы будут заменены.")
            if ans != QMessageBox.StandardButton.Yes:
                return
        page.status = "new"
        page.blocks = []
        self._start([self.index], False)

    def _start(self, indices: list[int], retranslate: bool) -> None:
        if self.job is not None or self.project is None:
            return
        self.pipeline.reload()
        self._busy_pages = set(indices)
        for i in indices:
            self._update_thumb(i)
        job = _ChapterJob(self.pipeline, self.project, indices, retranslate)
        job.signals.progress.connect(self._on_progress)
        job.signals.page_done.connect(self._on_page_done)
        job.signals.finished.connect(self._on_finished)
        job.setAutoDelete(False)
        self.job = job
        self._set_running(True)
        if self.index in self._busy_pages:
            self._fill_cards()
        self.pool.start(job)

    def stop(self) -> None:
        if isinstance(self.job, _ChapterJob):
            self.job.control.cancel()
            self.pause_btn.setEnabled(False)
            self.stop_btn.setEnabled(False)
            self._status("Останавливаю… Текущий шаг распознавания закончится через пару секунд.")

    def toggle_pause(self) -> None:
        job = self.job
        if not isinstance(job, _ChapterJob) or job.cancelled:
            return
        if job.control.paused:
            job.control.resume()
            self._show_pause(False)
            self._status(self._progress_text or "Продолжаю перевод…")
        else:
            job.control.pause()
            self._show_pause(True)
            self._status("Пауза. Перевод стоит и не нагружает компьютер — нажмите «Продолжить», когда будет удобно.")

    def _show_pause(self, paused: bool) -> None:
        from ..ui.widgets import set_pill
        if paused:
            set_pill(self.pause_btn, "Продолжить", "tonal", "play")
        else:
            set_pill(self.pause_btn, "Пауза", "outline", "pause")

    def _set_running(self, on: bool) -> None:
        self.progress.setVisible(on)
        self.stop_btn.setVisible(on)
        self.stop_btn.setEnabled(True)
        self.pause_btn.setVisible(on)
        self.pause_btn.setEnabled(True)
        self._show_pause(False)
        self.run_btn.setEnabled(not on)
        self.page_btn.setEnabled(not on)
        self.export_btn.setEnabled(not on)
        if on:
            self.open_out.setVisible(False)

    def _on_progress(self, done: int, total: int, current: str) -> None:
        self.progress.setMaximum(max(1, total))
        self.progress.setValue(done)
        if current:
            self._progress_text = f"Страница {done + 1} из {total}: {current}"
            job = self.job
            if isinstance(job, _ChapterJob) and (job.control.paused or job.cancelled):
                return      # не затирать «Пауза» / «Останавливаю»
            self._status(self._progress_text)

    def _on_page_done(self, i: int) -> None:
        self._busy_pages.discard(i)
        self._update_thumb(i)
        if i == self.index:
            self._fill_cards()
            self._render_now()

    def _on_finished(self, msg: str) -> None:
        self.job = None
        for i in list(self._busy_pages):
            self._busy_pages.discard(i)
            self._update_thumb(i)
        self._set_running(False)
        self._status(msg)
        self._fill_cards()
        self._render_now()

    # ---------------------------------------------------------------- выгрузка
    def export(self) -> None:
        if self.project is None or self.job is not None:
            return
        if not any(p.status == "done" for p in self.project.pages):
            QMessageBox.information(self, "Студия", "Сначала переведите главу или хотя бы одну страницу.")
            return
        self._save()
        job = _ExportJob(self.project, self.cfg.get("studio_load", sysload.DEFAULT_LOAD))
        job.signals.progress.connect(self._on_progress)
        job.signals.finished.connect(self._on_exported)
        job.setAutoDelete(False)
        self.job = job
        self._set_running(True)
        self.stop_btn.setVisible(False)
        self.pause_btn.setVisible(False)
        self.pool.start(job)

    def _on_exported(self, msg: str) -> None:
        self.job = None
        self._set_running(False)
        self._status(f"{msg} Папка: {self.project.out_dir}")
        self.open_out.setVisible(True)

    def _open_out_dir(self) -> None:
        if self.project is not None:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.project.out_dir)))

    # ---------------------------------------------------------------- оформление
    def _fill_fonts(self, current: str = "") -> None:
        self.font_select.blockSignals(True)
        self.font_select.clear()
        self.font_select.addItem(f"По умолчанию ({fonts.name_of(fonts.default_font())})", "")
        for name, path in fonts.list_fonts():
            self.font_select.addItem(name, path)
        self.font_select.setCurrentIndex(max(0, self.font_select.findData(current)))
        self.font_select.blockSignals(False)

    def _add_font(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(self, "Файлы шрифтов", "", "Шрифты (*.ttf *.otf *.ttc)")
        if not paths:
            return
        fonts.FONT_DIR.mkdir(parents=True, exist_ok=True)
        last = ""
        for p in paths:
            target = fonts.FONT_DIR / Path(p).name
            try:
                shutil.copy2(p, target)
                last = str(target)
            except OSError as e:
                self._status(f"Не удалось скопировать шрифт: {e}")
        fonts.refresh()
        self._fill_fonts(last)
        if last:
            self._on_font()

    def _apply_style_to_controls(self) -> None:
        st = self.project.style
        self._fill_fonts(st.font)
        for w, v in ((self.min_size, st.min_size), (self.max_size, st.max_size), (self.spacing, st.line_spacing)):
            w.value = v
            w._render()
        self.caps.switch.setChecked(st.caps)
        self.bold.switch.setChecked(st.bold)
        self.tilt.switch.setChecked(st.tilt)
        self.fmt.setCurrentIndex(max(0, self.fmt.findData(st.fmt)))

    def _on_font(self, _i: int = 0) -> None:
        if self.project is None:
            return
        self.project.style.font = self.font_select.currentData() or ""
        self._changed(True)

    def _on_style(self) -> None:
        if self.project is None:
            return
        st = self.project.style
        st.min_size = int(self.min_size.value)
        st.max_size = max(st.min_size, int(self.max_size.value))
        st.line_spacing = float(self.spacing.value)
        st.caps = self.caps.switch.isChecked()
        st.bold = self.bold.switch.isChecked()
        st.tilt = self.tilt.switch.isChecked()
        st.fmt = self.fmt.currentData() or "png"
        self._changed(True)

    def _set_load(self, level: str) -> None:
        # переводчик и распознавание не трогаем: идущий перевод сам подхватит новую нагрузку
        self.cfg["studio_load"] = level
        self.cfg.save()

    def _set_cfg(self, key: str, value) -> None:
        self.cfg[key] = value
        self.cfg.save()
        self.pipeline.reload()
        self.settings_changed.emit()

    # ---------------------------------------------------------------- прочее
    def _save(self) -> None:
        if self.project is not None:
            try:
                self.project.save()
            except OSError as e:
                self._status(f"Не удалось сохранить проект: {e}")

    def _status(self, text: str) -> None:
        self.status.setText(text)

    def _shutdown(self) -> None:
        if isinstance(self.job, _ChapterJob):
            # и с паузы тоже: работа проснётся и сразу завершится
            self.job.control.cancel()
        if self._thumb_job is not None:
            self._thumb_job.stopped = True

    def closeEvent(self, e):
        self._shutdown()
        self._save()
        super().closeEvent(e)
