"""Элементы интерфейса в стиле Android-версии: карточки, кнопки, плитки, списки, переключатели."""

from __future__ import annotations

from PySide6.QtCore import (Property, QEasingCurve, QEvent, QPoint, QPropertyAnimation, QRect, QRectF, QSize, Qt,
                            QTimer, QVariantAnimation, Signal)
from PySide6.QtGui import QColor, QLinearGradient, QPainter, QPainterPath, QPixmap
from PySide6.QtWidgets import (QAbstractButton, QBoxLayout, QButtonGroup, QFrame, QHBoxLayout, QLabel, QLayout,
                               QLineEdit, QPushButton, QScrollArea, QSizePolicy, QToolButton, QVBoxLayout, QWidget)

from . import theme


class Clickable(QFrame):
    """Рамка, которая ведёт себя как кнопка, но правильно считает высоту содержимого."""

    clicked = Signal()

    def __init__(self, name: str):
        super().__init__()
        self.setObjectName(name)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def mousePressEvent(self, e):
        # нажатие нужно «забрать», иначе отпускание кнопки мыши сюда не придёт
        if e.button() == Qt.MouseButton.LeftButton:
            e.accept()
        else:
            super().mousePressEvent(e)

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton and self.rect().contains(e.position().toPoint()):
            self.clicked.emit()


def relayout(w: QWidget) -> None:
    """Пересчитать высоту всех родителей — после того как что-то раскрылось или свернулось."""
    p = w
    while p is not None:
        lay = p.layout()
        if lay is not None:
            lay.invalidate()
            lay.activate()
        if isinstance(p, Clip):
            p.sync()
        p.updateGeometry()
        if isinstance(p, ScrollContent):
            p.schedule()
        p = p.parentWidget()


MAX_H = 16777215


def outer_scroll(w: QWidget) -> QScrollArea | None:
    """Ближайшее окно прокрутки снаружи (не считая собственного списка виджета)."""
    p = w.parentWidget()
    while p is not None:
        if isinstance(p, QScrollArea) and p.objectName() != "dropListArea":
            return p
        p = p.parentWidget()
    return None


def scroll_into_view(w: QWidget, margin: int = 16) -> None:
    """Плавно прокрутить окно так, чтобы виджет был виден целиком (или хотя бы его верх)."""
    sa = outer_scroll(w)
    if sa is None or sa.widget() is None:
        return
    bar = sa.verticalScrollBar()
    top = w.mapTo(sa.widget(), QPoint(0, 0)).y() - margin
    bottom = top + w.height() + 2 * margin
    view = sa.viewport().height()
    cur = bar.value()
    if top < cur:
        target = top
    elif bottom > cur + view:
        target = min(top, bottom - view)
    else:
        return
    target = max(bar.minimum(), min(bar.maximum(), target))
    anim = QPropertyAnimation(bar, b"value", bar)
    anim.setDuration(260)
    anim.setEasingCurve(QEasingCurve.Type.OutCubic)
    anim.setStartValue(cur)
    anim.setEndValue(target)
    anim.start(QPropertyAnimation.DeletionPolicy.DeleteWhenStopped)


class HeightAnimator:
    """Плавно раскрывает и сворачивает виджет по высоте; соседи сдвигаются вместе с ним."""

    def __init__(self, target: QWidget, owner: QWidget, duration: int = 240):
        self.w = target
        self.owner = owner
        self.anim = QVariantAnimation(owner)
        self.anim.setDuration(duration)
        self.anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.anim.valueChanged.connect(self._step)
        self.anim.finished.connect(self._done)
        self._opening = False
        self._keep_fixed = False
        self.on_opened = None

    def _step(self, v) -> None:
        self.w.setFixedHeight(int(v))
        relayout(self.w)

    def open(self, height: int, keep_fixed: bool = False) -> None:
        """height — до какой высоты раскрыть; keep_fixed — оставить её постоянной (для списка)."""
        self.anim.stop()
        start = self.w.height() if self.w.isVisible() else 0
        self._opening = True
        self._keep_fixed = keep_fixed
        self.w.setFixedHeight(start)
        self.w.setVisible(True)
        self.anim.setStartValue(start)
        self.anim.setEndValue(max(1, height))
        self.anim.start()

    def close(self) -> None:
        self.anim.stop()
        if not self.w.isVisible():
            return
        self._opening = False
        self.anim.setStartValue(self.w.height())
        self.anim.setEndValue(0)
        self.anim.start()

    def _done(self) -> None:
        if self._opening:
            if not self._keep_fixed:
                self.w.setMinimumHeight(0)
                self.w.setMaximumHeight(MAX_H)
            relayout(self.w)
            if self.on_opened is not None:
                self.on_opened()
            scroll_into_view(self.owner)
        else:
            self.w.setVisible(False)
            self.w.setMinimumHeight(0)
            self.w.setMaximumHeight(MAX_H)
            relayout(self.w)


