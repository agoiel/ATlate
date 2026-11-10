"""Компактный режим: маленькая панель поверх экрана вместо большого окна.

Свёрнутая панель — небольшой значок. При наведении она раскрывается в столбик круглых
кнопок, а наведение на кнопку выдвигает сбоку планку с её действиями:

    Перевод — перевести (или пауза Live), сменить режим, скрыть перевод
    Зона    — выделить новую, показать, скрыть
    Выход   — вернуться к обычному окну или закрыть программу

Панель остаётся раскрытой, пока на ней мышь, и ещё несколько секунд после (настраивается).

Про нагрузку: панель ничего не делает, пока её не трогают — нет постоянных таймеров,
анимации идут только во время перехода, значки рисуются один раз и запоминаются.
Окна панели не забирают фокус у читалки и по возможности скрыты от снимков экрана.
"""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import (QEasingCurve, QPoint, QPropertyAnimation, QRect, QRectF, Qt, QTimer,
                            QVariantAnimation, Signal)
from PySide6.QtGui import (QColor, QCursor, QGuiApplication, QLinearGradient, QPainter, QPainterPath, QPen,
                           QPixmap, QRegion)
from PySide6.QtWidgets import QHBoxLayout, QLabel, QWidget

from ..capture import exclude_from_capture
from . import theme
from .widgets import pill, round_pixmap

W = 56                 # ширина панели и размер свёрнутого значка
BTN = 40               # диаметр круглой кнопки
GAP = 8
LOGO = 40
GREEN = "#9BE7C4"      # «работает» — тот же цвет, что у точки статуса в главном окне

_FLAGS = (Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint
          | Qt.WindowType.WindowDoesNotAcceptFocus | Qt.WindowType.NoDropShadowWindowHint)


@dataclass
class Item:
    """Действие на планке."""

    key: str
    text: str
    icon: str = ""
    primary: bool = False
    enabled: bool = True


@dataclass
class Group:
    """Круглая кнопка панели: значок, действие по щелчку и действия на планке."""

    key: str
    icon: str
    click: str             # что делает щелчок по самой кнопке


GROUPS = [
    Group("translate", "play", "main"),
    Group("zone", "select", "zone_new"),
    Group("exit", "exit", "full"),
]


def _paint_body(p: QPainter, r: QRectF, radius: float) -> None:
    """Фон панели и планки — тот же градиент, что у шапки главного окна."""
    path = QPainterPath()
    path.addRoundedRect(r, radius, radius)
    g = QLinearGradient(r.topLeft(), r.bottomRight())
    g.setColorAt(0, QColor(theme.INK))
    g.setColorAt(1, QColor(theme.HERO_END))
    p.fillPath(path, g)
    edge = QColor(theme.CREAM)
    edge.setAlpha(90)
    p.setPen(QPen(edge, 1.2))
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawPath(path)


# ==================================================================== планка

