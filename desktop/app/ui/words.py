"""«Слова на экране» (ПК): задержите мышь на слове — над ним «Копировать» и «В словарик».

Окно перевода пропускает мышь насквозь (иначе мешало бы листать главу), поэтому слово под мышью
ищется по положению курсора — раз в 0,1 с и только пока перевод на экране. Мышь остановилась на
слове оригинала на DWELL секунд — плашка этого облака на время прячется (под ней виден оригинал),
слово обводится, над ним появляются две кнопки. Мышь ушла — всё возвращается как было.

Чтобы не мешать чтению: после нового перевода кнопки появляются, только когда мышь сдвинули
(мышь, которая просто лежит на облаке, ничего не открывает), и на одном слове — один раз.
В словарик попадает слово оригинала: его и учат. Перевод — из глоссария тайтла или от переводчика.
"""

from __future__ import annotations

import threading
import time

from PySide6.QtCore import QObject, QPoint, QRect, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QCursor, QGuiApplication, QPainter
from PySide6.QtWidgets import QApplication, QHBoxLayout, QLabel, QPushButton, QWidget

from .. import titles, vocab
from ..capture import exclude_from_capture, find_screen
from ..i18n import tr
from ..models import words_of
from . import theme

DWELL = 0.6         # столько мышь должна постоять на слове
LEAVE = 0.35        # столько можно быть вне слова и кнопок, прежде чем они спрячутся
TICK_MS = 100       # пока мышь движется возле перевода
IDLE_MS = 500       # мышь стоит или далеко от перевода: реже — программа меньше просыпается в простое