def natural_height(w: QWidget, width: int) -> int:
    lay = w.layout()
    if lay is None:
        return w.sizeHint().height()
    lay.activate()
    if lay.hasHeightForWidth():
        h = lay.totalHeightForWidth(width)
        if h > 0:
            return h
    return lay.totalSizeHint().height()


class Clip(QWidget):
    """Шторка: содержимое всегда своей полной высоты, а видна только верхняя часть.

    Во время анимации карточка «приоткрывается», а не сплющивает содержимое.
    """

    def __init__(self, inner: QWidget):
        super().__init__()
        self.inner = inner
        inner.setParent(self)
        sp = QSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)
        sp.setHeightForWidth(True)
        self.setSizePolicy(sp)

    def hasHeightForWidth(self) -> bool:  # noqa: N802
        return True

    def heightForWidth(self, w: int) -> int:  # noqa: N802
        return natural_height(self.inner, w)

    def sizeHint(self):  # noqa: N802
        return QSize(self.inner.sizeHint().width(), self.heightForWidth(max(self.width(), 200)))

    def minimumSizeHint(self):  # noqa: N802
        return QSize(self.inner.minimumSizeHint().width(), self.heightForWidth(max(self.width(), 200)))

    def sync(self) -> None:
        self.inner.setGeometry(0, 0, self.width(), self.heightForWidth(self.width()))

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self.sync()

    def event(self, e):
        if e.type() == QEvent.Type.LayoutRequest:
            self.updateGeometry()
            self.sync()
        return super().event(e)


class ScrollContent(QWidget):
    """Содержимое окна прокрутки. Всегда получает полную высоту для своей ширины.

    Сам QScrollArea иногда отдаёт содержимому только минимальную высоту, и тогда при
    раскрытии карточек тексты наезжают друг на друга. Здесь высота пересчитывается
    после каждого изменения раскладки.
    """

    def __init__(self):
        super().__init__()
        self._pending = False

    def refit(self) -> None:
        self._pending = False
        lay = self.layout()
        if lay is None:
            return
        lay.activate()
        h = lay.totalHeightForWidth(self.width()) if lay.hasHeightForWidth() else -1
        h = max(h, lay.totalSizeHint().height(), lay.totalMinimumSize().height())
        if h != self.minimumHeight():
            self.setMinimumHeight(h)
        # заново разложить всё содержимое под текущий размер окна
        lay.invalidate()
        lay.activate()

    def schedule(self) -> None:
        if not self._pending:
            self._pending = True
            QTimer.singleShot(0, self.refit)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        if e.size().width() != e.oldSize().width():
            self.schedule()

    def event(self, e):
        if e.type() == QEvent.Type.LayoutRequest:
            self.schedule()
        return super().event(e)


def _repolish(w: QWidget) -> None:
    w.style().unpolish(w)
    w.style().polish(w)


def label(text: str, name: str = "", wrap: bool = True) -> QLabel:
    lb = QLabel(text)
    if name:
        lb.setObjectName(name)
    lb.setWordWrap(wrap)
    return lb


class ElideLabel(QLabel):
    """Метка в одну строку: если не помещается, обрезается многоточием (полный текст — в подсказке)."""

    def __init__(self, text: str = "", name: str = ""):
        super().__init__()
        if name:
            self.setObjectName(name)
        self._full = ""
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.setText(text)

    def setText(self, text: str) -> None:  # noqa: N802
        self._full = text
        self._elide()

    def text(self) -> str:
        return self._full

    def _elide(self) -> None:
        shown = self.fontMetrics().elidedText(self._full, Qt.TextElideMode.ElideRight, max(self.width(), 40))
        super().setText(shown)
        self.setToolTip(self._full if shown != self._full else "")

    def sizeHint(self):
        return QSize(self.fontMetrics().horizontalAdvance(self._full) + 4, super().sizeHint().height())

    def minimumSizeHint(self):
        return QSize(40, super().minimumSizeHint().height())

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._elide()

    def changeEvent(self, e):
        super().changeEvent(e)
        if e.type() in (QEvent.Type.FontChange, QEvent.Type.StyleChange):
            self._elide()
            self.updateGeometry()


def caption(text: str) -> QLabel:
    return label(text, "caption")


def pill(text: str, kind: str = "primary", icon_name: str | None = None, tooltip: str = "") -> QPushButton:
    """Кнопка-таблетка. kind: primary | hero | heroOutline | outline | tonal | link."""
    b = QPushButton(text)
    b.setProperty("kind", kind)
    b.setCursor(Qt.CursorShape.PointingHandCursor)
    if icon_name:
        c = theme.palette()
        color = {"primary": c.on_primary, "hero": theme.INK, "heroOutline": theme.CREAM}.get(kind, c.primary)
        b.setIcon(theme.icon(icon_name, color, 18))
        b.setIconSize(QSize(18, 18))
    if tooltip:
        b.setToolTip(tooltip)
    return b


def set_pill(b: QPushButton, text: str, kind: str, icon_name: str | None = None) -> None:
    b.setText(text)
    b.setProperty("kind", kind)
    if icon_name:
        c = theme.palette()
        color = {"primary": c.on_primary, "hero": theme.INK, "heroOutline": theme.CREAM}.get(kind, c.primary)
        b.setIcon(theme.icon(icon_name, color, 18))
    _repolish(b)


