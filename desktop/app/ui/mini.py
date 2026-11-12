"""Компактный режим: маленькая панель поверх экрана вместо большого окна.

Свёрнутая панель — небольшой значок. При наведении она раскрывается в столбик круглых
кнопок, а кнопка под мышью сама вытягивается вбок в «таблетку», и в ней появляются
её действия:

    Перевод — перевести (или пауза Live), сменить режим, скрыть перевод
    Зона    — выделить новую, показать, скрыть
    Выход   — вернуться к обычному окну или закрыть программу

Так же — вытягиваясь от значка — показываются короткие сообщения (ошибка, «текст не найден»).
Панель остаётся раскрытой, пока на ней мышь, и ещё несколько секунд после (настраивается).

Всё рисуется в одном окне. Оно шире самой панели (с запасом на вытянутую кнопку с обеих
сторон), но мышь ловит только там, где что-то нарисовано, — остальное её пропускает.

Про нагрузку: панель ничего не делает, пока её не трогают — нет постоянных таймеров,
анимации идут только во время перехода, значки рисуются один раз и запоминаются.
Окно не забирает фокус у читалки и по возможности скрыто от снимков экрана.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from PySide6.QtCore import QEasingCurve, QPoint, QPointF, QRect, QRectF, Qt, QTimer, QVariantAnimation, Signal
from PySide6.QtGui import (QColor, QCursor, QFont, QFontMetricsF, QGuiApplication, QLinearGradient, QPainter,
                           QPainterPath, QPen, QPixmap, QRegion)
from PySide6.QtWidgets import QWidget

from ..capture import exclude_from_capture
from . import theme
from .widgets import round_pixmap

W = 56                 # ширина панели и размер свёрнутого значка
BTN = 40               # диаметр круглой кнопки
GAP = 8
LOGO = 40
EXT = 600              # запас окна по бокам панели: сюда вытягивается кнопка
CHIP_H = 30            # действие внутри вытянутой кнопки
CHIP_PAD = 12
CHIP_GAP = 6
ICON = 16
TOAST_W = 420
GREEN = "#9BE7C4"      # «работает» — тот же цвет, что у точки статуса в главном окне
WARN = "#FFB4A8"

_FLAGS = (Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint
          | Qt.WindowType.WindowDoesNotAcceptFocus | Qt.WindowType.NoDropShadowWindowHint)


@dataclass
class Item:
    """Действие внутри вытянутой кнопки."""

    key: str
    text: str
    icon: str = ""
    primary: bool = False
    enabled: bool = True
    x: float = 0.0         # место внутри вытянутой кнопки (от её начала) и ширина — считает раскладка
    w: float = 0.0


@dataclass
class Group:
    """Круглая кнопка панели: значок, действие по щелчку и действия, которые в ней появляются."""

    key: str
    icon: str
    click: str             # что делает щелчок по самой кнопке
    items: list[Item] = field(default_factory=list)
    width: float = BTN     # полная ширина вытянутой кнопки


def _gradient(r: QRectF) -> QLinearGradient:
    """Тот же градиент, что у шапки главного окна."""
    g = QLinearGradient(r.topLeft(), r.bottomRight())
    g.setColorAt(0, QColor(theme.INK))
    g.setColorAt(1, QColor(theme.HERO_END))
    return g


def _rounded(r: QRectF, radius: float) -> QPainterPath:
    path = QPainterPath()
    path.addRoundedRect(r, radius, radius)
    return path


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
        self.groups = [Group("translate", "play", "main"), Group("zone", "select", "zone_new"),
                       Group("exit", "exit", "full")]
        n = len(self.groups)
        self.full_h = W + n * (BTN + GAP) + 4
        self.setFixedSize(W + 2 * EXT, self.full_h)

        self._font = QFont(theme.font_family())
        self._font.setPixelSize(13)
        self._font.setWeight(QFont.Weight.DemiBold)
        self._fm = QFontMetricsF(self._font)

        self._t = 0.0                    # 0 — свёрнута, 1 — раскрыта
        # насколько вытянута каждая кнопка (0..1); последний — сообщение у значка
        self._ext = [0.0] * (n + 1)
        self._ext_from = [0.0] * (n + 1)
        self._ext_to = [0.0] * (n + 1)
        self._side = 1                   # куда вытягивать: 1 — вправо, -1 — влево
        self._hover = -1                 # круглая кнопка под мышью
        self._chip: tuple[int, int] | None = None   # действие под мышью: (кнопка, номер)
        self._hl = 0.0                   # плавная подсветка того, что под мышью
        self._open = -1                  # вытянутая кнопка
        self._inside = False
        self._collapse_pending = False
        self._spin = 0.0                 # угол «колёсика», пока идёт перевод
        self._busy = False
        self._live_on = False
        self._drag: QPoint | None = None
        self._dragged = False
        self._toast_lines: list[str] = []
        self._toast_error = False
        self._toast_w = 0.0
        self._icons: dict[tuple[str, str, int], QPixmap] = {}
        self._logo = round_pixmap(str(theme.resource("icon.png")), LOGO)

        self._anim = QVariantAnimation(self)             # раскрытие панели
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._anim.valueChanged.connect(self._set_t)
        self._anim.finished.connect(self._apply_mask)
        self._ext_anim = QVariantAnimation(self)         # вытягивание кнопок
        self._ext_anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._ext_anim.setStartValue(0.0)
        self._ext_anim.setEndValue(1.0)
        self._ext_anim.valueChanged.connect(self._set_ext)
        self._ext_anim.finished.connect(self._after_ext)
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
        self._retract = QTimer(self)         # втянуть кнопку, если мышь ушла с неё
        self._retract.setSingleShot(True)
        self._retract.setInterval(350)
        self._retract.timeout.connect(self._maybe_retract)
        self._dwell = QTimer(self)           # не вытягивать кнопки, пока мышь просто пролетает над ними
        self._dwell.setSingleShot(True)
        self._dwell.setInterval(90)
        self._dwell.timeout.connect(self._open_hovered)
        self._toast = QTimer(self)
        self._toast.setSingleShot(True)
        self._toast.timeout.connect(self._end_toast)
        self._apply_mask()

    # ------------------------------------------------------------ геометрия
    def panel_pos(self) -> QPoint:
        """Левый верхний угол самой панели на экране (окно шире: по бокам запас)."""
        return self.pos() + QPoint(EXT, 0)

    def _screen_rect(self) -> QRect:
        s = QGuiApplication.screenAt(self.panel_pos() + QPoint(W // 2, W // 2)) or QGuiApplication.primaryScreen()
        return s.availableGeometry()

    def place(self, pos: list | tuple | None) -> None:
        """Поставить панель в сохранённое место (или в правый верхний угол), не дальше края экрана."""
        if pos and len(pos) == 2:
            self.move(int(pos[0]) - EXT, int(pos[1]))
        else:
            r = QGuiApplication.primaryScreen().availableGeometry()
            self.move(r.right() - W - 24 - EXT, r.top() + 120)
        self._clamp()

    def _clamp(self) -> None:
        r = self._screen_rect()
        p = self.panel_pos()
        x = min(max(r.left() + 4, p.x()), r.right() - W - 4)
        y = min(max(r.top() + 4, p.y()), r.bottom() - self.full_h - 4)
        if (x, y) != (p.x(), p.y()):
            self.move(x - EXT, y)

    def _body_rect(self) -> QRectF:
        return QRectF(EXT, 0, W, W + (self.full_h - W) * self._t)

    def _button_rect(self, i: int) -> QRectF:
        return QRectF(EXT + (W - BTN) / 2, W + i * (BTN + GAP), BTN, BTN)

    def _pill_rect(self, i: int, ext: float | None = None) -> QRectF:
        """Кнопка i, вытянутая на долю ext (по умолчанию — как сейчас)."""
        r = self._button_rect(i)
        e = self._ext[i] if ext is None else ext
        extra = (self.groups[i].width - BTN) * e
        return r.adjusted(0, 0, extra, 0) if self._side > 0 else r.adjusted(-extra, 0, 0, 0)

    def _toast_rect(self, ext: float | None = None) -> QRectF:
        """Сообщение: вытягивается от значка (у свёрнутой панели — сам значок целиком)."""
        e = self._ext[-1] if ext is None else ext
        inset = 4.0 * self._t
        r = QRectF(EXT + inset, inset, W - 2 * inset, W - 2 * inset)
        extra = self._toast_w * e
        return r.adjusted(0, 0, extra, 0) if self._side > 0 else r.adjusted(-extra, 0, 0, 0)

    def _chip_rect(self, i: int, k: int) -> QRectF:
        """Место действия k внутри полностью вытянутой кнопки i."""
        r = self._button_rect(i)
        it = self.groups[i].items[k]
        y = r.top() + (BTN - CHIP_H) / 2
        if self._side > 0:
            return QRectF(r.left() + it.x, y, it.w, CHIP_H)
        return QRectF(r.right() - it.x - it.w, y, it.w, CHIP_H)     # влево — зеркально

    def _layout(self, i: int) -> None:
        """Разложить действия внутри кнопки: сразу после её значка, одно за другим."""
        g = self.groups[i]
        g.items = self._items(g.key)
        x = float(BTN)
        for it in g.items:
            it.w = 2 * CHIP_PAD + self._fm.horizontalAdvance(it.text) + (ICON + 6 if it.icon else 0)
            it.x = x
            x += it.w + CHIP_GAP
        g.width = x - CHIP_GAP + (BTN - CHIP_H) / 2

    def _pick_side(self, need: float) -> None:
        """Вытягивать вправо, если там хватает места; иначе туда, где его больше."""
        scr = self._screen_rect()
        x = self.panel_pos().x()
        room_right = scr.right() - (x + W)
        room_left = x - scr.left()
        self._side = 1 if room_right >= need + 8 or room_right >= room_left else -1

    def _apply_mask(self) -> None:
        """Мышь ловится только там, где панель нарисована; остальная часть окна её пропускает."""
        running = self._anim.state() == QVariantAnimation.State.Running
        region = QRegion(EXT, 0, W, self.full_h if (self._t > 0.0 or running) else W)
        for i in range(len(self.groups)):
            if self._ext[i] > 0.0 or self._ext_to[i] > 0.0:
                region = region.united(QRegion(self._pill_rect(i, 1.0).toAlignedRect().adjusted(-2, -2, 2, 2)))
        if self._ext[-1] > 0.0 or self._ext_to[-1] > 0.0:
            region = region.united(QRegion(self._toast_rect(1.0).toAlignedRect().adjusted(-2, -2, 2, 2)))
        self.setMask(region)

    def visible_rect(self) -> QRect:
        """Что панель сейчас занимает на экране (для проверки, попадёт ли она в снимок)."""
        return self.mask().boundingRect().translated(self.pos())

    def _hit(self, pos: QPointF) -> bool:
        """Находится ли точка окна на нарисованной части панели."""
        if self._t <= 0.0:
            if QRectF(EXT, 0, W, W).contains(pos):
                return True
        elif self._body_rect().contains(pos):
            return True
        for i in range(len(self.groups)):
            if self._ext[i] > 0.0 and self._pill_rect(i).adjusted(-3, -3, 3, 3).contains(pos):
                return True
        return self._ext[-1] > 0.0 and self._toast_rect().contains(pos)

    def _index_at(self, pos: QPointF) -> int:
        if self._t < 0.6:
            return -1
        for i in range(len(self.groups)):
            if self._button_rect(i).adjusted(-4, -4, 4, 4).contains(pos):
                return i
        return -1

    def _chip_at(self, pos: QPointF) -> tuple[int, int] | None:
        i = self._open
        if i < 0 or self._ext[i] < 0.9:
            return None
        for k in range(len(self.groups[i].items)):
            if self._chip_rect(i, k).contains(pos):
                return i, k
        return None

    # ------------------------------------------------------------ состояние
    def refresh(self) -> None:
        """Состояние программы изменилось: обновить значок и подписи в вытянутой кнопке."""
        st = self._state()
        busy, live = bool(st.get("busy")), bool(st.get("live_on"))
        if busy != self._busy:
            self._busy = busy
            if busy:
                self._spin_anim.start()
            else:
                self._spin_anim.stop()
        self._live_on = live
        if self._open >= 0:
            self._layout(self._open)
            self._apply_mask()
        self.update()

    def toast(self, text: str, error: bool = False, seconds: float = 4.0) -> None:
        """Коротко показать сообщение, вытянув его от значка (ошибка, «текст не найден»)."""
        if not self.isVisible():
            return
        self._toast_lines = self._wrap(text, TOAST_W - 26, 2)
        self._toast_error = error
        self._toast_w = 26 + max(self._fm.horizontalAdvance(s) for s in self._toast_lines)
        self._pick_side(self._toast_w)
        self._stretch(len(self.groups))
        self._toast.start(int(seconds * 1000))

    def _end_toast(self) -> None:
        if self._ext_to[-1] > 0.0:
            self._stretch(self._open)

    def _wrap(self, text: str, width: float, lines: int) -> list[str]:
        """Разбить текст на строки по ширине; лишнее в конце заменить многоточием."""
        out, cur = [], ""
        for word in text.split():
            trial = f"{cur} {word}".strip()
            if cur and self._fm.horizontalAdvance(trial) > width:
                out.append(cur)
                cur = word
            else:
                cur = trial
        out.append(cur)
        if len(out) > lines:
            out = out[:lines]
            last = out[-1] + "…"
            while len(last) > 2 and self._fm.horizontalAdvance(last) > width:
                last = last[:-2] + "…"
            out[-1] = last
        return out

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

    # ------------------------------------------------------------ раскрытие панели
    def expand(self) -> None:
        self._collapse.stop()
        self._collapse_pending = False
        if self._t >= 1.0 or (self._anim.state() == QVariantAnimation.State.Running
                              and self._anim.endValue() == 1.0):
            return
        self._animate(1.0, 190)

    def collapse(self) -> None:
        """Свернуть панель. Вытянутая кнопка сначала втягивается — так ничего не вылезает за край."""
        self._dwell.stop()
        self._retract.stop()
        self._hover = -1
        self._chip = None
        self._inside = False        # если мышь осталась на значке, следующее движение раскроет панель снова
        if self._open >= 0 and self._ext[self._open] > 0.0:
            self._collapse_pending = True
            self._open = -1
            self._stretch(-1, fast=True)
            return
        self._open = -1
        if self._t > 0.0:
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

    def _set_hl(self, v) -> None:
        self._hl = float(v)
        self.update()

    def _set_spin(self, v) -> None:
        self._spin = float(v)
        self.update(EXT, 0, W, W)

    def _schedule_collapse(self) -> None:
        self._collapse.start(int(max(0.0, self.collapse_delay) * 1000))

    def _mouse_inside(self) -> bool:
        return self._hit(QPointF(self.mapFromGlobal(QCursor.pos())))

    def _maybe_collapse(self) -> None:
        if self._drag is None and not self._mouse_inside():
            self.collapse()

    # ------------------------------------------------------------ вытягивание кнопки
    def _stretch(self, target: int, fast: bool = False) -> None:
        """Вытянуть кнопку target (номер кнопки, их число — сообщение, -1 — ничего); остальные втянуть."""
        for i in range(len(self._ext)):
            self._ext_from[i] = self._ext[i]
            self._ext_to[i] = 1.0 if i == target else 0.0
        if target != len(self.groups) and self._ext_to[-1] == 0.0:
            self._toast.stop()
        self._ext_anim.stop()
        self._ext_anim.setDuration(130 if fast else 240)
        self._apply_mask()
        self._ext_anim.start()

    def _set_ext(self, v) -> None:
        k = float(v)
        for i in range(len(self._ext)):
            self._ext[i] = self._ext_from[i] + (self._ext_to[i] - self._ext_from[i]) * k
        self.update()

    def _after_ext(self) -> None:
        self._apply_mask()
        if self._collapse_pending:
            self._collapse_pending = False
            if self._t > 0.0:
                self._animate(0.0, 160)

    def _open_group(self, i: int) -> None:
        if self._t < 1.0:
            return
        self._layout(i)
        if self._open < 0 or self._ext[self._open] <= 0.0:
            self._pick_side(self.groups[i].width)   # сторону не меняем, пока что-то уже вытянуто
        self._open = i
        self._stretch(i)

    def _close_group(self) -> None:
        self._dwell.stop()
        self._retract.stop()
        if self._open >= 0:
            self._open = -1
            self._chip = None
            self._stretch(len(self.groups) if self._toast.isActive() else -1)

    def _open_hovered(self) -> None:
        if self._hover >= 0 and self._hover != self._open and self._drag is None:
            self._open_group(self._hover)

    def _maybe_retract(self) -> None:
        if self._open < 0:
            return
        pos = QPointF(self.mapFromGlobal(QCursor.pos()))
        if not self._pill_rect(self._open).adjusted(-4, -4, 4, 4).contains(pos):
            self._close_group()

    # ------------------------------------------------------------ мышь
    def _enter(self) -> None:
        self._inside = True
        self.expand()

    def _leave(self) -> None:
        self._inside = False
        self._dwell.stop()
        if self._hover != -1 or self._chip is not None:
            self._hover = -1
            self._chip = None
            self.update()
        if self._open >= 0:
            self._retract.start()
        if self._drag is None:
            self._schedule_collapse()
        self.unsetCursor()

    def enterEvent(self, e):
        if self._hit(e.position()):
            self._enter()
        super().enterEvent(e)

    def leaveEvent(self, e):
        if self._inside:
            self._leave()
        super().leaveEvent(e)

    def mouseMoveEvent(self, e):
        if self._drag is not None:
            target = e.globalPosition().toPoint() - self._drag
            if not self._dragged and (target - self.pos()).manhattanLength() < 4:
                return
            self._dragged = True
            self._close_group()
            self.move(target)
            return
        pos = e.position()
        inside = self._hit(pos)
        if inside != self._inside:
            # окно шире панели: там, где ничего не нарисовано, мыши для панели «нет»
            if inside:
                self._enter()
            else:
                self._leave()
        if not inside:
            return
        chip = self._chip_at(pos)
        i = self._open if chip is not None else self._index_at(pos)
        if i != self._hover or chip != self._chip:
            changed_button = i != self._hover
            self._hover, self._chip = i, chip
            self._hl_anim.stop()
            self._hl = 0.0
            self._hl_anim.start()
            if changed_button:
                if i >= 0 and i != self._open:
                    self._dwell.start()
                else:
                    self._dwell.stop()
            self.update()
        if self._open >= 0:
            if self._pill_rect(self._open).adjusted(-4, -4, 4, 4).contains(pos):
                self._retract.stop()
            elif not self._retract.isActive():
                self._retract.start()
        clickable = (chip is not None and self.groups[chip[0]].items[chip[1]].enabled) or \
            (chip is None and i >= 0)
        on_handle = chip is None and i < 0 and self._body_rect().contains(pos)
        self.setCursor(Qt.CursorShape.PointingHandCursor if clickable else
                       Qt.CursorShape.SizeAllCursor if on_handle else Qt.CursorShape.ArrowCursor)

    def mousePressEvent(self, e):
        if e.button() != Qt.MouseButton.LeftButton:
            return
        pos = e.position()
        if self._chip_at(pos) is None and self._index_at(pos) < 0 and \
                (self._body_rect().contains(pos) or self._t <= 0.0):
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
                p = self.panel_pos()
                self.moved.emit(p.x(), p.y())
            return
        pos = e.position()
        chip = self._chip_at(pos)
        if chip is not None:
            it = self.groups[chip[0]].items[chip[1]]
            if it.enabled:
                self.triggered.emit(it.key)
                # подписи зависят от состояния («Скрыть перевод» ↔ «Показать перевод»)
                QTimer.singleShot(0, self.refresh)
            return
        i = self._index_at(pos)
        if i >= 0:
            self.triggered.emit(self.groups[i].click)
            QTimer.singleShot(0, self.refresh)

    def mouseDoubleClickEvent(self, e):
        pos = e.position()
        if self._chip_at(pos) is None and self._index_at(pos) < 0 and self._hit(pos):
            self._drag = None
            self.triggered.emit("full")

    def showEvent(self, e):
        super().showEvent(e)
        if not self.excluded:
            self.excluded = exclude_from_capture(self)

    def hideEvent(self, e):
        for t in (self._collapse, self._retract, self._dwell, self._toast):
            t.stop()
        for a in (self._anim, self._ext_anim, self._hl_anim, self._spin_anim):
            a.stop()
        self._busy = False
        self._inside = False
        self._collapse_pending = False
        self._open = self._hover = -1
        self._chip = None
        self._t = 0.0
        for i in range(len(self._ext)):
            self._ext[i] = self._ext_from[i] = self._ext_to[i] = 0.0
        self._apply_mask()
        super().hideEvent(e)

    def shutdown(self) -> None:
        self.close()

    # ------------------------------------------------------------ рисование
    def _icon(self, name: str, color: str, size: int = 20) -> QPixmap:
        key = (name, color, size)
        if key not in self._icons:
            self._icons[key] = theme.pixmap(name, color, size)
        return self._icons[key]

    def _draw_icon(self, p: QPainter, name: str, color: str, c: QPointF, size: int = 20) -> None:
        pm = self._icon(name, color, size)
        p.drawPixmap(QRectF(c.x() - size / 2, c.y() - size / 2, size, size), pm, QRectF(pm.rect()))

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        t = self._t
        cream, ink = QColor(theme.CREAM), QColor(theme.INK)
        edge = QColor(cream)
        edge.setAlpha(90)
        radius = 16 + (W / 2 - 16) * t
        body = self._body_rect().adjusted(0.6, 0.6, -0.6, -0.6)
        body_path = _rounded(body, radius)
        p.fillPath(body_path, _gradient(body))
        p.setPen(QPen(edge, 1.2))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawPath(body_path)

        # сообщение вытягивается от значка
        if self._ext[-1] > 0.0 and self._toast_lines:
            self._paint_toast(p, edge)

        # значок: логотип, вокруг — состояние (идёт перевод / Live работает)
        c = QPointF(EXT + W / 2, W / 2)
        p.drawPixmap(QRectF(c.x() - LOGO / 2, c.y() - LOGO / 2, LOGO, LOGO), self._logo, QRectF(self._logo.rect()))
        ring = QRectF(c.x() - LOGO / 2 - 3, c.y() - LOGO / 2 - 3, LOGO + 6, LOGO + 6)
        p.setBrush(Qt.BrushStyle.NoBrush)
        if self._busy:
            p.setPen(QPen(cream, 2.4, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            p.drawArc(ring, int(-self._spin * 16), 100 * 16)
        elif self._live_on:
            p.setPen(QPen(QColor(GREEN), 2.2))
            p.drawEllipse(ring)

        if t <= 0.02:
            p.end()
            return
        p.setFont(self._font)
        for i, g in enumerate(self.groups):
            e = self._ext[i]
            # кнопки появляются по очереди, слегка выезжая сверху
            ti = min(1.0, max(0.0, t * 1.5 - i * 0.2))
            if ti <= 0.0:
                continue
            p.save()
            if t < 1.0:
                # пока панель раскрывается или сворачивается, кнопки не выходят за её край
                p.setClipPath(body_path)
            p.setOpacity(ti)
            r = self._pill_rect(i).translated(0, -10 * (1 - ti))
            hot = i == self._hover and self._chip is None
            if e > 0.0:
                # вытянутая кнопка: непрозрачная «таблетка» поверх панели
                pill = _rounded(r, BTN / 2)
                p.fillPath(pill, _gradient(r))
                p.setPen(QPen(cream, 1.6))
                p.setBrush(QColor(255, 255, 255, 20))
                p.drawPath(pill)
            else:
                p.setPen(QPen(cream, 1.6))
                p.setBrush(QColor(255, 255, 255, int(20 + 28 * (self._hl if hot else 0.0))))
                p.drawEllipse(r)
            # значок кнопки остаётся на месте; у вытянутой он в светлом кружке
            home = self._button_rect(i).translated(0, -10 * (1 - ti))
            name = "pause" if (g.key == "translate" and self._live_on) else g.icon
            if e > 0.0:
                p.setOpacity(ti * e)
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(QColor("#FFF4E4") if hot else cream)
                p.drawEllipse(home.adjusted(5, 5, -5, -5))
                self._draw_icon(p, name, ink.name(), home.center())
                p.setOpacity(ti * (1 - e))
            if e < 1.0:
                self._draw_icon(p, name, cream.name(), home.center())
            if e > 0.0:
                self._paint_chips(p, i, r, e)
            p.restore()
        p.end()

    def _paint_chips(self, p: QPainter, i: int, pill: QRectF, e: float) -> None:
        """Действия внутри вытянутой кнопки: проявляются по очереди, пока она растёт."""
        cream, ink = QColor(theme.CREAM), QColor(theme.INK)
        p.setClipPath(_rounded(pill.adjusted(1, 1, -1, -1), BTN / 2), Qt.ClipOperation.IntersectClip)
        for k, it in enumerate(self.groups[i].items):
            a = min(1.0, max(0.0, (e - 0.3 - 0.1 * k) / 0.5))
            if a <= 0.0:
                continue
            r = self._chip_rect(i, k)
            # слегка «догоняет» с той стороны, откуда растёт кнопка
            r.translate(-self._side * 10 * (1 - a), 0)
            hot = self._chip == (i, k) and it.enabled
            p.setOpacity(a * (1.0 if it.enabled else 0.4))
            path = _rounded(r, CHIP_H / 2)
            if it.primary:
                p.fillPath(path, QColor("#FFF4E4") if hot else cream)
                fg = ink
            else:
                p.fillPath(path, QColor(255, 255, 255, int(20 + 26 * (self._hl if hot else 0.0))))
                p.setPen(QPen(cream, 1.4))
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawPath(path)
                fg = cream
            x = r.left() + CHIP_PAD
            if it.icon:
                self._draw_icon(p, it.icon, fg.name(), QPointF(x + ICON / 2, r.center().y()), ICON)
                x += ICON + 6
            p.setPen(fg)
            p.drawText(QRectF(x, r.top(), r.right() - x, r.height()),
                       int(Qt.AlignmentFlag.AlignVCenter.value) | int(Qt.AlignmentFlag.AlignLeft.value), it.text)

    def _paint_toast(self, p: QPainter, edge: QColor) -> None:
        e = self._ext[-1]
        r = self._toast_rect().adjusted(0.6, 0.6, -0.6, -0.6)
        path = _rounded(r, min(16 + (W / 2 - 16) * self._t, r.height() / 2))
        p.save()
        p.fillPath(path, _gradient(r))
        p.setPen(QPen(edge, 1.2))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawPath(path)
        p.setClipPath(path)
        p.setOpacity(min(1.0, max(0.0, (e - 0.35) / 0.5)))
        p.setFont(self._font)
        p.setPen(QColor(WARN if self._toast_error else theme.CREAM))
        full = self._toast_rect(1.0)
        icon_w = W - 8.0 * self._t          # столько в начале занимает сам значок
        text = QRectF(full.left() + icon_w + 4, full.top(), self._toast_w - 16, full.height()) \
            if self._side > 0 else QRectF(full.left() + 14, full.top(), self._toast_w - 16, full.height())
        p.drawText(text, int(Qt.AlignmentFlag.AlignVCenter.value) | int(Qt.AlignmentFlag.AlignLeft.value),
                   "\n".join(self._toast_lines))
        p.restore()