class WordChips(QWidget):
    """Две кнопки над словом: «Копировать» и «В словарик»; потом — короткий итог вместо них."""

    copy_clicked = Signal()
    add_clicked = Signal()

    def __init__(self):
        super().__init__(None, Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint
                         | Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.WindowDoesNotAcceptFocus
                         | Qt.WindowType.NoDropShadowWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self._excluded = False
        lay = QHBoxLayout(self)
        lay.setContentsMargins(6, 6, 6, 6)
        lay.setSpacing(6)
        style = ("QPushButton { background: #3F2D27; color: #F5E9DA; border: none; border-radius: 13px; "
                 "padding: 5px 12px; font-weight: 600; } QPushButton:hover { background: #574036; }")
        self.btn_copy = QPushButton(theme.icon("copy", "#F5E9DA", 16), tr("Копировать"))
        self.btn_add = QPushButton(theme.icon("plus", "#F5E9DA", 16), tr("В словарик"))
        for b in (self.btn_copy, self.btn_add):
            b.setStyleSheet(style)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            lay.addWidget(b)
        self.btn_copy.clicked.connect(self.copy_clicked)
        self.btn_add.clicked.connect(self.add_clicked)
        self.msg = QLabel()
        self.msg.setStyleSheet("color: #F5E9DA; background: transparent; padding: 3px 8px; font-weight: 600;")
        self.msg.setVisible(False)
        lay.addWidget(self.msg)
        self.anchor = QRect()

    def show_for(self, word: QRect) -> None:
        """Над словом (если сверху нет места — под ним)."""
        self.anchor = QRect(word)
        self.btn_copy.setVisible(True)
        self.btn_add.setVisible(True)
        self.msg.setVisible(False)
        self._place()
        self.show()
        self.raise_()
        if not self._excluded:
            # кнопки не попадают в снимок экрана: Live не примет их за изменение страницы
            self._excluded = exclude_from_capture(self)

    def say(self, text: str) -> None:
        """Вместо кнопок — короткий итог («Скопировано», «hunter — охотник · в словарике»)."""
        self.btn_copy.setVisible(False)
        self.btn_add.setVisible(False)
        self.msg.setText(text)
        self.msg.setVisible(True)
        self._place()

    def _place(self) -> None:
        self.adjustSize()
        a = self.anchor
        screen = QGuiApplication.screenAt(a.center()) or QGuiApplication.primaryScreen()
        geo = screen.availableGeometry()
        x = min(max(geo.left(), a.center().x() - self.width() // 2), geo.right() - self.width())
        y = a.top() - self.height() - 2
        if y < geo.top():
            y = a.bottom() + 4
        self.move(x, y)

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        bg = QColor("#2A1F1B")
        bg.setAlphaF(0.95)
        p.setPen(QColor("#574036"))
        p.setBrush(bg)
        p.drawRoundedRect(self.rect().adjusted(0, 0, -1, -1), 18, 18)


class WordHover(QObject):
    """Следит за мышью над переводом и открывает кнопки над словом. Один на главное окно."""

    added = Signal(object, bool)            # vocab.Entry, уже было в словарике
    failed = Signal(str)
    _done = Signal(object, bool, object)    # из потока перевода: запись, было ли, номер показа
    _error = Signal(str, object)

    def __init__(self, win):
        super().__init__(win)
        self.win = win
        self.chips: WordChips | None = None
        self.timer = QTimer(self)
        self.timer.setInterval(TICK_MS)
        self.timer.timeout.connect(self._tick)
        self._result = None
        self._result_at = 0.0
        self._last_pos = QPoint()
        self._moved_at = 0.0
        self._left_at: float | None = None
        self._fired = None                  # слово, над которым кнопки уже открывались
        self._checked = None                # (точка, перевод), где слово уже искали
        self.hit = None                     # (номер облака, облако, слово) — над чем открыты кнопки
        self.word_rect = QRect()            # рамка слова на экране
        self._busy = False
        self._shown = 0                     # номер показа кнопок: ответ перевода для старого слова не нужен
        self._done.connect(self._on_done, Qt.ConnectionType.QueuedConnection)
        self._error.connect(self._on_error, Qt.ConnectionType.QueuedConnection)

    @property
    def enabled(self) -> bool:
        return bool(self.win.cfg.get("word_hover", True))

    def sync(self) -> None:
        """Перевод на экране сменился или настройка изменилась: следить за мышью, только пока есть что показывать."""
        if self.enabled and self.win.shown_result is not None and self.win.region is not None:
            if not self.timer.isActive():
                self.timer.start()
        else:
            self.timer.stop()
            self.dismiss()

    # ------------------------------------------------------------ слежение
    def _tick(self) -> None:
        res = self.win.shown_result
        if res is None or self.win.region is None or not self.enabled:
            self.timer.stop()
            self.dismiss()
            return
        now = time.monotonic()
        if res is not self._result:              # новый перевод: старые кнопки не к месту
            self._result, self._result_at = res, now
            self.dismiss()
        pos = QCursor.pos()
        self._pace(pos, now)
        if self.chips is not None and self.chips.isVisible():
            near = (self.chips.geometry().adjusted(-8, -8, 8, 8).contains(pos)
                    or self.word_rect.adjusted(-6, -6, 6, 6).contains(pos))
            if near or self._busy:
                self._left_at = None
            elif self._left_at is None:
                self._left_at = now
            elif now - self._left_at > LEAVE:
                self.dismiss()
            return
        if pos != self._last_pos:
            self._last_pos, self._moved_at = pos, now
            if self._fired is not None and not self.word_rect.adjusted(-6, -6, 6, 6).contains(pos):
                self._fired = None
            return
        if self._moved_at <= self._result_at or now - self._moved_at < DWELL or self._checked == (pos, res):
            return
        self._checked = (pos, res)          # мышь стоит на месте — слово под ней ищется один раз
        hit = self.word_at(pos)
        if hit is None or (self._fired is not None and hit[2] is self._fired):
            return
        self._fired = hit[2]
        self.open(hit)

    def _pace(self, pos: QPoint, now: float) -> None:
        """Чаще, пока мышь движется над выделенной областью; реже, когда стоит или далеко."""
        o, r = self._origin(), self.win.region
        near = o.x() - 40 <= pos.x() <= o.x() + r.w + 40 and o.y() - 40 <= pos.y() <= o.y() + r.h + 40
        busy = self.chips is not None and self.chips.isVisible()
        fast = busy or (near and (pos != self._last_pos or now - self._moved_at < DWELL + 0.5))
        want = TICK_MS if fast else IDLE_MS
        if self.timer.interval() != want:
            self.timer.setInterval(want)

    def _origin(self) -> QPoint:
        """Левый верхний угол выделенной области на экране (в координатах Qt)."""
        r = self.win.region
        geo = find_screen(r.screen).geometry()
        return QPoint(geo.x() + r.x, geo.y() + r.y)

    def word_at(self, pos: QPoint):
        """Слово оригинала под точкой экрана → (номер облака, облако, слово) или None."""
        res, k = self.win.shown_result, float(getattr(self.win, "shown_k", 1.0) or 1.0)
        if res is None:
            return None
        o = self._origin()
        x, y = (pos.x() - o.x()) * k, (pos.y() - o.y()) * k
        best = None
        for i, b in enumerate(res.bubbles):
            if not (b.box.x - 4 <= x <= b.box.x2 + 4 and b.box.y - 4 <= y <= b.box.y2 + 4):
                continue
            for line in b.lines:
                for w in words_of(line):
                    bx = w.box
                    if bx.x - 2 <= x <= bx.x2 + 2 and bx.y - 2 <= y <= bx.y2 + 2 and vocab.clean(w.text):
                        if best is None or bx.w * bx.h < best[2].box.w * best[2].box.h:
                            best = (i, b, w)
        return best

    # ------------------------------------------------------------ кнопки
    def open(self, hit) -> None:
        i, bubble, word = hit
        k = float(getattr(self.win, "shown_k", 1.0) or 1.0)
        local = QRectF(word.box.x / k, word.box.y / k, word.box.w / k, word.box.h / k)
        o = self._origin()
        self.word_rect = local.translated(o.x(), o.y()).toAlignedRect()
        self.hit = hit
        self._shown += 1
        self._busy = False
        self._left_at = None
        self.win.overlay.peek(i, local)
        if self.chips is None:
            self.chips = WordChips()
            self.chips.copy_clicked.connect(self.copy)
            self.chips.add_clicked.connect(self.add)
        self.chips.show_for(self.word_rect)

    def dismiss(self) -> None:
        if self.chips is not None and self.chips.isVisible():
            self.chips.hide()
        self.win.overlay.unpeek()
        self.hit = None
        self._busy = False

    def _lang(self, bubble) -> str:
        return bubble.lang or self.win.cfg.get("source_lang", "")

    def copy(self) -> None:
        if self.hit is None:
            return
        word = vocab.clean(self.hit[2].text)
        QApplication.clipboard().setText(word)
        self.chips.say(tr("Скопировано: {0}", word))
        QTimer.singleShot(1200, self._close_if_idle)

    def add(self) -> None:
        if self.hit is None or self._busy:
            return
        _i, bubble, w = self.hit
        cfg = self.win.cfg
        lang = self._lang(bubble)
        word = vocab.base_form(w.text, lang)
        old = vocab.find(vocab.load(), word, lang)
        if old is not None and old.translation:
            self.chips.say(tr("Уже в словарике: {0} — {1}", old.word, old.translation))
            self.added.emit(old, True)
            QTimer.singleShot(2500, self._close_if_idle)
            return
        self._busy = True
        self.chips.say(tr("Перевожу «{0}»…", word))
        shown = self._shown
        title = titles.load(cfg.get("active_title", ""))
        entry = vocab.Entry(word, lang=lang, tgt=cfg.get("target_lang", "ru"), context=bubble.text,
                            context_tr=bubble.translation, title=title.name if title else "")
        glossary = self.win._current_glossary() if hasattr(self.win, "_current_glossary") else None

        def work() -> None:
            try:
                entry.translation, _by = vocab.translate_word(cfg, word, lang, entry.tgt, entry.context,
                                                             entry.context_tr, glossary)
            except Exception as e:  # noqa: BLE001 — переводчик не ответил: слово всё равно сохраним
                self._error.emit(str(e).splitlines()[0][:160], (entry, shown))
                return
            saved, existed = vocab.add(entry)
            self._done.emit(saved, existed, shown)

        threading.Thread(target=work, daemon=True, name="vocab-word").start()

    def _on_done(self, entry, existed: bool, shown) -> None:
        self._busy = False
        self.added.emit(entry, existed)
        if shown == self._shown and self.chips is not None and self.chips.isVisible():
            self.chips.say(tr("{0} — {1} · в словарике", entry.word, entry.translation or "?"))
            QTimer.singleShot(2500, self._close_if_idle)

    def _on_error(self, message: str, data) -> None:
        entry, shown = data
        self._busy = False
        saved, existed = vocab.add(entry)       # без перевода: его можно дописать в словарике
        self.added.emit(saved, existed)
        self.failed.emit(message)
        if shown == self._shown and self.chips is not None and self.chips.isVisible():
            self.chips.say(tr("{0} — в словарике, перевод не получен", entry.word))
            QTimer.singleShot(2500, self._close_if_idle)

    def _close_if_idle(self) -> None:
        """Итог показан — убрать кнопки, если мышь уже не над ними."""
        if self.chips is None or not self.chips.isVisible() or self._busy:
            return
        if not self.chips.geometry().contains(QCursor.pos()):
            self.dismiss()

    def shutdown(self) -> None:
        self.timer.stop()
        self.dismiss()
        if self.chips is not None:
            self.chips.close()
            self.chips = None