# ---------------------------------------------------------------- карточка

class Card(QFrame):
    """Карточка со скруглёнными углами: заголовок с иконкой, подпись и содержимое."""

    def __init__(self, title: str, subtitle: str = "", icon_name: str = "", collapsible: bool = False,
                 collapsed: bool = False):
        super().__init__()
        self.setObjectName("card")
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Minimum)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 14, 16, 16)
        outer.setSpacing(10)

        head = QWidget()
        hl = QHBoxLayout(head)
        hl.setContentsMargins(0, 0, 0, 0)
        hl.setSpacing(12)
        if icon_name:
            badge = QLabel()
            badge.setObjectName("badge")
            badge.setFixedSize(36, 36)
            badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
            badge.setPixmap(theme.pixmap(icon_name, theme.palette().primary, 20))
            hl.addWidget(badge, 0, Qt.AlignmentFlag.AlignTop)
        col = QVBoxLayout()
        col.setSpacing(0)
        col.addWidget(ElideLabel(title, "cardTitle"))
        if subtitle:
            col.addWidget(ElideLabel(subtitle, "cardSubtitle"))
        hl.addLayout(col, 1)

        self.body = QWidget()
        self.lay = QVBoxLayout(self.body)
        self.lay.setContentsMargins(0, 4, 0, 0)
        self.lay.setSpacing(10)

        if collapsible:
            self.chevron = QLabel()
            self.chevron.setFixedSize(22, 22)
            hl.addWidget(self.chevron, 0, Qt.AlignmentFlag.AlignVCenter)
            btn = Clickable("collapseHeader")
            bl = QVBoxLayout(btn)
            bl.setContentsMargins(0, 0, 0, 0)
            bl.addWidget(head)
            btn.clicked.connect(self.toggle)
            outer.addWidget(btn)
            self._open = not collapsed
            self.clip = Clip(self.body)
            self.clip.setVisible(self._open)
            outer.addWidget(self.clip)
            self._anim = HeightAnimator(self.clip, self)
            self._render_chev()
        else:
            outer.addWidget(head)
            outer.addWidget(self.body)

    def add(self, w: QWidget | QLayout, stretch: int = 0) -> None:
        if isinstance(w, QLayout):
            self.lay.addLayout(w, stretch)
        else:
            self.lay.addWidget(w, stretch)

    def toggle(self) -> None:
        self._open = not self._open
        if self._open:
            m = self.layout().contentsMargins()
            self._anim.open(self.clip.heightForWidth(self.width() - m.left() - m.right()))
        else:
            self._anim.close()
        self._render_chev()

    def _render_chev(self) -> None:
        pm = theme.pixmap("chevron", theme.palette().primary, 22)
        if self._open:
            pm = pm.transformed(__import__("PySide6.QtGui", fromlist=["QTransform"]).QTransform().rotate(180))
        self.chevron.setPixmap(pm)


# ---------------------------------------------------------------- плитка

class Tile(QToolButton):
    """Плитка быстрого действия: иконка в кружке и подпись."""

    def __init__(self, text: str, icon_name: str, tooltip: str = ""):
        super().__init__()
        self.setObjectName("tile")
        self.setText(text)
        c = theme.palette()
        pm = QPixmap(44, 44)
        pm.fill(Qt.GlobalColor.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setBrush(QColor(c.selected))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawEllipse(0, 0, 44, 44)
        p.drawPixmap(11, 11, theme.pixmap(icon_name, c.primary, 22))
        p.end()
        self.setIcon(pm)
        self.setIconSize(QSize(44, 44))
        self.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextUnderIcon)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setMinimumHeight(96)
        if tooltip:
            self.setToolTip(tooltip)


# ---------------------------------------------------------------- раскрывающийся список

class _Choice(QFrame):
    clicked = Signal()

    def __init__(self, title: str, subtitle: str):
        super().__init__()
        self.setObjectName("choice")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(12, 8, 10, 8)
        col = QVBoxLayout()
        col.setSpacing(1)
        t = label(title)
        t.setStyleSheet("font-weight: 600;")
        col.addWidget(t)
        if subtitle:
            col.addWidget(caption(subtitle))
        lay.addLayout(col, 1)
        self.check = QLabel()
        self.check.setFixedSize(22, 22)
        lay.addWidget(self.check)

    def set_checked(self, on: bool) -> None:
        self.setProperty("checked", "true" if on else "false")
        c = theme.palette()
        self.check.setPixmap(theme.pixmap("check", c.primary, 20) if on else QPixmap())
        _repolish(self)

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            e.accept()

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton and self.rect().contains(e.position().toPoint()):
            self.clicked.emit()