class MiniBar(QWidget):
    """Планка сбоку от панели: кнопки действий или короткое сообщение."""

    triggered = Signal(str)
    hovered = Signal(bool)

    def __init__(self):
        super().__init__(None, _FLAGS)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.excluded = False
        self._lay = QHBoxLayout(self)
        self._lay.setContentsMargins(10, 8, 10, 8)
        self._lay.setSpacing(8)
        self._fade = QPropertyAnimation(self, b"windowOpacity", self)
        self._fade.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._fade.finished.connect(self._after_fade)
        self._slide = QPropertyAnimation(self, b"pos", self)
        self._slide.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._slide.setDuration(170)
        self._closing = False

    # ------------------------------------------------------------ содержимое
    def _clear(self) -> None:
        while self._lay.count():
            w = self._lay.takeAt(0).widget()
            if w is not None:
                w.hide()
                w.deleteLater()

    def set_items(self, items: list[Item]) -> None:
        self._clear()
        for it in items:
            b = pill(it.text, "hero" if it.primary else "heroOutline", it.icon or None)
            b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            if not it.enabled:
                # недоступное действие — приглушённое (например, «Показать», пока зона не выделена)
                b.setEnabled(False)
                b.setCursor(Qt.CursorShape.ArrowCursor)
                b.setStyleSheet("color: rgba(243,227,204,0.38); border-color: rgba(243,227,204,0.28);"
                                " background: transparent;")
                if it.icon:
                    b.setIcon(theme.icon(it.icon, "#8E7A69", 18))
            b.clicked.connect(lambda _c=False, k=it.key: self.triggered.emit(k))
            self._lay.addWidget(b)
            b.show()        # иначе в уже открытой планке кнопка появится позже, и размер посчитается без неё
        self._fit()

    def set_message(self, text: str, error: bool) -> None:
        self._clear()
        lab = QLabel(text)
        lab.setWordWrap(True)
        lab.setMaximumWidth(360)
        lab.setStyleSheet(f"color: {'#FFB4A8' if error else theme.CREAM}; background: transparent; font-weight: 600;")
        self._lay.addWidget(lab)
        lab.show()
        self._fit()

    def _fit(self) -> None:
        """Размер окна — точно по содержимому, сразу (планку тут же ставят рядом с кнопкой)."""
        self._lay.activate()
        self.resize(self._lay.sizeHint())

    # ------------------------------------------------------------ показ
    def open_at(self, anchor: QRect, screen: QRect, animate: bool = True) -> None:
        """Показать рядом с прямоугольником anchor (в координатах экрана): справа, а если не влезает — слева."""
        w, h = self.width(), self.height()
        right = anchor.right() + 1 + GAP
        left = anchor.left() - GAP - w
        # справа, если влезает; иначе с той стороны, где больше места
        on_right = right + w <= screen.right() or (left < screen.left()
                                                    and screen.right() - anchor.right() >= anchor.left() - screen.left())
        x = right if on_right else left
        x = min(max(screen.left() + 4, x), max(screen.left() + 4, screen.right() - w - 4))
        y = min(max(screen.top() + 4, anchor.center().y() - h // 2), screen.bottom() - h - 4)
        target = QPoint(x, y)
        self._closing = False
        self._fade.stop()
        self._slide.stop()
        if not animate or (self.isVisible() and self.windowOpacity() > 0.9):
            # планка уже открыта (перешли на соседнюю кнопку) — просто переставляем
            self.setWindowOpacity(1.0)
            self.move(target)
            self.show()
            return
        self.move(target + QPoint(-12 if on_right else 12, 0))
        self.setWindowOpacity(0.0)
        self.show()
        self._fade.setDuration(150)
        self._fade.setStartValue(0.0)
        self._fade.setEndValue(1.0)
        self._fade.start()
        self._slide.setStartValue(self.pos())
        self._slide.setEndValue(target)
        self._slide.start()

    def close_soft(self) -> None:
        if not self.isVisible() or self._closing:
            return
        self._closing = True
        self._slide.stop()
        self._fade.stop()
        self._fade.setDuration(110)
        self._fade.setStartValue(self.windowOpacity())
        self._fade.setEndValue(0.0)
        self._fade.start()

    def _after_fade(self) -> None:
        if self._closing:
            self._closing = False
            self.hide()

    # ------------------------------------------------------------ события
    def showEvent(self, e):
        super().showEvent(e)
        if not self.excluded:
            self.excluded = exclude_from_capture(self)

    def enterEvent(self, e):
        self.hovered.emit(True)
        super().enterEvent(e)

    def leaveEvent(self, e):
        self.hovered.emit(False)
        super().leaveEvent(e)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(0.6, 0.6, -0.6, -0.6)
        _paint_body(p, r, min(26.0, r.height() / 2))
        p.end()


# ==================================================================== панель

class MiniPanel(QWidget):
    """Плавающая панель компактного режима.

    state — функция без аргументов, возвращает текущее состояние программы словарём
    (live_mode, live_on, busy, has_region, overlay_visible, zone_visible).
    Сигнал triggered отдаёт ключ действия — выполняет его главное окно.
    """

    triggered = Signal(str)
    moved = Signal(int, int)

    def __init__(self, state, collapse_delay: float = 5.0):
        super().__init__(None, _FLAGS)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setMouseTracking(True)
        self._state = state
        self.collapse_delay = collapse_delay
        self.excluded = False
        self.full_h = W + len(GROUPS) * (BTN + GAP) + 4
        self.setFixedSize(W, self.full_h)

        self._t = 0.0                    # 0 — свёрнута, 1 — раскрыта
        self._hover = -1                 # кнопка под мышью
        self._hl = 0.0                   # плавная подсветка кнопки под мышью
        self._open = -1                  # кнопка, для которой открыта планка
        self._spin = 0.0                 # угол «колёсика», пока идёт перевод
        self._busy = False
        self._live_on = False
        self._drag: QPoint | None = None
        self._dragged = False
        self._icons: dict[tuple[str, str], QPixmap] = {}
        self._logo = round_pixmap(str(theme.resource("icon.png")), LOGO)

        self.bar = MiniBar()
        self.bar.triggered.connect(self._on_bar_action)
        self.bar.hovered.connect(self._on_bar_hover)

        self._anim = QVariantAnimation(self)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._anim.valueChanged.connect(self._set_t)
        self._anim.finished.connect(self._after_anim)
        self._hl_anim = QVariantAnimation(self)
        self._hl_anim.setDuration(130)
        self._hl_anim.setStartValue(0.0)
        self._hl_anim.setEndValue(1.0)
        self._hl_anim.valueChanged.connect(self._set_hl)
        self._spin_anim = QVariantAnimation(self)
        self._spin_anim.setDuration(900)
        self._spin_anim.setStartValue(0.0)
        self._spin_anim.setEndValue(360.0)
        self._spin_anim.setLoopCount(-1)
        self._spin_anim.valueChanged.connect(self._set_spin)

        self._collapse = QTimer(self)        # свернуть через несколько секунд после ухода мыши
        self._collapse.setSingleShot(True)
        self._collapse.timeout.connect(self._maybe_collapse)
        self._bar_close = QTimer(self)       # закрыть планку, если мышь ушла и с неё, и с её кнопки
        self._bar_close.setSingleShot(True)
        self._bar_close.setInterval(350)
        self._bar_close.timeout.connect(self._maybe_close_bar)
        self._dwell = QTimer(self)           # не открывать планки, пока мышь просто пролетает над кнопками
        self._dwell.setSingleShot(True)
        self._dwell.setInterval(90)
        self._dwell.timeout.connect(self._open_hovered)
        self._toast = QTimer(self)
        self._toast.setSingleShot(True)
        self._toast.timeout.connect(self._end_toast)
        self._toast_on = False
        self._apply_mask()

    # ------------------------------------------------------------ геометрия
    def _screen_rect(self) -> QRect:
        s = QGuiApplication.screenAt(self.geometry().center()) or QGuiApplication.primaryScreen()
        return s.availableGeometry()

    def place(self, pos: list | tuple | None) -> None:
        """Поставить панель в сохранённое место (или в правый верхний угол), не дальше края экрана."""
        if pos and len(pos) == 2:
            self.move(int(pos[0]), int(pos[1]))
        else:
            r = QGuiApplication.primaryScreen().availableGeometry()
            self.move(r.right() - W - 24, r.top() + 120)
        self._clamp()

    def _clamp(self) -> None:
        r = self._screen_rect()
        x = min(max(r.left() + 4, self.x()), r.right() - W - 4)
        y = min(max(r.top() + 4, self.y()), r.bottom() - self.full_h - 4)
        if (x, y) != (self.x(), self.y()):
            self.move(x, y)

    def _button_rect(self, i: int) -> QRectF:
        top = W + i * (BTN + GAP)
        return QRectF((W - BTN) / 2, top, BTN, BTN)

    def _index_at(self, pos: QPoint) -> int:
        if self._t < 0.6:
            return -1
        for i in range(len(GROUPS)):
            if self._button_rect(i).adjusted(-4, -4, 4, 4).contains(pos):
                return i
        return -1

    def _apply_mask(self) -> None:
        """Свёрнутая панель занимает только значок: остальное место пропускает мышь насквозь."""
        h = self.full_h if (self._t > 0.0 or self._anim.state() == QVariantAnimation.State.Running) else W
        self.setMask(QRegion(0, 0, W, h))

    # ------------------------------------------------------------ состояние
    def refresh(self) -> None:
        """Состояние программы изменилось: обновить значок и открытую планку."""
        st = self._state()
        busy, live = bool(st.get("busy")), bool(st.get("live_on"))
        if busy != self._busy:
            self._busy = busy
            if busy:
                self._spin_anim.start()
            else:
                self._spin_anim.stop()
        self._live_on = live
        self.update()
        if self._open >= 0 and self.bar.isVisible() and not self._toast_on:
            self._show_bar(self._open, animate=False)

    def toast(self, text: str, error: bool = False, seconds: float = 4.0) -> None:
        """Коротко показать сообщение рядом со значком (ошибка, «текст не найден»)."""
        if not self.isVisible():
            return
        self._open = -1
        self._toast_on = True
        self.bar.set_message(text, error)
        anchor = QRect(self.mapToGlobal(QPoint(0, 0)), self.mapToGlobal(QPoint(W, W)))
        self.bar.open_at(anchor, self._screen_rect())
        self._toast.start(int(seconds * 1000))
        self.update()

    def _end_toast(self) -> None:
        if self._toast_on:
            self._toast_on = False
            self.bar.close_soft()

    def _items(self, group: str) -> list[Item]:
        st = self._state()
        if group == "translate":
            if st.get("live_mode"):
                main = Item("main", "Пауза Live", "pause", True) if st.get("live_on") \
                    else Item("main", "Включить Live", "play", True)
                mode = Item("mode", "Режим: Live", "refresh")
            else:
                main = Item("main", "Перевести", "play", True)
                mode = Item("mode", "Режим: Скриншот", "refresh")
            hide = Item("toggle", "Скрыть перевод", "hide") if st.get("overlay_visible") \
                else Item("toggle", "Показать перевод", "eye")
            return [main, mode, hide]
        if group == "zone":
            has = bool(st.get("has_region"))
            return [Item("zone_new", "Новая", "select", True),
                    Item("zone_show", "Показать", "eye", enabled=has),
                    Item("zone_hide", "Скрыть", "hide", enabled=has and bool(st.get("zone_visible")))]
        return [Item("full", "Обычное окно", "window", True), Item("quit", "Закрыть ATlate", "power")]

    # ------------------------------------------------------------ раскрытие
    def expand(self) -> None:
        self._collapse.stop()
        if self._t >= 1.0 or (self._anim.state() == QVariantAnimation.State.Running
                              and self._anim.endValue() == 1.0):
            return
        self._animate(1.0, 190)

    def collapse(self) -> None:
        self._close_bar()
        self._hover = -1
        if self._t <= 0.0:
            return
        self._animate(0.0, 160)

    def _animate(self, end: float, ms: int) -> None:
        self._anim.stop()
        self._anim.setDuration(max(1, int(ms * abs(end - self._t))))
        self._anim.setStartValue(self._t)
        self._anim.setEndValue(end)
        self._anim.start()
        self._apply_mask()

    def _set_t(self, v) -> None:
        self._t = float(v)
        self.update()

    def _after_anim(self) -> None:
        self._apply_mask()

    def _set_hl(self, v) -> None:
        self._hl = float(v)
        if self._hover >= 0:
            self.update(self._button_rect(self._hover).toAlignedRect().adjusted(-3, -3, 3, 3))

    def _set_spin(self, v) -> None:
        self._spin = float(v)
        self.update(0, 0, W, W)

    def _schedule_collapse(self) -> None:
        self._collapse.start(int(max(0.0, self.collapse_delay) * 1000))

    def _mouse_inside(self) -> bool:
        p = QCursor.pos()
        if self.geometry().contains(p) and (self._t > 0 or p.y() - self.y() <= W):
            return True
        return self.bar.isVisible() and not self._toast_on and self.bar.frameGeometry().contains(p)

    def _maybe_collapse(self) -> None:
        if self._drag is None and not self._mouse_inside():
            self.collapse()

    # ------------------------------------------------------------ планка
    def _show_bar(self, i: int, animate: bool = True) -> None:
        self._toast.stop()
        self._toast_on = False
        self._open = i
        self.bar.set_items(self._items(GROUPS[i].key))
        r = self._button_rect(i).toAlignedRect()
        anchor = QRect(self.mapToGlobal(QPoint(0, r.top())), self.mapToGlobal(QPoint(W, r.bottom())))
        self.bar.open_at(anchor, self._screen_rect(), animate)
        self.update()

    def _close_bar(self) -> None:
        self._dwell.stop()
        self._bar_close.stop()
        if self._toast_on:
            return
        if self._open >= 0:
            self._open = -1
            self.update()
        self.bar.close_soft()

    def _open_hovered(self) -> None:
        if self._hover >= 0 and self._hover != self._open and self._drag is None:
            self._show_bar(self._hover)

    def _maybe_close_bar(self) -> None:
        if self._open < 0:
            return
        p = QCursor.pos()
        over_button = self._index_at(self.mapFromGlobal(p)) == self._open and self.geometry().contains(p)
        if not over_button and not self.bar.frameGeometry().contains(p):
            self._close_bar()

    def _on_bar_hover(self, inside: bool) -> None:
        if self._toast_on:
            return
        if inside:
            self._collapse.stop()
            self._bar_close.stop()
        else:
            self._bar_close.start()
            if not self._mouse_inside():
                self._schedule_collapse()

    def _on_bar_action(self, key: str) -> None:
        self.triggered.emit(key)
        # подписи на планке зависят от состояния («Скрыть перевод» ↔ «Показать перевод»)
        QTimer.singleShot(0, self.refresh)

    # ------------------------------------------------------------ мышь
    def enterEvent(self, e):
        self.expand()
        super().enterEvent(e)

    def leaveEvent(self, e):
        self._dwell.stop()
        if self._hover != -1:
            self._hover = -1
            self.update()
        if self._open >= 0:
            self._bar_close.start()
        if self._drag is None:
            self._schedule_collapse()
        super().leaveEvent(e)

    def mouseMoveEvent(self, e):
        if self._drag is not None:
            target = e.globalPosition().toPoint() - self._drag
            if not self._dragged and (target - self.pos()).manhattanLength() < 4:
                return
            self._dragged = True
            self._close_bar()
            self.move(target)
            return
        i = self._index_at(e.position().toPoint())
        if i != self._hover:
            self._hover = i
            self._hl_anim.stop()
            self._hl = 0.0
            if i >= 0:
                self._hl_anim.start()
                self._dwell.start()
            else:
                self._dwell.stop()
                if self._open >= 0:
                    self._bar_close.start()
            self.update()
        self.setCursor(Qt.CursorShape.PointingHandCursor if i >= 0 else Qt.CursorShape.SizeAllCursor)

    def mousePressEvent(self, e):
        if e.button() != Qt.MouseButton.LeftButton:
            return
        if self._index_at(e.position().toPoint()) < 0:
            # за значок и свободное место панель перетаскивается
            self._drag = e.globalPosition().toPoint() - self.pos()
            self._dragged = False

    def mouseReleaseEvent(self, e):
        if e.button() != Qt.MouseButton.LeftButton:
            return
        if self._drag is not None:
            self._drag = None
            if self._dragged:
                self._clamp()
                self.moved.emit(self.x(), self.y())
            return
        i = self._index_at(e.position().toPoint())
        if i >= 0:
            self.triggered.emit(GROUPS[i].click)
            QTimer.singleShot(0, self.refresh)

    def mouseDoubleClickEvent(self, e):
        if self._index_at(e.position().toPoint()) < 0:
            self._drag = None
            self.triggered.emit("full")

    def showEvent(self, e):
        super().showEvent(e)
        if not self.excluded:
            self.excluded = exclude_from_capture(self)

    def hideEvent(self, e):
        self._collapse.stop()
        self._bar_close.stop()
        self._dwell.stop()
        self._toast.stop()
        self._toast_on = False
        self._spin_anim.stop()
        self._busy = False
        self._open = self._hover = -1
        self.bar.hide()
        self._anim.stop()
        self._t = 0.0
        self._apply_mask()
        super().hideEvent(e)

    def shutdown(self) -> None:
        self.bar.close()
        self.close()

    # ------------------------------------------------------------ рисование
    def _icon(self, name: str, color: str) -> QPixmap:
        key = (name, color)
        if key not in self._icons:
            self._icons[key] = theme.pixmap(name, color, 20)
        return self._icons[key]

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        t = self._t
        h = W + (self.full_h - W) * t
        body = QRectF(0.6, 0.6, W - 1.2, h - 1.2)
        _paint_body(p, body, 16 + (W / 2 - 16) * t)

        # значок: логотип, вокруг — состояние (идёт перевод / Live работает)
        c = W / 2
        p.drawPixmap(QRectF(c - LOGO / 2, c - LOGO / 2, LOGO, LOGO), self._logo, QRectF(self._logo.rect()))
        ring = QRectF(c - LOGO / 2 - 3, c - LOGO / 2 - 3, LOGO + 6, LOGO + 6)
        p.setBrush(Qt.BrushStyle.NoBrush)
        if self._busy:
            p.setPen(QPen(QColor(theme.CREAM), 2.4, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            p.drawArc(ring, int(-self._spin * 16), 100 * 16)
        elif self._live_on:
            p.setPen(QPen(QColor(GREEN), 2.2))
            p.drawEllipse(ring)

        if t <= 0.02:
            p.end()
            return
        if t < 1.0:
            # пока панель раскрывается, кнопки не должны вылезать за её край
            clip = QPainterPath()
            clip.addRoundedRect(body, 16 + (W / 2 - 16) * t, 16 + (W / 2 - 16) * t)
            p.setClipPath(clip)
        cream, ink = QColor(theme.CREAM), QColor(theme.INK)
        for i, g in enumerate(GROUPS):
            # кнопки появляются по очереди, слегка выезжая сверху
            ti = min(1.0, max(0.0, t * 1.5 - i * 0.2))
            if ti <= 0.0:
                continue
            r = self._button_rect(i).translated(0, -10 * (1 - ti))
            p.setOpacity(ti)
            active = i == self._open
            if active:
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(cream)
            else:
                glow = self._hl if i == self._hover else 0.0
                p.setPen(QPen(cream, 1.6))
                p.setBrush(QColor(255, 255, 255, int(20 + 28 * glow)))
            p.drawEllipse(r)
            name = g.icon
            if g.key == "translate" and self._live_on:
                name = "pause"
            pm = self._icon(name, ink.name() if active else cream.name())
            p.drawPixmap(QRectF(r.center().x() - 10, r.center().y() - 10, 20, 20), pm, QRectF(pm.rect()))
        p.setOpacity(1.0)
        p.end()
