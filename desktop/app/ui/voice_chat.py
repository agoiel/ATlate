"""«Голос в чат» в интерфейсе: запись по горячей клавише и плашка состояния поверх экрана.

Как пользоваться:
* удерживать клавишу и говорить — отпустить, когда закончили (как рация в играх);
* или нажать один раз — запись остановится сама, когда игрок замолчит (или по второму нажатию).
Что именно выбрано, видно через 0,35 с после нажатия: клавиша ещё нажата — значит, удерживают.

Распознавание и перевод идут в фоновом потоке, игра и окно не замирают.
Логика без Qt — в app/voice.py.
"""

from __future__ import annotations

import threading
import time

from PySide6.QtCore import QObject, QRect, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QCursor, QFont, QFontMetrics, QGuiApplication, QPainter
from PySide6.QtWidgets import QApplication, QWidget

from .. import voice
from ..capture import exclude_from_capture
from ..hotkeys import key_down
from ..i18n import tr
from . import theme

HOLD_AFTER = 0.35           # клавишу держат дольше — значит, «рация»: запись до отпускания


class VoiceToast(QWidget):
    """Плашка поверх всех окон внизу экрана: «Слушаю…» с уровнем звука, «Распознаю…», перевод.

    Не забирает фокус у игры, мышь проходит сквозь неё, в снимки экрана не попадает
    (иначе экранный переводчик мог бы взяться переводить саму плашку).
    """

    COLORS = {"listen": "#E5484D", "busy": "#D9B27C", "done": "#3FB27F", "error": "#F2994A"}

    def __init__(self):
        super().__init__(None, Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint
                         | Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.WindowDoesNotAcceptFocus
                         | Qt.WindowType.WindowTransparentForInput | Qt.WindowType.NoDropShadowWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.kind, self.title, self.body, self.note, self.level = "", "", "", "", 0.0
        self._excluded = False
        self._hide = QTimer(self)
        self._hide.setSingleShot(True)
        self._hide.timeout.connect(self.hide)
        self._title_font = QFont(self.font())
        self._title_font.setPointSizeF(max(10.0, self.font().pointSizeF() + 1))
        self._title_font.setBold(True)

    def show_state(self, kind: str, title: str, body: str = "", seconds: float = 0, note: str = "") -> None:
        """note — строка под текстом бледнее (например, что было сказано, под переводом)."""
        self.kind, self.title, self.body, self.note = kind, title, body, note
        self.level = 0.0
        self._fit()
        if not self.isVisible():
            self.show()
            if not self._excluded:
                self._excluded = exclude_from_capture(self)
        self.raise_()
        self.update()
        if seconds:
            self._hide.start(int(seconds * 1000))
        else:
            self._hide.stop()

    def set_level(self, level: float) -> None:
        self.level = level
        self.update()

    # ------------------------------------------------------------ размеры и место
    PAD, DOT, GAP = 16, 10, 10

    def _text_rects(self, width: int) -> tuple[QRect, QRect, QRect]:
        """Где заголовок, текст и бледная строка при заданной ширине плашки."""
        left = self.PAD + self.DOT + self.GAP
        inner = width - left - self.PAD
        tm, bm = QFontMetrics(self._title_font), QFontMetrics(self.font())
        title = QRect(left, self.PAD, inner, tm.height())
        body = QRect(left, title.bottom(), inner, 0)
        if self.body:
            h = bm.boundingRect(QRect(0, 0, inner, 2000), int(Qt.TextFlag.TextWordWrap), self.body).height()
            body = QRect(left, title.bottom() + 6, inner, h)
        note = QRect(left, body.bottom(), inner, 0)
        if self.note:
            h = bm.boundingRect(QRect(0, 0, inner, 2000), int(Qt.TextFlag.TextWordWrap), self.note).height()
            note = QRect(left, body.bottom() + 4, inner, h)
        return title, body, note

    def _fit(self) -> None:
        tm, bm = QFontMetrics(self._title_font), QFontMetrics(self.font())
        want = max([tm.horizontalAdvance(self.title)]
                   + [bm.horizontalAdvance(line) for line in (self.body + "\n" + self.note).splitlines()])
        width = min(560, max(280, want + 2 * self.PAD + self.DOT + self.GAP + 4))
        title, body, note = self._text_rects(width)
        bottom = note.bottom() if self.note else body.bottom() if self.body else title.bottom()
        height = bottom + self.PAD + (12 if self.kind == "listen" else 0)
        self.resize(width, height)
        screen = QGuiApplication.screenAt(QCursor.pos()) or QGuiApplication.primaryScreen()
        geo = screen.availableGeometry()
        self.move(geo.x() + (geo.width() - width) // 2, geo.bottom() - height - int(geo.height() * 0.12))

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        c = theme.palette()
        bg = QColor("#2A1F1B")
        bg.setAlphaF(0.94)
        p.setPen(QColor(c.border if not c.dark else "#574036"))
        p.setBrush(bg)
        p.drawRoundedRect(self.rect().adjusted(0, 0, -1, -1), 16, 16)
        title, body, note = self._text_rects(self.width())
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(self.COLORS.get(self.kind, "#D9B27C")))
        p.drawEllipse(self.PAD, title.center().y() - self.DOT // 2, self.DOT, self.DOT)
        p.setPen(QColor("#F5E9DA"))
        p.setFont(self._title_font)
        p.drawText(title, int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter), self.title)
        if self.body:
            p.setFont(self.font())
            p.setPen(QColor("#E3D2BE"))
            p.drawText(body, int(Qt.TextFlag.TextWordWrap), self.body)
        if self.note:
            p.setFont(self.font())
            p.setPen(QColor("#A8927E"))
            p.drawText(note, int(Qt.TextFlag.TextWordWrap), self.note)
        if self.kind == "listen":
            # уровень звука: видно, что микрофон слышит
            bar = QRect(title.left(), self.height() - self.PAD - 4, title.width(), 4)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(255, 255, 255, 40))
            p.drawRoundedRect(bar, 2, 2)
            p.setBrush(QColor(self.COLORS["listen"]))
            p.drawRoundedRect(QRect(bar.left(), bar.top(), int(bar.width() * min(1.0, self.level)), 4), 2, 2)