class Dropdown(QFrame):
    """Поле выбора. По нажатию список плавно раскрывается прямо в карточке, соседние карточки
    сдвигаются, а если вариантов много — список прокручивается внутри. Сворачивается так же плавно."""

    changed = Signal(str)

    def __init__(self, caption_text: str, items: list[tuple[str, str, str, str]], current: str):
        """items: (ключ, название, описание, группа)."""
        super().__init__()
        self.setObjectName("dropdown")
        self.items = items
        self.current = current
        self._open = False
        lay = QVBoxLayout(self)
        lay.setContentsMargins(2, 2, 2, 2)
        lay.setSpacing(0)

        self.header = Clickable("dropHeader")
        hl = QHBoxLayout(self.header)
        hl.setContentsMargins(12, 8, 10, 8)
        col = QVBoxLayout()
        col.setSpacing(1)
        col.addWidget(ElideLabel(caption_text.upper(), "fieldLabel"))
        self.title = ElideLabel("")
        self.title.setStyleSheet("font-size: 11pt; font-weight: 600;")
        self.subtitle = ElideLabel("", "caption")
        col.addWidget(self.title)
        col.addWidget(self.subtitle)
        hl.addLayout(col, 1)
        self.chev = QLabel()
        self.chev.setFixedSize(22, 22)
        hl.addWidget(self.chev)
        self.header.clicked.connect(self.toggle)
        lay.addWidget(self.header)

        self.list = QWidget()
        self.list.setObjectName("dropListContent")
        ll = QVBoxLayout(self.list)
        ll.setContentsMargins(8, 2, 8, 10)
        ll.setSpacing(6)
        self.choices: dict[str, _Choice] = {}
        group = None
        for key, title, sub, grp in items:
            if grp and grp != group:
                group = grp
                ll.addWidget(label(grp, "sectionLabel"))
            ch = _Choice(title, sub)
            ch.clicked.connect(lambda k=key: self._choose(k))
            self.choices[key] = ch
            ll.addWidget(ch)
        ll.addStretch(1)
        self.list.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Minimum)

        self.area = QScrollArea()
        self.area.setObjectName("dropListArea")
        self.area.setWidgetResizable(True)
        self.area.setFrameShape(QFrame.Shape.NoFrame)
        self.area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.area.setWidget(self.list)
        self.area.setVisible(False)
        lay.addWidget(self.area)
        self._anim = HeightAnimator(self.area, self)
        self._anim.on_opened = self._show_current
        self._render()

    def is_open(self) -> bool:
        return self._open

    def toggle(self) -> None:
        if self._open:
            self.close_list()
        else:
            self.open_list()

    def open_list(self) -> None:
        self._open = True
        need = natural_height(self.list, max(self.width() - 16, 200))
        # список вытягивает свою карточку примерно вдвое; что не поместилось — прокручивается
        card = self.parentWidget()
        while card is not None and card.objectName() != "card":
            card = card.parentWidget()
        base = card.height() if card is not None else self.height() * 3
        limit = max(300, min(base, 520))
        self.area.verticalScrollBar().setValue(0)
        self._anim.open(min(need, limit), keep_fixed=True)
        self._render_state()

    def _show_current(self) -> None:
        ch = self.choices.get(self.current)
        if ch is not None and self.area.verticalScrollBar().value() == 0:
            self.area.ensureWidgetVisible(ch, 0, 12)

    def close_list(self) -> None:
        self._open = False
        self._anim.close()
        self._render_state()

    def select(self, key: str) -> None:
        self.current = key
        self._render()

    def _choose(self, key: str) -> None:
        self.current = key
        self.close_list()
        self._render()
        self.changed.emit(key)

    def _render_state(self) -> None:
        self.setProperty("open", "true" if self._open else "false")
        _repolish(self)
        self._render_chevron()

    def _render(self) -> None:
        item = next((i for i in self.items if i[0] == self.current), self.items[0])
        self.title.setText(item[1])
        self.subtitle.setText(item[2])
        self.subtitle.setVisible(bool(item[2]))
        for k, ch in self.choices.items():
            ch.set_checked(k == item[0])
        self._render_chevron()

    def _render_chevron(self) -> None:
        from PySide6.QtGui import QTransform
        pm = theme.pixmap("chevron", theme.palette().primary, 22)
        if self._open:
            pm = pm.transformed(QTransform().rotate(180))
        self.chev.setPixmap(pm)


class _SelectItem(QFrame):
    clicked = Signal()

    def __init__(self, text: str):
        super().__init__()
        self.setObjectName("selectItem")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(10, 7, 8, 7)
        self.label = ElideLabel(text)
        lay.addWidget(self.label, 1)
        self.check = QLabel()
        self.check.setFixedSize(18, 18)
        lay.addWidget(self.check)

    def set_checked(self, on: bool) -> None:
        self.setProperty("checked", "true" if on else "false")
        self.check.setPixmap(theme.pixmap("check", theme.palette().primary, 16) if on else QPixmap())
        _repolish(self)

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            e.accept()

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton and self.rect().contains(e.position().toPoint()):
            self.clicked.emit()


class Select(QFrame):
    """Компактный выпадающий список (замена QComboBox).

    По нажатию список плавно раскрывается вниз прямо в карточке — на несколько строк,
    дальше он прокручивается. Умеет то же, что нужно от QComboBox: addItem, clear,
    findData, currentData, setCurrentIndex, setItemText и сигнал currentIndexChanged.
    """

    currentIndexChanged = Signal(int)  # noqa: N815
    VISIBLE_ROWS = 6

    def __init__(self):
        super().__init__()
        self.setObjectName("select")
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        self._items: list[tuple[str, object]] = []
        self._rows: list[_SelectItem] = []
        self._index = -1
        self._open = False
        lay = QVBoxLayout(self)
        lay.setContentsMargins(1, 1, 1, 1)
        lay.setSpacing(0)

        self.header = Clickable("selectHeader")
        hl = QHBoxLayout(self.header)
        hl.setContentsMargins(12, 8, 8, 8)
        hl.setSpacing(6)
        self.text = ElideLabel("")
        hl.addWidget(self.text, 1)
        self.chev = QLabel()
        self.chev.setFixedSize(18, 18)
        hl.addWidget(self.chev)
        self.header.clicked.connect(self.toggle)
        lay.addWidget(self.header)

        self.list = QWidget()
        self.list.setObjectName("dropListContent")
        self.list_lay = QVBoxLayout(self.list)
        self.list_lay.setContentsMargins(4, 2, 4, 4)
        self.list_lay.setSpacing(2)
        self.list_lay.addStretch(1)
        self.list.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Minimum)
        self.area = QScrollArea()
        self.area.setObjectName("dropListArea")
        self.area.setWidgetResizable(True)
        self.area.setFrameShape(QFrame.Shape.NoFrame)
        self.area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.area.setWidget(self.list)
        self.area.setVisible(False)
        lay.addWidget(self.area)
        self._anim = HeightAnimator(self.area, self, 200)
        self._anim.on_opened = self._show_current
        self._render_chevron()

    # ------------------------------------------------------------ как у QComboBox
    def addItem(self, text: str, data=None) -> None:  # noqa: N802
        self._items.append((text, data))
        row = _SelectItem(text)
        i = len(self._rows)
        row.clicked.connect(lambda i=i: self._choose(i))
        self._rows.append(row)
        self.list_lay.insertWidget(self.list_lay.count() - 1, row)
        if self._index < 0:
            self._index = 0
            self._render()
        else:
            row.set_checked(False)

    def clear(self) -> None:
        for row in self._rows:
            self.list_lay.removeWidget(row)
            row.hide()
            row.setParent(None)
            row.deleteLater()
        self._rows.clear()
        self._items.clear()
        self._index = -1
        self._render()

    def count(self) -> int:
        return len(self._items)

    def itemText(self, i: int) -> str:  # noqa: N802
        return self._items[i][0] if 0 <= i < len(self._items) else ""

    def itemData(self, i: int):  # noqa: N802
        return self._items[i][1] if 0 <= i < len(self._items) else None

    def setItemText(self, i: int, text: str) -> None:  # noqa: N802
        if 0 <= i < len(self._items):
            self._items[i] = (text, self._items[i][1])
            self._rows[i].label.setText(text)
            if i == self._index:
                self.text.setText(text)

    def findData(self, data) -> int:  # noqa: N802
        for i, (_t, d) in enumerate(self._items):
            if d == data:
                return i
        return -1

    def currentIndex(self) -> int:  # noqa: N802
        return self._index

    def currentData(self):  # noqa: N802
        return self.itemData(self._index)

    def currentText(self) -> str:  # noqa: N802
        return self.itemText(self._index)

    def setCurrentIndex(self, i: int) -> None:  # noqa: N802
        if not 0 <= i < len(self._items) or i == self._index:
            return
        self._index = i
        self._render()
        self.currentIndexChanged.emit(i)

    # ------------------------------------------------------------ раскрытие
    def toggle(self) -> None:
        if self._open:
            self.close_list()
        elif self._items:
            self.open_list()

    def open_list(self) -> None:
        self._open = True
        rows = self._rows[: self.VISIBLE_ROWS]
        need = natural_height(self.list, max(self.width() - 10, 120))
        if len(self._rows) > self.VISIBLE_ROWS:
            # видно несколько строк, остальное прокручивается
            row_h = max((r.sizeHint().height() for r in rows), default=34)
            need = min(need, self.VISIBLE_ROWS * (row_h + 2) + 6 + row_h // 2)
        self.area.verticalScrollBar().setValue(0)
        self._anim.open(need, keep_fixed=True)
        self._render_state()

    def close_list(self) -> None:
        self._open = False
        self._anim.close()
        self._render_state()

    def _show_current(self) -> None:
        if 0 <= self._index < len(self._rows):
            self.area.ensureWidgetVisible(self._rows[self._index], 0, 8)

    def _choose(self, i: int) -> None:
        self.close_list()
        self.setCurrentIndex(i)

    def _render_state(self) -> None:
        self.setProperty("open", "true" if self._open else "false")
        _repolish(self)
        self._render_chevron()

    def _render(self) -> None:
        self.text.setText(self.itemText(self._index))
        for k, row in enumerate(self._rows):
            row.set_checked(k == self._index)

    def _render_chevron(self) -> None:
        from PySide6.QtGui import QTransform
        pm = theme.pixmap("chevron", theme.palette().primary, 18)
        if self._open:
            pm = pm.transformed(QTransform().rotate(180))
        self.chev.setPixmap(pm)


# ---------------------------------------------------------------- поле ввода с подписью

class Field(QWidget):
    edited = Signal(str)

    def __init__(self, caption_text: str, value: str = "", placeholder: str = "", secret: bool = False):
        super().__init__()
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(4)
        if caption_text:
            lay.addWidget(label(caption_text.upper(), "fieldLabel"))
        self.edit = QLineEdit(value)
        self.edit.setPlaceholderText(placeholder)
        if secret:
            self.edit.setEchoMode(QLineEdit.EchoMode.PasswordEchoOnEdit)
        self.edit.editingFinished.connect(lambda: self.edited.emit(self.edit.text().strip()))
        lay.addWidget(self.edit)


# ---------------------------------------------------------------- число с кнопками − и +

class Stepper(QWidget):
    changed = Signal(float)

    def __init__(self, title: str, subtitle: str, value: float, step: float, lo: float, hi: float, fmt):
        super().__init__()
        self.value, self.step, self.lo, self.hi, self.fmt = value, step, lo, hi, fmt
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)
        col = QVBoxLayout()
        col.setSpacing(0)
        col.addWidget(label(title))
        if subtitle:
            col.addWidget(caption(subtitle))
        lay.addLayout(col, 1)
        minus = pill("−", "round")
        plus = pill("+", "round")
        self.out = QLabel()
        self.out.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.out.setMinimumWidth(56)
        self.out.setStyleSheet("font-weight: 600;")
        for b in (minus, plus):
            b.setAutoRepeat(True)       # можно держать кнопку нажатой
            b.setAutoRepeatDelay(350)
            b.setAutoRepeatInterval(60)
        minus.clicked.connect(lambda: self._change(-step))
        plus.clicked.connect(lambda: self._change(step))
        lay.addWidget(minus)
        lay.addWidget(self.out)
        lay.addWidget(plus)
        self._render()

    def _change(self, d: float) -> None:
        self.value = round(min(self.hi, max(self.lo, self.value + d)), 2)
        self._render()
        self.changed.emit(self.value)

    def _render(self) -> None:
        self.out.setText(self.fmt(self.value))