class VoiceController(QObject):
    """Запись → распознавание → перевод → буфер обмена. Один на всё окно."""

    changed = Signal()              # состояние поменялось (карточка обновляет кнопку «Проверить»)
    finished = Signal(object)       # voice.Phrase
    failed = Signal(str)
    _done = Signal(object)          # из фонового потока — в главный
    _error = Signal(str)

    def __init__(self, cfg, parent=None):
        super().__init__(parent)
        self.cfg = cfg
        self.recognizer = voice.Recognizer()
        self.state = "idle"         # idle | listening | busy
        self.recorder: voice.Recorder | None = None
        self.endpoint: voice.Endpoint | None = None
        self.hold: bool | None = None
        self.combo = ""             # горячая клавиша, которой начали запись (для «рации»)
        self._started = 0.0
        self._toast: VoiceToast | None = None
        self.last: voice.Phrase | None = None
        self.timer = QTimer(self)
        self.timer.setInterval(40)
        self.timer.timeout.connect(self._tick)
        self._done.connect(self._on_done, Qt.ConnectionType.QueuedConnection)
        self._error.connect(self._on_error, Qt.ConnectionType.QueuedConnection)

    @property
    def toast(self) -> VoiceToast:
        if self._toast is None:
            self._toast = VoiceToast()
        return self._toast

    def _beep(self, kind: str) -> None:
        if self.cfg.get("voice_sounds", True):
            voice.beep(kind)

    def preload(self) -> None:
        """Функция включена и модель скачана — загрузить её в фоне заранее."""
        name = self.cfg.get("voice_model", "base")
        if self.cfg.get("voice_enabled") and voice.model_ready(name):
            threading.Thread(target=self.recognizer.preload, args=(name,), daemon=True, name="voice-load").start()

    # ------------------------------------------------------------ запись
    def press(self, combo: str = "") -> None:
        """Горячая клавиша (combo) или кнопка «Проверить» (без combo): начать или закончить фразу."""
        if self.state == "listening":
            self._stop()
            return
        if self.state == "busy":
            self.toast.show_state("busy", tr("Ещё распознаю прошлую фразу…"), seconds=2)
            return
        if not voice.model_ready(self.cfg.get("voice_model", "base")):
            self._on_error(tr("Модель распознавания не скачана — скачайте её в карточке «Голос в чат»."))
            return
        rec = voice.Recorder(self.cfg.get("voice_mic", ""))
        try:
            rec.start()
        except voice.VoiceError as e:
            self._on_error(str(e))
            return
        self.recorder, self.endpoint = rec, voice.Endpoint()
        self.combo = combo
        self.hold = None if combo else False     # кнопкой в окне — только «нажал один раз»
        self._started = time.monotonic()
        self.state = "listening"
        self._beep("start")
        self.toast.show_state("listen", tr("Слушаю…"), self._listen_hint())
        self.timer.start()
        self.changed.emit()

    def _listen_hint(self) -> str:
        if self.hold is None:
            return tr("Говорите…")
        if self.hold is False:
            return tr("Говорите. Запись остановится сама, когда вы замолчите.")
        return tr("Говорите. Отпустите клавишу, когда закончите.")

    def _tick(self) -> None:
        if self.recorder is None or self.endpoint is None:
            return
        elapsed = time.monotonic() - self._started
        state = self.endpoint.feed(self.recorder.take())
        if self._toast is not None:
            self._toast.set_level(self.endpoint.level)
        if self.hold is None and elapsed >= HOLD_AFTER:
            self.hold = bool(key_down(self.combo))
            self.toast.show_state("listen", tr("Слушаю…"), self._listen_hint())
        if self.hold:
            if not key_down(self.combo) or elapsed >= voice.HOLD_LIMIT:
                self._stop()
        elif self.hold is False and state in ("done", "silent"):
            self._stop()

    def _stop(self) -> None:
        self.timer.stop()
        audio = self.recorder.stop() if self.recorder is not None else None
        self.recorder, self.endpoint = None, None
        self.state = "busy"
        self.toast.show_state("busy", tr("Распознаю…"))
        self.changed.emit()
        threading.Thread(target=self._work, args=(audio,), daemon=True, name="voice").start()

    def _work(self, audio) -> None:
        try:
            self._done.emit(voice.process(audio, self.cfg, self.recognizer))
        except voice.VoiceError as e:
            self._error.emit(str(e))
        except Exception as e:  # noqa: BLE001
            self._error.emit(tr("Не удалось распознать: {0}", e))

    # ------------------------------------------------------------ итог
    def _on_done(self, phrase) -> None:
        self.state = "idle"
        self.last = phrase
        QApplication.clipboard().setText(phrase.text)
        self._beep("done")
        self.toast.show_state("done", tr("В буфере — вставьте в чат: Ctrl+V"), phrase.text, seconds=5,
                              note=phrase.heard if phrase.translated else "")
        self.changed.emit()
        self.finished.emit(phrase)

    def _on_error(self, message: str) -> None:
        self.state = "idle"
        self._beep("error")
        self.toast.show_state("error", tr("Не получилось"), message, seconds=5)
        self.changed.emit()
        self.failed.emit(message)

    def cancel(self) -> None:
        self.timer.stop()
        if self.recorder is not None:
            self.recorder.stop()
        self.recorder, self.endpoint = None, None
        if self.state == "listening":
            self.state = "idle"
        if self._toast is not None:
            self._toast.hide()
        self.changed.emit()

    def shutdown(self) -> None:
        self.cancel()
        self.recognizer.unload()
        if self._toast is not None:
            self._toast.close()
            self._toast = None


class ModelDownload(QObject):
    """Скачивание модели распознавания в фоне: progress(скачано, всего), done(), failed(текст)."""

    progress = Signal(int, int)
    done = Signal()
    failed = Signal(str)

    def __init__(self, name: str, parent=None):
        super().__init__(parent)
        self.name = name
        self._cancel = False
        self.running = False

    def start(self) -> None:
        self.running = True
        threading.Thread(target=self._run, daemon=True, name="voice-model").start()

    def cancel(self) -> None:
        self._cancel = True

    def _run(self) -> None:
        try:
            voice.download_model(self.name, lambda a, b: self.progress.emit(a, b), lambda: self._cancel)
        except voice.VoiceError as e:
            self.running = False
            self.failed.emit(str(e))
            return
        self.running = False
        self.done.emit()