# ---------------------------------------------------------------- переключатель

class Toggle(QAbstractButton):
    """Переключатель в стиле Android с плавным движением."""

    def __init__(self, checked: bool = False):
        super().__init__()
        self.setCheckable(True)
        self.setChecked(checked)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedSize(46, 26)
        self._pos = 1.0 if checked else 0.0
        # свойство анимации — knob (положение ползунка); имя pos занято положением самого виджета
        self._anim = QPropertyAnimation(self, b"knob", self)
        self._anim.setDuration(140)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.toggled.connect(self._animate)

    def _animate(self, on: bool) -> None:
        self._anim.stop()
        self._anim.setStartValue(self._pos)
        self._anim.setEndValue(1.0 if on else 0.0)
        self._anim.start()

    def _get_pos(self) -> float:
        return self._pos

    def _set_pos(self, v: float) -> None:
        self._pos = v
        self.update()

    knob = Property(float, _get_pos, _set_pos)

    def paintEvent(self, _):
        c = theme.palette()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        track = QColor(c.primary) if self.isChecked() else QColor(c.border)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(track)
        p.drawRoundedRect(QRectF(0, 3, 46, 20), 10, 10)
        x = 3 + self._pos * 20
        p.setBrush(QColor(c.on_primary if self.isChecked() else c.surface))
        p.setPen(QColor(0, 0, 0, 30))
        p.drawEllipse(QRectF(x, 3, 20, 20))
        p.end()


class SwitchRow(QWidget):
    toggled = Signal(bool)

    def __init__(self, title: str, subtitle: str, checked: bool):
        super().__init__()
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 2, 0, 2)
        col = QVBoxLayout()
        col.setSpacing(0)
        col.addWidget(label(title))
        if subtitle:
            col.addWidget(caption(subtitle))
        lay.addLayout(col, 1)
        self.switch = Toggle(checked)
        self.switch.toggled.connect(self.toggled)
        lay.addWidget(self.switch)

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            e.accept()

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton and self.rect().contains(e.position().toPoint()):
            self.switch.toggle()


# ---------------------------------------------------------------- переключатель режимов в шапке

class Segmented(QFrame):
    changed = Signal(str)

    def __init__(self, options: list[tuple[str, str]], current: str):
        super().__init__()
        self.setObjectName("segmented")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(4, 4, 4, 4)
        lay.setSpacing(2)
        self.group = QButtonGroup(self)
        self.buttons: dict[str, QPushButton] = {}
        for key, text in options:
            b = QPushButton(text)
            b.setProperty("kind", "segment")
            b.setCheckable(True)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setChecked(key == current)
            b.clicked.connect(lambda _=False, k=key: self.changed.emit(k))
            self.group.addButton(b)
            self.buttons[key] = b
            lay.addWidget(b, 1)

    def select(self, key: str) -> None:
        if key in self.buttons:
            self.buttons[key].setChecked(True)


# ---------------------------------------------------------------- метка состояния

class StatusChip(QFrame):
    def __init__(self):
        super().__init__()
        self.setObjectName("chip")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(10, 4, 12, 4)
        lay.setSpacing(8)
        self.dot = QLabel()
        self.dot.setFixedSize(8, 8)
        self.text = QLabel()
        self.text.setStyleSheet(f"color: {theme.CREAM}; background: transparent; font-weight: 600; font-size: 9pt;")
        lay.addWidget(self.dot)
        lay.addWidget(self.text)
        self.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)

    def set(self, active: bool, text: str) -> None:
        color = "#9BE7C4" if active else "rgba(243,227,204,0.55)"
        self.dot.setStyleSheet(f"background: {color}; border-radius: 4px;")
        self.text.setText(text)


# ---------------------------------------------------------------- шапка с градиентом

class Hero(QWidget):
    """Тёмная шапка с градиентом, скруглённым низом и полупрозрачным облачком."""

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect())
        path = QPainterPath()
        rad = 26.0
        path.moveTo(r.left(), r.top())
        path.lineTo(r.right(), r.top())
        path.lineTo(r.right(), r.bottom() - rad)
        path.quadTo(r.right(), r.bottom(), r.right() - rad, r.bottom())
        path.lineTo(r.left() + rad, r.bottom())
        path.quadTo(r.left(), r.bottom(), r.left(), r.bottom() - rad)
        path.closeSubpath()
        g = QLinearGradient(r.topLeft(), r.bottomRight())
        g.setColorAt(0, QColor(theme.INK))
        g.setColorAt(1, QColor(theme.HERO_END))
        p.fillPath(path, g)
        p.setClipPath(path)
        w, h = r.width(), r.height()
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(255, 255, 255, 14))
        p.drawEllipse(QRectF(w * 0.80, -h * 0.25, w * 0.42, w * 0.42))
        p.setBrush(QColor(255, 255, 255, 9))
        p.drawEllipse(QRectF(w * 0.88, h * 0.55, w * 0.30, w * 0.30))
        # облачко с репликой
        bw = min(170.0, w * 0.22)
        bh = bw * 0.62
        left, top = w - bw - 30, 22.0
        pen_color = QColor(255, 255, 255, 46)
        from PySide6.QtGui import QPen
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(pen_color, 2))
        p.drawEllipse(QRectF(left, top, bw, bh))
        tail = QPainterPath()
        tail.moveTo(left + bw * 0.28, top + bh * 0.92)
        tail.lineTo(left + bw * 0.18, top + bh * 1.22)
        tail.lineTo(left + bw * 0.42, top + bh * 0.98)
        p.drawPath(tail)
        p.setPen(QPen(QColor(255, 255, 255, 40), 3, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        cx = left + bw / 2
        for dy, k in ((0.38, 0.24), (0.54, 0.30), (0.70, 0.16)):
            p.drawLine(QPoint(int(cx - bw * k), int(top + bh * dy)), QPoint(int(cx + bw * k), int(top + bh * dy)))
        p.end()


def round_pixmap(path: str, size: int) -> QPixmap:
    """Картинка в круге (логотип в шапке)."""
    src = QPixmap(path)
    out = QPixmap(size * 2, size * 2)
    out.fill(Qt.GlobalColor.transparent)
    if src.isNull():
        return out
    p = QPainter(out)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    clip = QPainterPath()
    clip.addEllipse(QRectF(0, 0, size * 2, size * 2))
    p.setClipPath(clip)
    p.drawPixmap(QRect(0, 0, size * 2, size * 2), src)
    p.setClipping(False)
    from PySide6.QtGui import QPen
    p.setPen(QPen(QColor(theme.CREAM), 4))
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawEllipse(QRectF(2, 2, size * 2 - 4, size * 2 - 4))
    p.end()
    out.setDevicePixelRatio(2.0)
    return out


# ---------------------------------------------------------------- раскладки

class FlowLayout(QLayout):
    """Элементы идут в ряд и переносятся на новую строку, если не помещаются (кнопки в шапке)."""

    def __init__(self, spacing: int = 8):
        super().__init__()
        self._items = []
        self._sp = spacing
        self.setContentsMargins(0, 0, 0, 0)

    def addItem(self, item):
        self._items.append(item)

    def count(self):
        return len(self._items)

    def itemAt(self, i):
        return self._items[i] if 0 <= i < len(self._items) else None

    def takeAt(self, i):
        return self._items.pop(i) if 0 <= i < len(self._items) else None

    def expandingDirections(self):
        return Qt.Orientation(0)

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, w):
        return self._do(QRect(0, 0, w, 0), True)

    def setGeometry(self, rect):
        super().setGeometry(rect)
        self._do(rect, False)

    def sizeHint(self):
        return self.minimumSize()

    def minimumSize(self):
        s = QSize()
        for it in self._items:
            s = s.expandedTo(it.minimumSize())
        return s

    def _do(self, rect: QRect, test: bool) -> int:
        x, y, line_h = rect.x(), rect.y(), 0
        visible = [it for it in self._items if not (it.widget() and it.widget().isHidden())]
        for it in visible:
            hint = it.sizeHint()
            nx = x + hint.width() + self._sp
            if nx - self._sp > rect.right() + 1 and line_h > 0:
                x = rect.x()
                y += line_h + self._sp
                nx = x + hint.width() + self._sp
                line_h = 0
            if not test:
                it.setGeometry(QRect(QPoint(x, y), hint))
            x = nx
            line_h = max(line_h, hint.height())
        return y + line_h - rect.y()


class Columns(QWidget):
    """Карточки в одну или две колонки — в зависимости от ширины окна.

    В две колонки карточки раскладываются так, чтобы колонки были примерно одной высоты.
    Каждая колонка — отдельный виджет: когда карточка раскрывается, колонка и окно сразу
    пересчитывают высоту.
    """

    def __init__(self, widgets: list[QWidget], breakpoint: int = 820):
        super().__init__()
        self.widgets = widgets
        self.breakpoint = breakpoint
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Minimum)
        self.lay = QHBoxLayout(self)
        self.lay.setContentsMargins(0, 0, 0, 0)
        self.lay.setSpacing(14)
        self.cols: list[QWidget] = []
        for _ in range(2):
            col = QWidget()
            col.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Minimum)
            cl = QVBoxLayout(col)
            cl.setContentsMargins(0, 0, 0, 0)
            cl.setSpacing(14)
            cl.setAlignment(Qt.AlignmentFlag.AlignTop)
            self.cols.append(col)
            self.lay.addWidget(col, 1)
        self._n = 0
        self._arrange(1)

    def _arrange(self, n: int) -> None:
        if n == self._n:
            return
        self._n = n
        # removeWidget, а не takeAt: старая запись раскладки должна удалиться сразу, иначе Qt
        # продолжает обновлять размеры через неё, и перенесённые карточки перестают расти
        for col in self.cols:
            lay = col.layout()
            for w in self.widgets:
                lay.removeWidget(w)
            while lay.count():  # остались только распорки
                lay.takeAt(0)
        heights = [0] * n
        for w in self.widgets:
            i = heights.index(min(heights))
            self.cols[i].layout().addWidget(w)
            heights[i] += w.sizeHint().height() + 14
        # распорка внизу: карточки не растягиваются, если соседняя колонка длиннее
        for col in self.cols:
            col.layout().addStretch(1)
        self.cols[1].setVisible(n > 1)
        relayout(self)

    def resizeEvent(self, e):
        n = 2 if e.size().width() >= self.breakpoint else 1
        super().resizeEvent(e)
        if n != self._n:
            QTimer.singleShot(0, lambda: self._arrange(n))


class ResponsiveRow(QWidget):
    """Два поля в ряд со стрелкой между ними, а в узком месте — друг под другом."""

    def __init__(self, left: QWidget, middle: QWidget, right: QWidget, breakpoint: int = 420,
                 anchor: QWidget | None = None):
        """anchor — к середине какого виджета выравнивать стрелку (например, к полю списка)."""
        super().__init__()
        self.middle = middle
        self.anchor = anchor
        self.breakpoint = breakpoint
        self.box = QBoxLayout(QBoxLayout.Direction.LeftToRight, self)
        self.box.setContentsMargins(0, 0, 0, 0)
        self.box.setSpacing(8)
        # всё прижато кверху: когда список под полем раскрывается, соседнее поле и стрелка не съезжают
        self.box.addWidget(left, 1, Qt.AlignmentFlag.AlignTop)
        self.box.addWidget(middle, 0, Qt.AlignmentFlag.AlignTop)
        self.box.addWidget(right, 1, Qt.AlignmentFlag.AlignTop)

    def _place_middle(self) -> None:
        if self.anchor is None or not self.anchor.isVisible():
            return
        y = self.anchor.mapTo(self, QPoint(0, 0)).y() + self.anchor.height() // 2 \
            - max(1, self.middle.sizeHint().height() - self.middle.contentsMargins().top()) // 2
        if y >= 0 and self.middle.contentsMargins().top() != y:
            self.middle.setContentsMargins(0, y, 0, 0)

    def showEvent(self, e):
        super().showEvent(e)
        QTimer.singleShot(0, self._place_middle)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        QTimer.singleShot(0, self._place_middle)
        narrow = e.size().width() < self.breakpoint
        direction = QBoxLayout.Direction.TopToBottom if narrow else QBoxLayout.Direction.LeftToRight
        if self.box.direction() != direction:
            # после смены ряд ↔ столбик высота меняется — сообщить карточке и окну
            QTimer.singleShot(0, lambda: self._apply(direction, narrow))

    def _apply(self, direction, narrow: bool) -> None:
        if self.box.direction() == direction:
            return
        self.box.setDirection(direction)
        self.middle.setVisible(not narrow)
        relayout(self)
