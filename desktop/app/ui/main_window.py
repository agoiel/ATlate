"""Главное окно ПК-версии — в стиле Android-приложения.

Шапка со статусом и режимом «Скриншот | Live», быстрые действия и карточки настроек.
При узком окне карточки встают в одну колонку, кнопки в шапке переносятся.
"""

from __future__ import annotations

import html
import sys
import threading
import time
import traceback

import numpy as np
from PIL import Image
from PySide6.QtCore import QObject, QRect, QRunnable, QSize, Qt, QThreadPool, QTimer, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QFont, QIcon, QImage, QPainter, QPixmap
from PySide6.QtWidgets import (QApplication, QDialog, QFileDialog, QFrame, QGridLayout, QHBoxLayout,
                               QInputDialog, QLabel, QMenu, QMessageBox, QPlainTextEdit, QScrollArea,
                               QSizePolicy, QTextBrowser, QVBoxLayout, QWidget)

from .. import __version__, i18n, languages, titles, voice
from ..capture import Region, find_screen, grab_region, grab_region_raw, gray_thumb, qimage_to_pil
from ..config import DATA_ROOT, GEMINI_MODEL, GLOSSARY_DIR, ROOT, Config
from ..glossary import Glossary, list_glossaries, universal_for
from ..hotkeys import Hotkeys, pretty
from ..i18n import tr
from ..models import PageResult
from ..ocr import easyocr_available
from ..pipeline import Pipeline
from . import theme
from .glossary_editor import GlossaryEditor, new_glossary_file
from .list_window import ListWindow
from .mini import MiniPanel
from .voice_chat import ModelDownload, VoiceController
from .overlay import OverlayWindow, paint_plates
from .selector import SelectionWindow
from .widgets import (Card, Columns, Dropdown, Field, FlowLayout, Hero, ResponsiveRow, Segmented, StatusChip,
                      ScrollContent, Select, Stepper, SwitchRow, Tile, caption, label, pill, relayout, round_pixmap,
                      set_pill)

# Описание переводчиков для списка (как в Android-версии)
TRANSLATOR_INFO = [
    ("google_free", "Google", tr("Без ключа · среднее качество"), tr("Бесплатно, без ключа")),
    ("yandex", tr("Яндекс"), tr("Без ключа · хорошо знает русский"), tr("Бесплатно, без ключа")),
    ("duckduckgo", "DuckDuckGo", tr("Без ключа · переводит через Bing"), tr("Бесплатно, без ключа")),
    ("lingva", "Lingva", tr("Без ключа · Google через посредника, выручает при VPN"), tr("Бесплатно, без ключа")),
    ("deepl", "DeepL", tr("Ключ · 500 тыс. символов в месяц бесплатно"), tr("С ключом")),
    ("microsoft", "Microsoft", tr("Ключ · 2 млн символов в месяц бесплатно"), tr("С ключом")),
    ("papago", "Papago", tr("Ключ Naver Cloud · лучший для корейского"), tr("С ключом")),
    ("gemini", "Gemini", tr("ИИ · есть бесплатный лимит"), tr("ИИ — понимают контекст")),
    ("claude", "Claude", tr("ИИ · естественный живой перевод"), tr("ИИ — понимают контекст")),
    ("openai", tr("OpenAI-совместимый"), tr("ИИ · OpenAI, OpenRouter, DeepSeek, Ollama, LM Studio"), tr("ИИ — понимают контекст")),
]


# ================================================================ фоновая работа
class _Signals(QObject):
    done = Signal(object, float, object)    # PageResult, k, токен задачи
    failed = Signal(str)


class _Job(QRunnable):
    """Распознать и перевести снимок в фоне.

    Сигналы у всех задач общие (объект живёт в главном окне), а сама задача удаляется, как только
    отработала. Раньше у каждой задачи были свои сигналы, удалять её было нельзя — и Qt держал
    в памяти все задачи вместе с их снимками: память росла с каждым переводом.
    """

    def __init__(self, signals: _Signals, pipeline: Pipeline, image: Image.Image, k: float, token,
                 live: bool = False):
        super().__init__()
        self.pipeline, self.image, self.k, self.token, self.live = pipeline, image, k, token, live
        self.signals = signals

    def run(self):
        try:
            res = self.pipeline.process(self.image, live=self.live)
            self.signals.done.emit(res, self.k, self.token)
        except Exception:  # noqa: BLE001
            self.signals.failed.emit(traceback.format_exc())


class _CompareSignals(QObject):
    done = Signal(str)


class _CompareJob(QRunnable):
    """Распознать зону по очереди и параллельно — для режима разработчика."""

    def __init__(self, signals: _CompareSignals, pipeline: Pipeline, image: Image.Image):
        super().__init__()
        self.pipeline, self.image = pipeline, image
        self.signals = signals      # общие, как у _Job

    def run(self):
        try:
            text = self.pipeline.compare(self.image)
        except Exception:  # noqa: BLE001
            text = tr("Ошибка сравнения:\n") + traceback.format_exc()
        self.signals.done.emit(text)


DEV_LOG = DATA_ROOT / "dev_log.txt"


def dev_log(text: str) -> None:
    """Дописать запись в журнал режима разработчика (хранятся последние ~60 КБ)."""
    try:
        old = DEV_LOG.read_text(encoding="utf-8") if DEV_LOG.exists() else ""
        stamp = time.strftime("%d.%m %H:%M:%S")
        all_ = f"{old}── {stamp}\n{text}\n\n"
        if len(all_) > 60_000:
            all_ = "── " + all_[-60_000:].split("── ", 1)[-1]
        DEV_LOG.write_text(all_, encoding="utf-8")
    except OSError:
        pass


class _HotkeyBridge(QObject):
    fired = Signal(str)


class _InstallBridge(QObject):
    finished = Signal(object, object)


# ================================================================ плитки
class _TileGrid(QWidget):
    """Плитки быстрых действий: 4 в ряд, а в узком окне — по 2."""

    def __init__(self, tiles: list[Tile]):
        super().__init__()
        self.tiles = tiles
        self.grid = QGridLayout(self)
        self.grid.setContentsMargins(0, 0, 0, 0)
        self.grid.setSpacing(10)
        self._cols = 0
        self._place(4)

    def _place(self, cols: int) -> None:
        if cols == self._cols:
            return
        self._cols = cols
        for t in self.tiles:
            self.grid.removeWidget(t)
        for i, t in enumerate(self.tiles):
            self.grid.addWidget(t, i // cols, i % cols)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        cols = 2 if e.size().width() < 560 else 4
        if cols != self._cols:
            QTimer.singleShot(0, lambda: (self._place(cols), relayout(self)))


# ================================================================ главное окно
class MainWindow(QWidget):
    # настройки не сохранились (диск полон, файл занят…) — сообщение из любого потока в окно
    save_failed = Signal(str)
    # проверка обновлений закончилась (из фонового потока): (версия, ссылка) или None
    update_checked = Signal(object)

    def __init__(self, cfg: Config):
        super().__init__(None, Qt.WindowType.Window)
        self.cfg = cfg
        self.save_failed.connect(self._on_save_failed)
        cfg.on_save_error = self.save_failed.emit
        self.update_checked.connect(self._on_update_checked)
        self.shown_result: PageResult | None = None     # последний перевод с экрана (его копирует «Копировать перевод»)
        # вышедшая новая версия (номер, ссылка) — запомнена с прошлой проверки, плашка видна и после перезапуска
        from .. import updates
        found = cfg.get("update_found")
        self._update = updates.offer(cfg, tuple(found)) if cfg.get("update_check", True) and found else None
        if cfg.get("ocr_engine") == "easyocr" and not easyocr_available():
            cfg["ocr_engine"] = "windows"       # EasyOCR выбран раньше, но его нет — переводить было бы нечем
            cfg.save()
        self.pipeline = Pipeline(cfg)
        self.region: Region | None = Region.from_dict(cfg.get("last_region"))
        self.overlay = OverlayWindow(cfg)
        self.list_window = ListWindow()
        self.selector: SelectionWindow | None = None
        self.pool = QThreadPool.globalInstance()
        self.busy = False
        self._token = 0
        self._last_thumb: np.ndarray | None = None
        self._dirty = False
        self._hotkeys = None
        # сигналы фоновых задач — одни на всё окно (см. _Job)
        self.job_signals = _Signals(self)
        self.job_signals.done.connect(self._handle_done)
        self.job_signals.failed.connect(self._handle_failed)
        self.compare_signals = _CompareSignals(self)
        self.compare_signals.done.connect(self._on_compared)
        self.live_on = False
        self._file_image: Image.Image | None = None
        self.mini: MiniPanel | None = None      # панель компактного режима (создаётся при первом входе)
        # «Голос в чат»: фраза в микрофон → перевод в буфер обмена
        self.voice = VoiceController(cfg, self)
        self.voice.changed.connect(self._refresh_voice)
        self.voice.finished.connect(self._on_voice_done)
        self.voice.failed.connect(self._on_voice_failed)
        self._voice_dl: ModelDownload | None = None
        self._voice_pct = 0
        self.voice_tor_note = self.voice_model_note = self.voice_dl_btn = self.voice_gloss_combo = None
        self.voice_gloss_note = self.voice_recent = None

        self.setWindowTitle(f"ATlate {__version__}")
        self.setObjectName("root")
        self.setMinimumSize(360, 420)
        geo = cfg.get("window_size")
        self.resize(*(geo if isinstance(geo, list) and len(geo) == 2 else (1000, 820)))
        self._apply_on_top()

        # первый запуск: универсальный глоссарий для языка оригинала
        if not cfg.get("glossary") and not cfg.get("active_title"):
            cfg["glossary"] = universal_for(GLOSSARY_DIR, cfg["source_lang"])
            cfg.save()

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        outer.addWidget(self.scroll)
        self._build()

        self.auto_timer = QTimer(self)
        self.auto_timer.timeout.connect(self._auto_tick)
        self.zone_timer = QTimer(self)
        self.zone_timer.setSingleShot(True)
        self.zone_timer.timeout.connect(self._hide_zone)

        self.bridge = _HotkeyBridge()
        self.bridge.fired.connect(self._on_hotkey)
        self._start_hotkeys()

        self._installing = False
        self._install_declined: set[str] = set()
        self.install_bridge = _InstallBridge()
        self.install_bridge.finished.connect(self._on_install_finished)

        if self.region:
            self.overlay.place(self.region)
        self._refresh_status()
        QTimer.singleShot(500, self.check_ocr_language)
        QTimer.singleShot(4000, self.check_updates)
        QTimer.singleShot(6000, self.voice.preload)

    # ================================================================ интерфейс
    def _build(self) -> None:
        """Собрать содержимое окна (заново — при смене темы)."""
        content = ScrollContent()
        content.setObjectName("content")
        # окно прокрутки отдаёт содержимому его полную высоту, а не минимальную — иначе тексты наезжают
        content.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Minimum)
        v = QVBoxLayout(content)
        v.setContentsMargins(0, 0, 0, 20)
        v.setSpacing(0)
        v.addWidget(self._build_hero())

        body = QWidget()
        bl = QVBoxLayout(body)
        bl.setContentsMargins(16, 14, 16, 0)
        bl.setSpacing(14)
        if self._update is not None:
            bl.addWidget(self._build_update_banner())
        # две страницы: экранный переводчик и «Голос в чат» — переключаются вкладками в шапке
        self.screen_page = QWidget()
        self.screen_page.setObjectName("transparent")
        sp = QVBoxLayout(self.screen_page)
        sp.setContentsMargins(0, 0, 0, 0)
        sp.setSpacing(14)
        sp.addWidget(self._build_tiles())
        sp.addWidget(self._build_studio_banner())
        self.columns = Columns([self._build_languages(), self._build_translator(), self._build_title(),
                                self._build_tuning(), self._build_hotkeys(), self._build_developer()])
        sp.addWidget(self.columns)
        bl.addWidget(self.screen_page)
        self.voice_page = self._build_voice_page()
        bl.addWidget(self.voice_page)
        foot = caption(tr("ATlate {0} · распознавание: {1}",
                          __version__, 'Windows OCR' if self.cfg['ocr_engine'] == 'windows' else 'EasyOCR'))
        foot.setAlignment(Qt.AlignmentFlag.AlignCenter)
        bl.addWidget(foot)
        v.addWidget(body)
        v.addStretch(1)
        old = self.scroll.takeWidget()
        self.scroll.setWidget(content)
        if old is not None:
            old.deleteLater()
        self._show_page()

    # ---------------------------------------------------------------- шапка
    def _build_hero(self) -> QWidget:
        hero = Hero()
        lay = QVBoxLayout(hero)
        lay.setContentsMargins(22, 20, 22, 22)
        lay.setSpacing(12)

        brand = QHBoxLayout()
        brand.setSpacing(14)
        logo = QLabel()
        logo.setFixedSize(52, 52)
        logo.setPixmap(round_pixmap(str(theme.resource("icon.png")), 52))
        logo.setStyleSheet("background: transparent;")
        brand.addWidget(logo)
        names = QVBoxLayout()
        names.setSpacing(0)
        t = QLabel("ATlate")
        t.setStyleSheet("color: white; font-size: 20pt; font-weight: 700; background: transparent;")
        sub = QLabel(tr("Перевод манхвы прямо с экрана"))
        sub.setStyleSheet(f"color: {theme.CREAM}; background: transparent;")
        sub.setWordWrap(True)
        names.addWidget(t)
        names.addWidget(sub)
        brand.addLayout(names, 1)
        self.pin = pill("", "heroOutline", "pin", tr("Окно поверх всех окон"))
        self.pin.setCheckable(True)
        self.pin.setChecked(bool(self.cfg.get("always_on_top", False)))
        self.pin.setFixedSize(QSize(44, 40))
        self.pin.toggled.connect(self._toggle_on_top)
        self.btn_mini = pill("", "heroOutline", "compact",
                             tr("Компактный режим: вместо окна — маленькая панель поверх экрана"))
        self.btn_mini.setFixedSize(QSize(44, 40))
        self.btn_mini.clicked.connect(self.enter_mini)
        brand.addWidget(self.btn_mini, 0, Qt.AlignmentFlag.AlignTop)
        brand.addWidget(self.pin, 0, Qt.AlignmentFlag.AlignTop)
        lay.addLayout(brand)

        self.chip = StatusChip()
        lay.addWidget(self.chip, 0, Qt.AlignmentFlag.AlignLeft)

        self.mode_switch = Segmented([("shot", tr("Скриншот")), ("live", "Live"), ("voice", tr("Голос"))],
                                     self._switch_key())
        self.mode_switch.setMaximumWidth(480)
        self.mode_switch.changed.connect(self._on_mode_switch)
        lay.addWidget(self.mode_switch)

        flow_host = QWidget()
        flow_host.setObjectName("transparent")
        flow = FlowLayout(10)
        flow_host.setLayout(flow)
        hk = self.cfg["hotkeys"]
        self.btn_main = pill("", "hero", "play")
        self.btn_main.clicked.connect(self._main_action)
        self.btn_select = pill(tr("Выделить область"), "heroOutline", "select", tr("Горячая клавиша: {0}",
                                                                                   hk.get('select', '')))
        self.btn_select.clicked.connect(self.start_selection)
        self.btn_zone = pill(tr("Показать зону"), "heroOutline", "eye", tr("Показать рамку текущей зоны перевода"))
        self.btn_zone.clicked.connect(self.show_zone)
        self.btn_hide = pill(tr("Скрыть перевод"), "heroOutline", "hide", tr("Горячая клавиша: {0}",
                                                                             hk.get('toggle', '')))
        self.btn_hide.clicked.connect(self.toggle_overlay)
        for b in (self.btn_main, self.btn_select, self.btn_zone, self.btn_hide):
            flow.addWidget(b)
        lay.addWidget(flow_host)
        self.screen_actions = flow_host

        # вкладка «Голос»: сказать фразу, включить клавишу, скопировать прошлый перевод ещё раз
        voice_host = QWidget()
        voice_host.setObjectName("transparent")
        vflow = FlowLayout(10)
        voice_host.setLayout(vflow)
        self.btn_say = pill(tr("Сказать фразу"), "hero", "mic", tr("Скажите фразу — перевод окажется в буфере обмена"))
        self.btn_say.clicked.connect(lambda: self.voice.press(""))
        self.btn_voice_on = pill("", "heroOutline", "power")
        self.btn_voice_on.clicked.connect(self._toggle_voice)
        self.btn_voice_again = pill(tr("Скопировать снова"), "heroOutline", "copy",
                                    tr("Положить последний перевод в буфер обмена ещё раз"))
        self.btn_voice_again.clicked.connect(self._copy_voice_again)
        for b in (self.btn_say, self.btn_voice_on, self.btn_voice_again):
            vflow.addWidget(b)
        lay.addWidget(voice_host)
        self.voice_actions = voice_host

        self.hint = QLabel()
        self.hint.setWordWrap(True)
        self.hint.setStyleSheet(f"color: {theme.CREAM}; background: transparent; font-size: 9pt;")
        lay.addWidget(self.hint)
        self.status = QLabel("")
        self.status.setWordWrap(True)
        self.status.setStyleSheet("background: transparent;")
        lay.addWidget(self.status)
        return hero

    # ---------------------------------------------------------------- плитки
    def _build_tiles(self) -> QWidget:
        tiles = [
            Tile(tr("Перевести\nкартинку"), "image", tr("Перевести сохранённый скриншот или страницу")),
            Tile(tr("Новый\nтайтл"), "refresh", tr("Создать тайтл со своим контекстом и глоссарием")),
            Tile(tr("Окно\nсо списком"), "list", tr("Оригинал и перевод каждой реплики списком")),
            Tile(tr("Как\nпользоваться"), "help", tr("Короткая инструкция")),
        ]
        tiles[0].clicked.connect(self.translate_file)
        tiles[1].clicked.connect(self.create_title)
        tiles[2].clicked.connect(self._show_list)
        tiles[3].clicked.connect(self.show_help)
        return _TileGrid(tiles)

    def _build_studio_banner(self) -> QWidget:
        """Вход в Студию: перевод главы целиком, с клином и тайпом."""
        box = QFrame()
        box.setObjectName("studioPromo")
        box.setCursor(Qt.CursorShape.PointingHandCursor)
        lay = QHBoxLayout(box)
        lay.setContentsMargins(16, 12, 14, 12)
        lay.setSpacing(12)
        ic = QLabel()
        ic.setObjectName("badge")
        ic.setFixedSize(40, 40)
        ic.setAlignment(Qt.AlignmentFlag.AlignCenter)
        ic.setPixmap(theme.pixmap("brush", theme.palette().primary, 22))
        lay.addWidget(ic)
        col = QVBoxLayout()
        col.setSpacing(1)
        col.addWidget(label(tr("Студия перевода"), "cardTitle"))
        col.addWidget(caption(tr("Перевести главу целиком: найти текст, стереть оригинал, вписать перевод и выгрузить "
                                 "готовые страницы")))
        lay.addLayout(col, 1)
        btn = pill(tr("Открыть"), "primary", "brush")
        btn.clicked.connect(self.open_studio)
        lay.addWidget(btn)
        return box

    # ---------------------------------------------------------------- обновления
    def _build_update_banner(self) -> QWidget:
        """Плашка «вышла новая версия» — только когда она действительно вышла."""
        version, url = self._update
        box = QFrame()
        box.setObjectName("updateBanner")
        lay = QHBoxLayout(box)
        lay.setContentsMargins(16, 10, 12, 10)
        lay.setSpacing(12)
        ic = QLabel()
        ic.setObjectName("badge")
        ic.setFixedSize(36, 36)
        ic.setAlignment(Qt.AlignmentFlag.AlignCenter)
        ic.setPixmap(theme.pixmap("download", theme.palette().primary, 20))
        lay.addWidget(ic)
        col = QVBoxLayout()
        col.setSpacing(1)
        col.addWidget(label(tr("Вышла версия {0}", version), "cardTitle"))
        col.addWidget(caption(tr("Сейчас у вас {0}. Что нового — на странице релиза.", __version__)))
        lay.addLayout(col, 1)
        later = pill(tr("Позже"), "link")
        later.setToolTip(tr("Не напоминать об этой версии"))
        later.clicked.connect(self._skip_update)
        lay.addWidget(later)
        get = pill(tr("Скачать"), "primary", "download")
        get.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(url)))
        lay.addWidget(get)
        return box

    def check_updates(self, force: bool = False) -> None:
        """Раз в день спросить у GitHub, не вышла ли новая версия (в фоне, окно не ждёт)."""
        from .. import updates
        if not force and not updates.due(self.cfg):
            return
        threading.Thread(target=lambda: self.update_checked.emit(updates.fetch()), daemon=True,
                         name="update-check").start()

    def _on_update_checked(self, found) -> None:
        from .. import updates
        if found is None:                     # не удалось — попробуем при следующем запуске
            return
        self.cfg["update_checked_at"] = time.time()
        self.cfg["update_found"] = list(found)
        self.cfg.save()
        offer = updates.offer(self.cfg, found)
        if offer != self._update:
            self._update = offer
            self._rebuild()

    def _skip_update(self) -> None:
        if self._update is not None:
            self.cfg["update_skip"] = self._update[0]
            self.cfg.save()
        self._update = None
        self._rebuild()

    def _set_update_check(self, on: bool) -> None:
        self.cfg["update_check"] = on
        self.cfg.save()
        if on:
            self.check_updates(force=True)
        elif self._update is not None:
            self._update = None
            self._rebuild()

    def open_studio(self) -> None:
        from ..studio.window import StudioWindow
        if getattr(self, "studio", None) is None:
            self.studio = StudioWindow(self.cfg)
            self.studio.settings_changed.connect(self._on_studio_settings)
        self.studio.show()
        self.studio.raise_()
        self.studio.activateWindow()

    def _on_studio_settings(self) -> None:
        self.pipeline.reload()
        self._rebuild()

    # ---------------------------------------------------------------- языки
    def _combo(self, items: list[tuple[str, str]], current: str, on_change) -> Select:
        cb = Select()
        for key, title in items:
            cb.addItem(title, key)
        cb.setCurrentIndex(max(0, cb.findData(current)))
        cb.currentIndexChanged.connect(lambda _i: on_change(cb.currentData()))
        return cb

    def _labeled(self, caption_text: str, w: QWidget) -> QWidget:
        box = QWidget()
        lay = QVBoxLayout(box)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(4)
        lay.addWidget(label(caption_text.upper(), "fieldLabel"))
        lay.addWidget(w)
        lay.addStretch(1)
        return box

    def _build_languages(self) -> Card:
        card = Card(tr("Языки"), tr("Откуда и куда переводить"), "lang")
        auto = bool(self.cfg.get("auto_lang"))
        self.src_combo = self._combo([("auto", self._auto_title())]
                                     + [(k, v["name"]) for k, v in languages.SOURCE_LANGUAGES.items()],
                                     "auto" if auto else self.cfg["source_lang"], self._set_source)
        self.src_combo.setToolTip(tr("«Авто» — программа сама поймёт, на каком языке текст в выделенной области"))
        self.tgt_combo = self._combo([(k, v["name"]) for k, v in languages.TARGET_LANGUAGES.items()],
                                     self.cfg["target_lang"], self._set_target)
        arrow = QLabel()
        arrow.setPixmap(theme.pixmap("arrow", theme.palette().primary, 20))
        card.add(ResponsiveRow(self._labeled(tr("С языка"), self.src_combo), arrow,
                               self._labeled(tr("На язык"), self.tgt_combo), anchor=self.src_combo.header))
        content = Dropdown(tr("Что переводим"), [
            ("manhwa", tr("Манхва, манга, маньхуа"), tr("Облака с репликами, звуки, плашки"), ""),
            ("screen", tr("Текст на экране"), tr("Статьи, чаты, игры, приложения — перевод прямо поверх текста"), ""),
        ], self.cfg.get("content_mode", "manhwa"))
        content.changed.connect(lambda v: self._set("content_mode", v))
        card.add(content)
        self.auto_hint = caption("")
        card.add(self.auto_hint)
        self._refresh_auto_hint()
        return card

    def _auto_title(self) -> str:
        """Подпись пункта «Авто»: вместе с языком, который сейчас найден."""
        name = languages.src(self.cfg["source_lang"])["name"].split(" (")[0]
        return tr("Авто · сейчас {0}",
                  i18n.lower(name)) if self.cfg.get("auto_lang") else tr("Авто — определить по экрану")

    def _refresh_auto_hint(self) -> None:
        """Под списком языков: из чего «Авто» выбирает (в Windows — только из установленных языков)."""
        text = ""
        if self.cfg.get("auto_lang"):
            text = tr("Язык оригинала определяется сам при переводе.")
            if sys.platform == "win32" and self.cfg["ocr_engine"] == "windows":
                try:
                    from ..win_setup import missing_ocr_languages
                    missing = missing_ocr_languages(list(languages.SOURCE_LANGUAGES))
                except Exception:  # noqa: BLE001
                    missing = []
                have = [i18n.lower(v["name"].split(" (")[0]) for k, v in languages.SOURCE_LANGUAGES.items()
                        if k not in missing]
                if missing:
                    text += tr(" Выбор — из языков, для которых в Windows установлено распознавание: {0}. Чтобы "
                               "добавить язык, выберите его в списке один раз — программа предложит его докачать.",
                               ", ".join(have) or tr("пока ни одного"))
        self.auto_hint.setText(text)
        self.auto_hint.setVisible(bool(text))

    # ---------------------------------------------------------------- переводчик
    def _build_translator(self) -> Card:
        card = Card(tr("Переводчик"), tr("Чем переводить текст"), "bolt")
        dd = Dropdown(tr("Переводчик"), TRANSLATOR_INFO, self.cfg["translator"])
        dd.changed.connect(self._set_translator)
        card.add(dd)
        self.keys_box = QWidget()
        self.keys_lay = QVBoxLayout(self.keys_box)
        self.keys_lay.setContentsMargins(0, 0, 0, 0)
        self.keys_lay.setSpacing(8)
        card.add(self.keys_box)
        self._render_keys()
        ctx = Stepper(tr("Контекст"), tr("Сколько прошлых реплик учитывать"), float(self.cfg["context_size"]),
                      1, 0, 40, lambda v: str(int(v)))
        ctx.changed.connect(lambda v: self._set("context_size", int(v)))
        card.add(ctx)
        pause = Stepper(tr("Пауза между запросами"), tr("Защищает от блокировки за частые запросы"),
                        float(self.cfg.get("request_interval", 1.0)), 0.5, 0, 10, lambda v: tr("{0:.1f} с", v))
        pause.changed.connect(lambda v: self._set("request_interval", v))
        card.add(pause)
        fb = SwitchRow(tr("Запасные бесплатные переводчики"),
                       tr("Если бесплатный переводчик не отвечает, перевести другим бесплатным"),
                       bool(self.cfg.get("free_fallback", True)))
        fb.toggled.connect(lambda v: self._set("free_fallback", v))
        card.add(fb)
        return card

    def _render_keys(self) -> None:
        while self.keys_lay.count():
            it = self.keys_lay.takeAt(0)
            w = it.widget() if it is not None else None
            if w is not None:
                # сразу убрать из окна, иначе старая подсказка на миг рисуется поверх новых полей
                w.hide()
                w.setParent(None)
                w.deleteLater()
        name = self.cfg["translator"]
        t = self.cfg["translators"].setdefault(name, {})

        def note(text: str) -> None:
            self.keys_lay.addWidget(caption(text))

        def field(cap: str, key: str, placeholder: str = "", secret: bool = False, default: str = "") -> None:
            f = Field(cap, t.get(key, default), placeholder, secret)
            f.edited.connect(lambda v, k=key, d=default: self._set_key(name, k, v or d))
            self.keys_lay.addWidget(f)

        def link(text: str, url: str) -> None:
            b = pill(text, "link", "link")
            b.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(url)))
            self.keys_lay.addWidget(b, 0, Qt.AlignmentFlag.AlignLeft)

        if name == "google_free":
            note(tr("Ключ не нужен. Это неофициальный способ: при VPN Google часто ограничивает запросы — "
                    "тогда выберите Lingva или Яндекс."))
        elif name == "yandex":
            note(tr("Работает без ключа. Если бесплатный способ перестанет отвечать, укажите ключ Yandex Cloud — "
                    "тогда перевод пойдёт через официальный API (платно, по символам)."))
            field(tr("API-ключ Yandex Cloud (необязательно)"), "api_key", "AQVN…", secret=True)
            field(tr("ID каталога (нужен вместе с ключом)"), "folder_id", "b1g…")
            link(tr("Как получить ключ"), "https://yandex.cloud/ru/docs/translate/quickstart")
        elif name == "duckduckgo":
            note(tr("Ключ не нужен. DuckDuckGo переводит через Microsoft Bing. Способ неофициальный "
                    "и может временно не отвечать."))
        elif name == "lingva":
            note(tr("Ключ не нужен. Lingva — открытый посредник к Google Переводчику: Google видит запрос "
                    "от сервера Lingva, а не от вас, поэтому ограничения VPN обычно не мешают."))
            field(tr("Адрес сервера Lingva"), "base_url", "https://lingva.ml", default="https://lingva.ml")
            link(tr("Список других серверов"), "https://github.com/thedaviddelta/lingva-translate#instances")
        elif name == "deepl":
            field(tr("API-ключ DeepL"), "api_key", "xxxxxxxx-xxxx:fx", secret=True)
            link(tr("Получить бесплатный ключ"), "https://www.deepl.com/pro-api")
        elif name == "microsoft":
            field(tr("Ключ Azure Translator"), "api_key", tr("ключ ресурса"), secret=True)
            field(tr("Регион"), "region", "global", default="global")
            link(tr("Открыть портал Azure"), "https://portal.azure.com")
        elif name == "papago":
            note(tr("Переводчик Naver — лучший для корейского. Нужна регистрация в Naver Cloud Platform. "
                    "На русский Papago переводит только с корейского и английского."))
            field("Client ID", "client_id", secret=True)
            field("Client Secret", "client_secret", secret=True)
            link(tr("Открыть Naver Cloud Platform"), "https://www.ncloud.com/product/aiService/papagoTranslation")
        elif name == "gemini":
            field(tr("API-ключ Gemini"), "api_key", "AIza…", secret=True)
            field(tr("Модель"), "model", GEMINI_MODEL, default=GEMINI_MODEL)
            link(tr("Получить ключ в Google AI Studio"), "https://aistudio.google.com/apikey")
        elif name == "claude":
            field(tr("API-ключ Claude"), "api_key", "sk-ant-…", secret=True)
            field(tr("Модель"), "model", "claude-haiku-5-5", default="claude-haiku-5-5")
            link(tr("Получить ключ"), "https://platform.claude.com")
        elif name == "openai":
            note(tr("Подходит для OpenAI, OpenRouter, DeepSeek и бесплатных локальных моделей: "
                    "Ollama (http://localhost:11434/v1) или LM Studio (http://localhost:1234/v1)."))
            field(tr("Адрес сервера"), "base_url", "https://api.openai.com/v1")
            field(tr("API-ключ"), "api_key", "sk-…", secret=True)
            field(tr("Модель"), "model", "gpt-4o-mini")
        relayout(self.keys_box)

    # ---------------------------------------------------------------- тайтл и глоссарий
    def _build_title(self) -> Card:
        card = Card(tr("Тайтл"), tr("Контекст, глоссарий, область и языки — свои у каждой манхвы"), "book")
        self.title_combo = Select()
        self.title_combo.currentIndexChanged.connect(self._on_title_combo)
        card.add(self._labeled(tr("Активный тайтл"), self.title_combo))
        row = FlowLayout(8)
        host = QWidget()
        host.setLayout(row)
        manage = pill(tr("Управление"), "outline")
        manage.clicked.connect(lambda: self._title_menu(manage))
        new_t = pill(tr("Новый тайтл"), "tonal", "plus")
        new_t.clicked.connect(self.create_title)
        row.addWidget(manage)
        row.addWidget(new_t)
        card.add(host)

        self.gloss_combo = Select()
        self.gloss_combo.currentIndexChanged.connect(self._on_gloss_combo)
        card.add(self._labeled(tr("Глоссарий"), self.gloss_combo))
        grow = FlowLayout(8)
        ghost = QWidget()
        ghost.setLayout(grow)
        edit = pill(tr("Изменить"), "outline")
        edit.clicked.connect(self.edit_glossary)
        new_g = pill(tr("Новый"), "tonal", "plus")
        new_g.clicked.connect(self.new_glossary)
        auto_g = pill(tr("Авто-глоссарий"), "tonal", "bolt",
                      tr("Новый глоссарий, который ИИ-переводчик пополняет сам: имена и термины из переводов"))
        auto_g.clicked.connect(self.new_auto_glossary)
        folder = pill(tr("Папка"), "tonal", "folder", tr("Открыть папку с глоссариями"))
        folder.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(GLOSSARY_DIR))))
        for b in (edit, new_g, auto_g, folder):
            grow.addWidget(b)
        card.add(ghost)
        self.auto_gloss = SwitchRow(tr("Пополнять глоссарий сам"),
                                    tr("ИИ-переводчик добавляет новые имена и термины, повторы пропускаются"), False)
        self.auto_gloss.toggled.connect(self._set_glossary_auto)
        card.add(self.auto_gloss)
        self.auto_gloss_hint = caption("")
        card.add(self.auto_gloss_hint)
        self._fill_titles()
        return card

    # ---------------------------------------------------------------- авто-глоссарий
    def _current_glossary(self) -> Glossary | None:
        name = self.cfg.get("glossary") or ""
        if not name:
            return None
        try:
            return Glossary.load(GLOSSARY_DIR / name)
        except (OSError, ValueError):
            return None

    def _refresh_auto_glossary(self) -> None:
        """Переключатель «Пополнять сам» — по текущему глоссарию; подсказка — почему он недоступен."""
        if not hasattr(self, "auto_gloss"):
            return
        name = self.cfg.get("glossary") or ""
        g = self._current_glossary()
        universal = name.startswith("universal_")
        self.auto_gloss.switch.blockSignals(True)
        self.auto_gloss.switch.setChecked(bool(g and g.auto and not universal))
        self.auto_gloss.switch.blockSignals(False)
        self.auto_gloss.setEnabled(g is not None and not universal)
        from ..translators import TRANSLATORS
        llm = getattr(TRANSLATORS.get(self.cfg["translator"]), "uses_llm", False)
        if g is not None and not g.fits(self.cfg["target_lang"]):
            hint = tr("Этот глоссарий — для перевода на {0}, поэтому сейчас он не используется. Выберите другой "
                      "или создайте авто-глоссарий.", i18n.lower(languages.name_of(g.target_lang)))
        elif g is None:
            hint = tr("Выберите или создайте глоссарий — или нажмите «Авто-глоссарий».")
        elif universal:
            hint = tr("Встроенный универсальный глоссарий не пополняется — нажмите «Авто-глоссарий».")
        elif g.auto and not llm:
            hint = (tr("Пополнение работает только с ИИ-переводчиками (Gemini, Claude, OpenAI-совместимые). "
                       "С выбранным переводчиком глоссарий только используется."))
        elif g.auto:
            hint = (tr("Добавлено автоматически: {0}. Такие записи помечены в редакторе — проверьте их и "
                       "исправьте, если ИИ ошибся.", g.auto_count))
        else:
            hint = ""
        self.auto_gloss_hint.setText(hint)
        self.auto_gloss_hint.setVisible(bool(hint))

    def _set_glossary_auto(self, on: bool) -> None:
        g = self._current_glossary()
        if g is None or (self.cfg.get("glossary") or "").startswith("universal_"):
            self._refresh_auto_glossary()
            return
        g.auto = on
        try:
            g.save()
        except OSError as e:
            self._set_status(tr("Не удалось сохранить глоссарий: {0}", e), True)
        self.pipeline.reload()
        self._refresh_auto_glossary()

    def new_auto_glossary(self) -> None:
        """Новый пустой глоссарий, который пополняется сам; сразу выбирается для текущего тайтла."""
        t = titles.load(self.cfg.get("active_title", ""))
        path = new_glossary_file(self, self.cfg["source_lang"], default=t.name if t else "", auto=True,
                                 target_lang=self.cfg["target_lang"])
        if not path:
            return
        self.cfg["glossary"] = path.name
        if t:
            t.glossary = path.name
            titles.save(t)
        self._save_and_reload()
        self._fill_titles()
        from ..translators import TRANSLATORS
        llm = getattr(TRANSLATORS.get(self.cfg["translator"]), "uses_llm", False)
        self._set_status(tr("Авто-глоссарий создан: новые имена и термины будут добавляться при переводе.")
                         if llm else tr("Авто-глоссарий создан. Чтобы он пополнялся, выберите ИИ-переводчик "
                                        "(Gemini, Claude или OpenAI-совместимый)."), notify=True)

    def _fill_titles(self) -> None:
        self._filling = True
        self.title_combo.clear()
        self.title_combo.addItem(tr("Без тайтла — контекст только до выхода"), "")
        for t in titles.list_titles():
            self.title_combo.addItem(tr("{0} · {1} реплик", t.name, len(t.history)), t.id)
        idx = self.title_combo.findData(self.cfg.get("active_title", ""))
        if idx < 0:
            self.cfg["active_title"] = ""
            idx = 0
        self.title_combo.setCurrentIndex(idx)
        t = titles.load(self.cfg.get("active_title", ""))
        if t and t.glossary != self.cfg.get("glossary"):
            self.cfg["glossary"] = t.glossary
        self.gloss_combo.clear()
        self.gloss_combo.addItem(tr("Без глоссария"), "")
        for p in list_glossaries(GLOSSARY_DIR):
            try:
                g = Glossary.load(p)
                mark = tr(" · авто") if g.auto else ""
                self.gloss_combo.addItem(tr("{0} · {1} зап.{2}", g.title, len(g.entries), mark), p.name)
            except (OSError, ValueError):
                continue
        self.gloss_combo.setCurrentIndex(max(0, self.gloss_combo.findData(self.cfg.get("glossary", ""))))
        self._filling = False
        self._refresh_auto_glossary()

    def _on_title_combo(self, _i: int) -> None:
        if getattr(self, "_filling", False):
            return
        tid = self.title_combo.currentData() or ""
        self._switch_title(tid)

    def _switch_title(self, tid: str) -> None:
        """Сменить тайтл: уходящий запоминает свою область, режим и языки, выбранный — восстанавливает свои."""
        old = self.cfg.get("active_title", "")
        if old and old != tid:
            titles.remember(old, self.cfg)
        self.cfg["active_title"] = tid
        self.pipeline.reset_auto()      # у другого тайтла язык может быть другим
        t = titles.load(tid)
        changed = False
        if t:
            self.cfg["glossary"] = t.glossary
            changed = self._apply_profile(t.profile)
        self._save_and_reload()
        if changed:
            # области, режим и языки показаны в разных карточках — проще собрать окно заново
            QTimer.singleShot(0, self._rebuild)
        else:
            self._fill_titles()
        self._refresh_status()

    def _apply_profile(self, profile: dict) -> bool:
        """Восстановить область, режим и языки тайтла. Возвращает, поменялось ли что-нибудь."""
        diff = {k: v for k, v in (profile or {}).items() if self.cfg.get(k) != v}
        if not diff:
            return False
        live = self.live_on
        if live:
            self.set_live(False)
        for k, v in diff.items():
            self.cfg[k] = v
        if "last_region" in diff:
            self.region = Region.from_dict(self.cfg.get("last_region"))
            self.overlay.clear()
            self.shown_result = None
            if self.region:
                self.overlay.place(self.region)
            self._last_thumb = None
        if live and self.cfg.get("translate_mode") == "live" and self.region:
            self.set_live(True)
        self._sync_mini()
        return True

    def _on_gloss_combo(self, _i: int) -> None:
        if getattr(self, "_filling", False):
            return
        self.cfg["glossary"] = self.gloss_combo.currentData() or ""
        t = titles.load(self.cfg.get("active_title", ""))
        if t:
            t.glossary = self.cfg["glossary"]
            titles.save(t)
        self._save_and_reload()
        self._refresh_auto_glossary()

    def _title_menu(self, anchor: QWidget) -> None:
        t = titles.load(self.cfg.get("active_title", ""))
        m = QMenu(self)
        if t is None:
            m.addAction(tr("Создать тайтл"), self.create_title)
            m.addAction(tr("Очистить контекст"), self.reset_session)
        else:
            m.addAction(tr("Переименовать"), self.rename_title)
            m.addAction(tr("Очистить контекст"), self.reset_session)
            m.addSeparator()
            m.addAction(tr("Удалить тайтл"), self.delete_title)
        m.exec(anchor.mapToGlobal(anchor.rect().bottomLeft()))

    def create_title(self) -> None:
        name, ok = QInputDialog.getText(self, tr("Новый тайтл"), tr("Название тайтла:"))
        if not ok or not name.strip():
            return
        old = self.cfg.get("active_title", "")
        if old:
            titles.remember(old, self.cfg)      # прежний тайтл запоминает свою область, режим и языки
        t = titles.create(name, universal_for(GLOSSARY_DIR, self.cfg["source_lang"]))
        self.cfg["active_title"] = t.id
        self.cfg["glossary"] = t.glossary
        self._save_and_reload()
        self._fill_titles()
        self._set_status(tr("Тайтл «{0}» создан. Контекст будет сохраняться между запусками.", t.name))

    def rename_title(self) -> None:
        t = titles.load(self.cfg.get("active_title", ""))
        if not t:
            return
        name, ok = QInputDialog.getText(self, tr("Переименовать"), tr("Название тайтла:"), text=t.name)
        if ok and name.strip():
            t.name = name.strip()
            titles.save(t)
            self._fill_titles()

    def delete_title(self) -> None:
        t = titles.load(self.cfg.get("active_title", ""))
        if not t:
            return
        if QMessageBox.question(self, tr("Удалить тайтл?"),
                                tr("Сохранённые реплики и кэш переводов «{0}» будут удалены. Глоссарий останется.",
                                   t.name)) != QMessageBox.StandardButton.Yes:
            return
        titles.delete(t.id)
        self.cfg["active_title"] = ""
        self._save_and_reload()
        self._fill_titles()

    def edit_glossary(self) -> None:
        name = self.cfg.get("glossary") or ""
        if not name:
            self.new_glossary()
            return
        GlossaryEditor(GLOSSARY_DIR / name, self).exec()
        self._fill_titles()
        self.pipeline.reload()

    def new_glossary(self) -> None:
        path = new_glossary_file(self, self.cfg["source_lang"])
        if path:
            self.cfg["glossary"] = path.name
            t = titles.load(self.cfg.get("active_title", ""))
            if t:
                t.glossary = path.name
                titles.save(t)
            self._save_and_reload()
            GlossaryEditor(path, self).exec()
            self._fill_titles()

    # ---------------------------------------------------------------- тонкая настройка
    def _build_tuning(self) -> Card:
        c = self.cfg
        card = Card(tr("Тонкая настройка"), tr("Распознавание, вид перевода, оформление"), "tune",
                    collapsible=True, collapsed=True)
        rec = Dropdown(tr("Распознавание текста"), [
            ("fast", tr("Быстрый"), tr("1 проход · для обычных облаков"), ""),
            ("thorough", tr("Тщательный"), tr("3 прохода · тёмные фоны, светлый текст"), ""),
            ("max", tr("Максимальный"), tr("5 проходов · пёстрые фоны, цветной текст"), ""),
        ], c.get("ocr_mode", "thorough"))
        rec.changed.connect(lambda v: self._set("ocr_mode", v))
        card.add(rec)
        card.add(caption(tr("Если текст на тёмном или пёстром фоне не находится, выберите режим потщательнее.")))

        grid = QGridLayout()
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(8)
        self.disp_combo = self._combo([("overlay", tr("Поверх облаков")), ("list", tr("Окно со списком")),
                                       ("both", tr("И то, и другое"))], c["display_mode"],
                                      lambda v: self._set("display_mode", v))
        # EasyOCR — только если он установлен: в собранную программу он не входит,
        # и раньше выбор этого пункта давал ошибку при каждом переводе
        engines = [("windows", "Windows OCR")] + ([("easyocr", "EasyOCR")] if easyocr_available() else [])
        engine = self._combo(engines, c["ocr_engine"], self._set_engine)
        themes = self._combo([("system", tr("Как в Windows")), ("light", tr("Светлая")), ("dark", tr("Тёмная"))],
                             c.get("theme", "system"), self._set_theme)
        grid.addWidget(self._labeled(tr("Показ перевода"), self.disp_combo), 0, 0)
        grid.addWidget(self._labeled(tr("Движок OCR"), engine), 0, 1)
        # названия языков — на самих языках, чтобы найти свой при любом интерфейсе
        ui_lang = self._combo([("system", tr("Как в системе")), ("ru", "Русский"), ("en", "English")],
                              c.get("ui_lang", "system"), self._set_ui_lang)
        grid.addWidget(self._labeled(tr("Тема"), themes), 1, 0)
        grid.addWidget(self._labeled(tr("Язык интерфейса"), ui_lang), 1, 1)
        card.add(grid)

        for title, sub, key, val, step, lo, hi, fmt in (
            (tr("Склейка строк"), tr("Больше — охотнее собирает строки в одно облако"), "bubble_gap",
             float(c["bubble_gap"]), 0.1, 0.2, 4.0, lambda v: f"{v:.1f}"),
            (tr("Увеличение перед OCR"), tr("Мелкий текст читается лучше"), "ocr_upscale",
             float(c["ocr_upscale"]), 0.25, 1.0, 4.0, lambda v: f"×{v:.2f}"),
            (tr("Шрифт перевода"), tr("Максимальный размер"), "overlay_max_font_size",
             float(c["overlay_max_font_size"]), 1, 8, 48, lambda v: f"{int(v)} px"),
            (tr("Непрозрачность плашек"), "", "overlay_opacity",
             float(c["overlay_opacity"]), 0.05, 0.3, 1.0, lambda v: f"{int(v * 100)} %"),
            (tr("Live: проверка экрана"), tr("Как часто смотреть, остановилась ли страница"), "auto_interval_ms",
             float(c["auto_interval_ms"]), 100, 300, 5000, lambda v: tr("{0} мс", int(v))),
            (tr("Компактная панель"), tr("Через сколько сворачивается, когда мышь ушла"), "mini_collapse_delay",
             float(c.get("mini_collapse_delay", 5)), 1, 0, 30, lambda v: tr("{0} с", int(v))),
        ):
            st = Stepper(title, sub, val, step, lo, hi, fmt)
            st.changed.connect(lambda v, k=key: self._set(k, int(v) if k in ("overlay_max_font_size",
                                                                                "auto_interval_ms",
                                                                                "mini_collapse_delay") else v))
            card.add(st)

        for title, sub, key in (
            (tr("Не переводить звуки"), tr("Текст поверх рисунка, а не в облаке"), "skip_free_text"),
            (tr("Пропускать обрезанное"), tr("Текст, который режет край области"), "skip_cut_bubbles"),
            (tr("Повторять наклон текста"), tr("Перевод под тем же углом, что и оригинал"), "keep_tilt"),
            (tr("Разделять облака по контуру"), tr("Не склеивать облака, стоящие вплотную"), "bubble_border_check"),
            (tr("Выделять по всей ширине"), tr("Проводите мышкой только сверху вниз — ширина берётся вся"),
             "full_width_select"),
        ):
            sw = SwitchRow(title, sub, bool(c.get(key, False)))
            sw.toggled.connect(lambda v, k=key: self._set(k, v))
            card.add(sw)
        upd = SwitchRow(tr("Проверять обновления"), tr("Раз в день узнавать на GitHub, не вышла ли новая версия"),
                        bool(c.get("update_check", True)))
        upd.toggled.connect(self._set_update_check)
        card.add(upd)
        return card

    # ---------------------------------------------------------------- горячие клавиши
    def _build_hotkeys(self) -> Card:
        card = Card(tr("Горячие клавиши"), tr("Работают, даже когда окно свёрнуто"), "keyboard",
                    collapsible=True, collapsed=True)
        hk = self.cfg["hotkeys"]
        grid = QGridLayout()
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(8)
        for i, (key, title) in enumerate((("select", tr("Выделить область")), ("translate", tr("Перевести")),
                                          ("toggle", tr("Показать или скрыть перевод")), ("auto", tr("Live вкл/выкл")),
                                          ("mini", tr("Компактный режим")), ("copy", tr("Копировать перевод")))):
            f = Field(title, hk.get(key, ""), "<alt>+q")
            f.edited.connect(lambda v, k=key: self._set_hotkey(k, v))
            grid.addWidget(f, i // 2, i % 2)
        card.add(grid)
        card.add(caption(tr("Формат: <alt>+q, <ctrl>+<shift>+t, <f8>. Пустое поле — клавиша отключена. "
                            "Работают при любой раскладке: Alt+Q сработает и на русской.")))
        return card

    # ---------------------------------------------------------------- вкладка «Голос»
    def _switch_key(self) -> str:
        return "voice" if self.cfg.get("ui_page") == "voice" else self.cfg.get("translate_mode", "shot")

    def _on_mode_switch(self, key: str) -> None:
        """Вкладки в шапке: «Скриншот» и «Live» — режимы экранного переводчика, «Голос» — своя страница."""
        if key == "voice":
            self._set_page("voice")
            return
        if self.cfg.get("ui_page") == "voice":
            self._set_page("screen")
        self._set_mode(key)

    def _set_page(self, page: str) -> None:
        if self.cfg.get("ui_page") != page:
            self.cfg["ui_page"] = page
            self.cfg.save()
        self._show_page()
        self._refresh_status()

    def _show_page(self) -> None:
        """Показать страницу выбранной вкладки: настройки экранного переводчика или голоса."""
        voice_on = self.cfg.get("ui_page") == "voice"
        self.screen_page.setVisible(not voice_on)
        self.voice_page.setVisible(voice_on)
        self.screen_actions.setVisible(not voice_on)
        self.voice_actions.setVisible(voice_on)
        if voice_on:
            self._refresh_voice()
        relayout(self.voice_page if voice_on else self.screen_page)

    def _refresh_voice_hero(self) -> None:
        """Шапка на вкладке «Голос»: состояние, кнопки и подсказка."""
        c = self.cfg
        key = c["hotkeys"].get("voice", "")
        on = bool(c.get("voice_enabled"))
        if not voice.available():
            self.chip.set(False, tr("Распознавания речи нет в этой сборке"))
        elif not voice.model_ready(c.get("voice_model", "base")):
            self.chip.set(False, tr("Голос в чат · модель распознавания не скачана"))
        elif on and key:
            self.chip.set(True, tr("Голос в чат включён · {0}", pretty(key)))
        elif on:
            self.chip.set(True, tr("Голос в чат включён · без клавиши"))
        else:
            self.chip.set(False, tr("Голос в чат выключен"))
        state = self.voice.state
        set_pill(self.btn_say, tr("Остановить") if state == "listening" else
                 tr("Распознаю…") if state == "busy" else tr("Сказать фразу"), "hero",
                 "stop" if state == "listening" else "mic")
        self.btn_say.setEnabled(state != "busy" and voice.available())
        set_pill(self.btn_voice_on, tr("Выключить клавишу") if on else tr("Включить клавишу"), "heroOutline", "power")
        self.btn_voice_on.setToolTip(tr("Клавиша {0} работает поверх игры, даже когда окно свёрнуто", pretty(key))
                                     if key else tr("Задайте клавишу в карточке «Голос в чат»"))
        self.btn_voice_on.setEnabled(voice.available())
        self.btn_voice_again.setEnabled(self.voice.last is not None)
        if not on:
            hint = tr("Нажмите «Включить клавишу», чтобы говорить прямо из игры. «Сказать фразу» работает и без неё.")
        elif key:
            hint = tr("Удерживайте {0} и говорите — отпустите, когда закончите. Или нажмите {0} один раз: запись "
                      "остановится сама, когда вы замолчите. Перевод сразу окажется в буфере обмена — вставьте его в "
                      "чат: Ctrl+V.", pretty(key))
        else:
            hint = tr("Задайте клавишу в карточке «Голос в чат» — без неё работает только кнопка «Сказать фразу».")
        self.hint.setText(hint)

    def _toggle_voice(self) -> None:
        on = not self.cfg.get("voice_enabled")
        self.cfg["voice_enabled"] = on
        self.cfg.save()
        if on:
            self.voice.preload()
        else:
            self.voice.shutdown()       # освободить память, занятую моделью
        self._start_hotkeys()
        self._refresh_voice()

    def _copy_voice_again(self) -> None:
        if self.voice.last is not None:
            QApplication.clipboard().setText(self.voice.last.text)
            self._set_status(tr("Снова в буфере обмена: {0}", self.voice.last.text))

    def _build_voice_page(self) -> QWidget:
        page = QWidget()
        page.setObjectName("transparent")
        lay = QVBoxLayout(page)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(14)
        self.voice_tor_note = self.voice_model_note = self.voice_dl_btn = self.voice_gloss_combo = None
        self.voice_gloss_note = self.voice_recent = None
        if not voice.available():
            card = Card(tr("Голос в чат"), tr("Скажите фразу — перевод сразу окажется в буфере обмена для чата игры"),
                        "mic")
            card.add(caption(tr("В этой сборке нет распознавания речи. Если программа запущена из исходников, "
                                "установите пакеты: pip install faster-whisper sounddevice")))
            lay.addWidget(card)
            return page
        lay.addWidget(Columns([self._build_voice_main(), self._build_voice_speech(), self._build_voice_glossary(),
                               self._build_voice_recent()]))
        return page

    def _build_voice_main(self) -> Card:
        c = self.cfg
        card = Card(tr("Голос в чат"), tr("Скажите фразу — перевод сразу окажется в буфере обмена для чата игры"), "mic")
        langs = [(k, v["name"]) for k, v in languages.voice_languages().items()]
        mine = self._combo(langs, voice.my_language(c), lambda v: self._set_voice("voice_my_lang", v, refill=True))
        chat = self._combo(langs, voice.chat_language(c), lambda v: self._set_voice("voice_chat_lang", v))
        arrow = QLabel()
        arrow.setPixmap(theme.pixmap("arrow", theme.palette().primary, 20))
        card.add(ResponsiveRow(self._labeled(tr("Я говорю на"), mine), arrow,
                               self._labeled(tr("Перевод в чат на"), chat), anchor=mine.header))
        hk = Field(tr("Клавиша"), c["hotkeys"].get("voice", ""), "<alt>+v")
        hk.edited.connect(self._set_voice_hotkey)
        card.add(hk)
        self.voice_tor_note = caption("")
        card.add(self.voice_tor_note)
        return card

    def _build_voice_speech(self) -> Card:
        c = self.cfg
        card = Card(tr("Распознавание речи"), tr("Прямо на компьютере, без интернета"), "tune")
        model = Dropdown(tr("Модель"), [
            ("base", tr("Быстрое · {0} МБ", voice.MODELS["base"]["mb"]),
             tr("Фраза распознаётся за 1–2 секунды"), ""),
            ("small", tr("Точное · {0} МБ", voice.MODELS["small"]["mb"]),
             tr("Лучше понимает быструю и невнятную речь, но в 2–3 раза дольше"), ""),
        ], c.get("voice_model", "base"))
        model.changed.connect(self._set_voice_model)
        card.add(model)
        self.voice_model_note = caption("")
        card.add(self.voice_model_note)
        self.voice_dl_btn = pill(tr("Скачать модель"), "primary", "download")
        self.voice_dl_btn.clicked.connect(self._voice_download)
        row = QHBoxLayout()
        row.addWidget(self.voice_dl_btn)
        row.addStretch(1)
        card.add(row)
        mics = voice.input_devices()
        cur = c.get("voice_mic", "")
        items = [("", tr("Как в системе"))] + [(m, m) for m in mics]
        if cur and cur not in mics:
            items.append((cur, tr("{0} (не подключён)", cur)))
        mic = self._combo(items, cur, lambda v: self._set_voice("voice_mic", v))
        card.add(self._labeled(tr("Микрофон"), mic))
        snd = SwitchRow(tr("Звуковые сигналы"), tr("Короткий звук, когда программа начала слушать и когда перевод "
                                                   "готов: в полноэкранной игре окна не видно"),
                        bool(c.get("voice_sounds", True)))
        snd.toggled.connect(lambda v: self._set_voice("voice_sounds", v))
        card.add(snd)
        card.add(caption(tr("Голос никуда не отправляется: в интернет уходит только распознанный текст — переводчику. "
                            "Сама программа в чат ничего не вставляет и клавиш не нажимает.")))
        return card

    def _build_voice_glossary(self) -> Card:
        card = Card(tr("Словарь для чата"), tr("Игровые слова, герои, ники — как сказать и как написать"), "book")
        self.voice_gloss_combo = Select()
        self.voice_gloss_combo.currentIndexChanged.connect(self._on_voice_gloss_combo)
        card.add(self._labeled(tr("Словарь"), self.voice_gloss_combo))
        row = FlowLayout(8)
        host = QWidget()
        host.setLayout(row)
        edit = pill(tr("Изменить"), "outline")
        edit.clicked.connect(self._edit_voice_glossary)
        new = pill(tr("Новый"), "tonal", "plus", tr("Новый словарь для игры: с вашего языка на язык чата"))
        new.clicked.connect(self._new_voice_glossary)
        folder = pill(tr("Папка"), "tonal", "folder", tr("Открыть папку с глоссариями"))
        folder.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(GLOSSARY_DIR))))
        for b in (edit, new, folder):
            row.addWidget(b)
        card.add(host)
        self.voice_gloss_note = caption("")
        card.add(self.voice_gloss_note)
        card.add(caption(tr("Слова из словаря распознаются в речи увереннее и попадают в перевод так, как записаны: "
                            "«го на мид» → «go mid». Склонения находятся сами: «катку», «мидом». Словари — те же "
                            "глоссарии, что у тайтлов; здесь видны те, что с вашего языка.")))
        self._fill_voice_glossaries()
        return card

    def _build_voice_recent(self) -> Card:
        card = Card(tr("Последние фразы"), tr("Щёлкните перевод, чтобы снова положить его в буфер"), "list")
        self.voice_recent = QLabel()
        self.voice_recent.setWordWrap(True)
        self.voice_recent.setTextFormat(Qt.TextFormat.RichText)
        self.voice_recent.linkActivated.connect(self._copy_recent)
        card.add(self.voice_recent)
        return card

    # ------------------------------------------------------------ словарь для чата
    def _fill_voice_glossaries(self) -> None:
        """Словари с моего языка (и выбранный, даже если он к языкам не подходит — чтобы было видно, что выбрано)."""
        if self.voice_gloss_combo is None:
            return
        mine = voice.my_language(self.cfg)
        cur = self.cfg.get("voice_glossary") or ""
        self._filling_voice = True
        self.voice_gloss_combo.clear()
        self.voice_gloss_combo.addItem(tr("Без словаря"), "")
        for path in list_glossaries(GLOSSARY_DIR):
            try:
                g = Glossary.load(path)
            except (OSError, ValueError):
                continue
            mine_ok = not g.source_lang or languages.whisper_code(g.source_lang) == languages.whisper_code(mine)
            if mine_ok or path.name == cur:
                self.voice_gloss_combo.addItem(tr("{0} · слов: {1}", g.title, len(g.entries)), path.name)
        self.voice_gloss_combo.setCurrentIndex(max(0, self.voice_gloss_combo.findData(cur)))
        self._filling_voice = False

    def _on_voice_gloss_combo(self, _i: int) -> None:
        if getattr(self, "_filling_voice", False):
            return
        self._set_voice("voice_glossary", self.voice_gloss_combo.currentData() or "")

    def _edit_voice_glossary(self) -> None:
        name = self.cfg.get("voice_glossary") or ""
        if not name or not (GLOSSARY_DIR / name).exists():
            self._new_voice_glossary()
            return
        GlossaryEditor(GLOSSARY_DIR / name, self).exec()
        self._fill_voice_glossaries()
        self._refresh_voice()

    def _new_voice_glossary(self) -> None:
        c = self.cfg
        path = new_glossary_file(self, voice.my_language(c), tr("Игра"), target_lang=voice.chat_language(c))
        if path:
            self.cfg["voice_glossary"] = path.name
            self.cfg.save()
            GlossaryEditor(path, self).exec()
            self._fill_voice_glossaries()
            self._refresh_voice()

    def _voice_glossary_text(self) -> str:
        c = self.cfg
        name = c.get("voice_glossary") or ""
        if not name:
            return tr("Без словаря игровые слова переводятся как получится.")
        try:
            g = Glossary.load(GLOSSARY_DIR / name)
        except (OSError, ValueError):
            return tr("Словарь не найден — выберите другой.")
        mine, chat = voice.my_language(c), voice.chat_language(c)
        if not voice.glossary_fits(g, mine, chat):
            return tr("Этот словарь — с языка «{0}» на «{1}», а сейчас выбраны другие языки, поэтому он не используется.",
                      i18n.lower(languages.name_of(g.source_lang) or "?"),
                      i18n.lower(languages.name_of(g.target_lang) or "?"))
        return tr("Используется: слов {0}.", len(g.entries))

    def _copy_recent(self, href: str) -> None:
        try:
            phrase = list(self.voice.recent)[int(href)]
        except (ValueError, IndexError):
            return
        QApplication.clipboard().setText(phrase.text)
        self._set_status(tr("Снова в буфере обмена: {0}", phrase.text))

    # ------------------------------------------------------------ настройки
    def _set_voice(self, key: str, value, refill: bool = False) -> None:
        self.cfg[key] = value
        self.cfg.save()
        if refill:
            self._fill_voice_glossaries()
        self._refresh_voice()

    def _set_voice_hotkey(self, value: str) -> None:
        self.cfg["hotkeys"]["voice"] = value
        self.cfg.save()
        self._start_hotkeys()
        self._refresh_voice()

    def _set_voice_model(self, name: str) -> None:
        if self._voice_dl is not None and self._voice_dl.running:
            self._voice_dl.cancel()
        self.voice.recognizer.unload()
        self._set_voice("voice_model", name)
        self.voice.preload()

    def _refresh_voice(self) -> None:
        """Карточки вкладки «Голос» и шапка — по текущему состоянию."""
        if self.cfg.get("ui_page") == "voice" and hasattr(self, "chip"):
            self._refresh_voice_hero()
        if self.voice_model_note is None:
            return
        c = self.cfg
        mine, chat = voice.my_language(c), voice.chat_language(c)
        from ..translators import TRANSLATORS
        tor = TRANSLATORS.get(c.get("translator", ""))
        note = tr("Переводит: {0}. Переводчик выбирается на вкладке «Скриншот» или «Live».",
                  tor.title if tor is not None else c.get("translator", ""))
        if languages.whisper_code(mine) == languages.whisper_code(chat):
            note += " " + tr("Сейчас языки совпадают — фраза будет копироваться без перевода.")
        self.voice_tor_note.setText(note)
        name = c.get("voice_model", "base")
        mb = voice.MODELS.get(name, voice.MODELS["base"])["mb"]
        dl = self._voice_dl if self._voice_dl is not None and self._voice_dl.running else None
        if dl is not None:
            self.voice_model_note.setText(tr("Скачивание модели… {0} %", self._voice_pct))
            set_pill(self.voice_dl_btn, tr("Отменить"), "outline", "stop")
            self.voice_dl_btn.setVisible(True)
        elif voice.model_ready(name):
            self.voice_model_note.setText(tr("Модель скачана ({0} МБ) — можно пользоваться.", mb))
            self.voice_dl_btn.setVisible(False)
        else:
            self.voice_model_note.setText(tr("Модель распознавания скачивается один раз: {0} МБ.", mb))
            set_pill(self.voice_dl_btn, tr("Скачать модель"), "primary", "download")
            self.voice_dl_btn.setVisible(True)
        self.voice_gloss_note.setText(self._voice_glossary_text())
        recent = list(self.voice.recent)
        if recent:
            pal = theme.palette()
            rows = []
            for i, ph in enumerate(recent):
                said = f"<br><span style='color:{pal.text2}'>{html.escape(ph.heard)}</span>" if ph.translated else ""
                link = (f"<a href='{i}' style='color:{pal.primary}; text-decoration:none; font-weight:600'>"
                        f"{html.escape(ph.text)}</a>")
                rows.append(f"<p style='margin:0 0 8px 0'>{link}{said}</p>")
            self.voice_recent.setText("".join(rows))
        else:
            self.voice_recent.setText(tr("Пока пусто: нажмите «Сказать фразу» или клавишу голоса."))
        relayout(self.voice_recent)

    def _voice_download(self) -> None:
        if self._voice_dl is not None and self._voice_dl.running:
            self._voice_dl.cancel()
            return
        dl = ModelDownload(self.cfg.get("voice_model", "base"), self)
        dl.progress.connect(self._on_voice_progress)
        dl.done.connect(self._on_voice_downloaded)
        dl.failed.connect(lambda msg: (self._set_status(msg, True), self._refresh_voice()))
        self._voice_dl, self._voice_pct = dl, 0
        dl.start()
        self._refresh_voice()

    def _on_voice_progress(self, done: int, total: int) -> None:
        pct = int(done * 100 / total) if total else 0
        if pct != self._voice_pct:
            self._voice_pct = pct
            self._refresh_voice()

    def _on_voice_downloaded(self) -> None:
        self._set_status(tr("Модель распознавания скачана — «Голос в чат» готов."), notify=True)
        self._refresh_voice()
        self.voice.preload()

    def _on_voice_done(self, phrase) -> None:
        if phrase.translated:
            self._set_status(tr("Перевод в буфере обмена: {0}", phrase.text))
        else:
            self._set_status(tr("Сказано на языке чата — скопировано без перевода: {0}", phrase.text))
        self._refresh_voice()

    def _on_voice_failed(self, message: str) -> None:
        self._set_status(message, True)

    # ---------------------------------------------------------------- для разработчиков
    def _build_developer(self) -> Card:
        c = self.cfg
        card = Card(tr("Для разработчиков"), tr("Отладочная информация и проверка скорости"), "code",
                    collapsible=True, collapsed=True)
        dev = SwitchRow(tr("Режим разработчика"), tr("Время каждого этапа, проходы распознавания, рамки строк и журнал"),
                        bool(c.get("dev_mode")))
        card.add(dev)
        par = SwitchRow(tr("Параллельное распознавание"),
                        tr("Проходы распознавания идут одновременно — быстрее на многоядерных процессорах"),
                        bool(c.get("parallel_ocr", True)))
        par.toggled.connect(lambda v: self._set("parallel_ocr", v))
        card.add(par)

        extra = QWidget()
        el = QVBoxLayout(extra)
        el.setContentsMargins(0, 0, 0, 0)
        el.setSpacing(10)
        boxes = SwitchRow(tr("Рамки распознанных строк"), tr("Поверх страницы, своим цветом для каждого прохода"),
                          bool(c.get("debug_boxes", True)))
        boxes.toggled.connect(lambda v: self._set("debug_boxes", v))
        el.addWidget(boxes)
        el.addWidget(label(tr("ПОСЛЕДНИЙ ЗАПУСК"), "fieldLabel"))
        self.dev_report = QPlainTextEdit()
        self.dev_report.setReadOnly(True)
        self.dev_report.setPlaceholderText(tr("Здесь появятся подробности после перевода."))
        mono = QFont("Consolas")
        mono.setStyleHint(QFont.StyleHint.Monospace)
        mono.setPointSize(9)
        self.dev_report.setFont(mono)
        self.dev_report.setMinimumHeight(150)
        self.dev_report.setMaximumHeight(220)
        el.addWidget(self.dev_report)
        buttons = QWidget()
        buttons.setObjectName("transparent")
        fl = FlowLayout(8)
        buttons.setLayout(fl)
        cmp_btn = pill(tr("Сравнить на текущей зоне"), "outline", "bolt",
                       tr("Распознать зону по очереди и параллельно: что быстрее и совпал ли результат"))
        cmp_btn.clicked.connect(self.compare_now)
        fl.addWidget(cmp_btn)
        copy_btn = pill(tr("Копировать журнал"), "tonal", "link")
        copy_btn.clicked.connect(self._copy_dev_log)
        fl.addWidget(copy_btn)
        open_btn = pill(tr("Открыть журнал"), "tonal", "folder")
        open_btn.clicked.connect(lambda: DEV_LOG.exists() and QDesktopServices.openUrl(QUrl.fromLocalFile(str(DEV_LOG))))
        fl.addWidget(open_btn)
        clear_btn = pill(tr("Очистить"), "tonal", "refresh")
        clear_btn.clicked.connect(self._clear_dev_log)
        fl.addWidget(clear_btn)
        hist_btn = pill(tr("История переводов"), "tonal", "list",
                        tr("Оригинал и перевод каждой реплики, пока включён режим разработчика"))
        hist_btn.clicked.connect(self.show_history)
        fl.addWidget(hist_btn)
        el.addWidget(buttons)
        extra.setVisible(bool(c.get("dev_mode")))
        card.add(extra)

        def toggle_dev(v: bool) -> None:
            self._set("dev_mode", v)
            extra.setVisible(v)
            relayout(extra)
            self.overlay.update()

        dev.toggled.connect(toggle_dev)
        return card

    def _copy_dev_log(self) -> None:
        text = DEV_LOG.read_text(encoding="utf-8") if DEV_LOG.exists() else ""
        if not text.strip():
            self._set_status(tr("Журнал пуст: включите режим разработчика и выполните перевод."))
            return
        QApplication.clipboard().setText(text)
        self._set_status(tr("Журнал скопирован."))

    def _clear_dev_log(self) -> None:
        try:
            DEV_LOG.unlink()
        except OSError:
            pass
        self._set_status(tr("Журнал очищен."))

    def _show_dev(self, text: str) -> None:
        if getattr(self, "dev_report", None) is not None:
            self.dev_report.setPlainText(text)
        dev_log(text)

    def compare_now(self) -> None:
        if self.region is None:
            self._set_status(tr("Область ещё не выделена."), True)
            return
        if self.busy:
            return
        self.busy = True
        self._set_status(tr("Сравниваю… это займёт несколько секунд"))
        hidden = self.overlay.isVisible() and not self.overlay.excluded
        if hidden:
            self.overlay.hide()

        def go():
            image, _k = grab_region(self.region)
            if hidden and not self.overlay.user_hidden:
                self.overlay.show()
            job = _CompareJob(self.compare_signals, self.pipeline, image)
            self.pool.start(job)

        QTimer.singleShot(120 if hidden else 0, go)

    def _on_compared(self, text: str) -> None:
        self.busy = False
        self._show_dev(text)
        self._set_status(text.split("\n")[-1])

    # ================================================================ изменение настроек
    def _save_and_reload(self) -> None:
        self.cfg.save()
        self.pipeline.reload()

    def _set(self, key: str, value) -> None:
        self.cfg[key] = value
        self._save_and_reload()
        if key == "auto_interval_ms" and self.auto_timer.isActive():
            self.auto_timer.start(int(value))
        if key in ("overlay_max_font_size", "overlay_opacity"):
            self.overlay.update()
        if key == "mini_collapse_delay" and self.mini is not None:
            self.mini.collapse_delay = float(value)
        self._refresh_status()

    def _set_key(self, translator: str, key: str, value: str) -> None:
        self.cfg["translators"].setdefault(translator, {})[key] = value
        self._save_and_reload()

    def _set_translator(self, name: str) -> None:
        self._set("translator", name)
        self._render_keys()
        self._refresh_auto_glossary()       # пополнение работает только с ИИ-переводчиками

    def _set_source(self, lang: str) -> None:
        if lang == "auto":
            # язык найдётся при следующем переводе; до тех пор остаётся прежний
            self.cfg["auto_lang"] = True
            self.pipeline.reset_auto()
            self._save_and_reload()
            self.src_combo.setItemText(0, self._auto_title())
            self._refresh_auto_hint()
            return
        self.cfg["auto_lang"] = False
        self._apply_source(lang)
        self.src_combo.setItemText(0, self._auto_title())
        self._refresh_auto_hint()
        QTimer.singleShot(200, self.check_ocr_language)

    def _apply_source(self, lang: str) -> None:
        """Сменить язык оригинала (вручную или автоопределением) и всё, что от него зависит."""
        self.cfg["source_lang"] = lang
        # универсальный глоссарий меняем на подходящий (свои глоссарии не трогаем)
        cur = self.cfg.get("glossary") or ""
        if not cur or cur.startswith("universal_"):
            self.cfg["glossary"] = universal_for(GLOSSARY_DIR, lang)
            t = titles.load(self.cfg.get("active_title", ""))
            if t:
                t.glossary = self.cfg["glossary"]
                titles.save(t)
        self._save_and_reload()
        self._fill_titles()

    def _on_language_detected(self, lang: str) -> None:
        """Автоопределение нашло другой язык: запомнить его и показать в списке."""
        self._apply_source(lang)
        self.src_combo.setItemText(0, self._auto_title())

    def _set_target(self, lang: str) -> None:
        self._set("target_lang", lang)
        self._refresh_auto_glossary()       # глоссарий мог стать неподходящим (или снова подходящим)

    def _set_engine(self, engine: str) -> None:
        self._set("ocr_engine", engine)
        QTimer.singleShot(200, self.check_ocr_language)

    def _set_theme(self, mode: str) -> None:
        self.cfg["theme"] = mode
        self.cfg.save()
        theme.apply(QApplication.instance(), mode)
        QTimer.singleShot(0, self._rebuild)

    def _set_ui_lang(self, lang: str) -> None:
        if lang == self.cfg.get("ui_lang", "system"):
            return
        self.cfg["ui_lang"] = lang
        self.cfg.save()
        if i18n.resolve(lang) == i18n.english:
            return          # «как в системе» и так совпадает с текущим языком
        if QMessageBox.question(self, tr("Язык интерфейса"),
                                tr("Язык сменится после перезапуска программы. Перезапустить сейчас?"),
                                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
                                ) == QMessageBox.StandardButton.Yes:
            self.restart_app()

    def _rebuild(self) -> None:
        pos = self.scroll.verticalScrollBar().value()
        self._build()
        self._refresh_status()
        QTimer.singleShot(0, lambda: self.scroll.verticalScrollBar().setValue(pos))

    def _set_hotkey(self, key: str, value: str) -> None:
        self.cfg["hotkeys"][key] = value
        self.cfg.save()
        self._start_hotkeys()
        self._refresh_status()

    def _set_mode(self, mode: str) -> None:
        self.cfg["translate_mode"] = mode
        self.cfg.save()
        if mode == "shot" and self.live_on:
            self.set_live(False)
        self._refresh_status()

    def _toggle_on_top(self, on: bool) -> None:
        self.cfg["always_on_top"] = on
        self.cfg.save()
        self._apply_on_top()
        self.show()

    def _apply_on_top(self) -> None:
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, bool(self.cfg.get("always_on_top", False)))

    # ================================================================ состояние
    def _refresh_status(self) -> None:
        if self.cfg.get("ui_page") == "voice":
            self._refresh_voice_hero()
            self.mode_switch.select("voice")
            self._sync_mini()
            return
        live = self.cfg.get("translate_mode", "shot") == "live"
        hk = self.cfg["hotkeys"]
        if self.region:
            area = tr("Область {0}×{1}", self.region.w, self.region.h)
        else:
            area = tr("Область не выделена")
        if live:
            self.chip.set(self.live_on, f"{area} · Live {tr('работает') if self.live_on else tr('на паузе')}")
            set_pill(self.btn_main, tr("Пауза Live") if self.live_on else tr("Включить Live"), "hero",
                     "stop" if self.live_on else "play")
            self.hint.setText(tr("Live: листайте главу — перевод появится сам, когда страница остановится. {0} — пауза, "
                                 "{1} — новая область.", hk.get('auto', ''), hk.get('select', '')))
        else:
            self.chip.set(bool(self.region), area)
            set_pill(self.btn_main, tr("Перевести"), "hero", "play")
            self.btn_main.setToolTip(tr("Горячая клавиша: {0}", hk.get('translate', '')))
            self.hint.setText(tr("{0} — выделить область · {1} — перевести · {2} — показать или скрыть перевод · {3} — "
                                 "Live", hk.get('select', ''), hk.get('translate', ''), hk.get('toggle', ''), hk.get('auto', ''))
                              + (tr(" · {0} — копировать перевод", hk["copy"]) if hk.get("copy") else ""))
        self.mode_switch.select("live" if live else "shot")
        self.btn_zone.setEnabled(self.region is not None)
        self._sync_mini()

    def _on_save_failed(self, err: str) -> None:
        if hasattr(self, "status"):
            self._set_status(tr("Настройки не сохранились: {0}", err), True, notify=True)

    def _set_status(self, text: str, error: bool = False, notify: bool = False) -> None:
        color = "#FFB4A8" if error else theme.CREAM
        self.status.setStyleSheet(f"color: {color}; background: transparent; font-weight: 600;")
        self.status.setText(text)
        if (error or notify) and self.in_mini:
            # в компактном режиме окна со статусом не видно — показываем сообщение у панели
            self.mini.toast(text, error)

    def _set_busy(self, on: bool) -> None:
        self.busy = on
        self._sync_mini()

    # ================================================================ компактный режим
    @property
    def in_mini(self) -> bool:
        return self.mini is not None and self.mini.isVisible()

    def _mini_state(self) -> dict:
        return {
            "live_mode": self.cfg.get("translate_mode", "shot") == "live",
            "live_on": self.live_on,
            "busy": self.busy,
            "has_region": self.region is not None,
            "overlay_visible": self.overlay.isVisible() and not self.overlay.user_hidden,
            "zone_visible": self.overlay.show_frame,
            "can_copy": bool(self.shown_result is not None and self.shown_result.translations()),
        }

    def _sync_mini(self) -> None:
        if self.in_mini:
            self.mini.refresh()

    def enter_mini(self) -> None:
        """Спрятать большое окно и оставить маленькую панель поверх экрана."""
        if self.mini is None:
            self.mini = MiniPanel(self._mini_state, float(self.cfg.get("mini_collapse_delay", 5)))
            self.mini.triggered.connect(self._mini_action)
            self.mini.moved.connect(self._mini_moved)
        self.mini.collapse_delay = float(self.cfg.get("mini_collapse_delay", 5))
        self.mini.place(self.cfg.get("mini_pos"))
        self.mini.show()
        self.mini.refresh()
        self.hide()
        if not self.cfg.get("mini_mode"):
            self.cfg["mini_mode"] = True
            self.cfg.save()

    def exit_mini(self) -> None:
        if self.mini is not None:
            self.mini.hide()
        if self.cfg.get("mini_mode"):
            self.cfg["mini_mode"] = False
            self.cfg.save()
        self.show()
        self.raise_()
        self.activateWindow()

    def _mini_moved(self, x: int, y: int) -> None:
        self.cfg["mini_pos"] = [x, y]
        self.cfg.save()

    def _mini_action(self, key: str) -> None:
        if key == "main":
            self._main_action()
        elif key == "mode":
            self._set_mode("shot" if self.cfg.get("translate_mode") == "live" else "live")
        elif key == "toggle":
            self.toggle_overlay()
        elif key == "zone_new":
            self.start_selection()
        elif key == "zone_show":
            self.show_zone(hold=True)
        elif key == "zone_hide":
            self._hide_zone()
        elif key == "copy":
            self.copy_translation()
        elif key == "full":
            self.exit_mini()
        elif key == "quit":
            self.close()
        self._sync_mini()

    def _mini_in_shot(self) -> bool:
        """Попадёт ли панель в снимок области (если Windows не умеет скрывать окно от снимков)."""
        if not self.in_mini or self.region is None:
            return False
        geo = find_screen(self.region.screen).geometry()
        zone = QRect(geo.x() + self.region.x, geo.y() + self.region.y, self.region.w, self.region.h)
        return not self.mini.excluded and self.mini.visible_rect().intersects(zone)

    def _mini_hide_for_shot(self, hide: bool) -> None:
        self.mini.setWindowOpacity(0.0 if hide else 1.0)

    # ================================================================ горячие клавиши
    def _start_hotkeys(self) -> None:
        """Глобальные сочетания (работают при любой раскладке — см. app/hotkeys.py)."""
        self._stop_hotkeys()
        mapping = {}
        for action, combo in self.cfg["hotkeys"].items():
            if action == "voice" and not (self.cfg.get("voice_enabled") and voice.available()):
                continue
            if combo:
                mapping[combo] = (lambda a=action: self.bridge.fired.emit(a))
        if not mapping:
            return
        self._hotkeys = Hotkeys()
        try:
            errors = self._hotkeys.start(mapping)
        except Exception as e:  # noqa: BLE001
            errors = [str(e)]
        if errors:
            self._set_status(tr("Горячие клавиши: ") + "; ".join(errors), True)

    def _stop_hotkeys(self) -> None:
        if self._hotkeys is not None:
            try:
                self._hotkeys.stop()
            except Exception:  # noqa: BLE001
                pass
            self._hotkeys = None

    def _on_hotkey(self, action: str) -> None:
        if action == "select":
            self.start_selection()
        elif action == "translate":
            self.translate_now()
        elif action == "toggle":
            self.toggle_overlay()
        elif action == "auto":
            if self.cfg.get("translate_mode") != "live":
                self._set_mode("live")
            self.set_live(not self.live_on)
        elif action == "mini":
            if self.in_mini:
                self.exit_mini()
            else:
                self.enter_mini()
        elif action == "copy":
            self.copy_translation()
        elif action == "voice":
            self.voice.press(self.cfg["hotkeys"].get("voice", ""))

    def _main_action(self) -> None:
        if self.cfg.get("translate_mode") == "live":
            self.set_live(not self.live_on)
        else:
            self.translate_now()

    # ================================================================ выделение и зона
    def start_selection(self) -> None:
        if self.selector is not None:
            return
        if self.in_mini:
            self.mini.collapse()        # раскрытая панель мешала бы выделять
        was_visible = self.overlay.isVisible()
        self.overlay.hide()
        self.selector = SelectionWindow(bool(self.cfg.get("full_width_select", False)))
        self.selector.selected.connect(self._on_selected)
        self.selector.cancelled.connect(lambda: self._on_selection_end(was_visible))
        self.selector.start()

    def _on_selection_end(self, restore_overlay: bool) -> None:
        self.selector = None
        if restore_overlay and not self.overlay.user_hidden:
            self.overlay.show()

    def _on_selected(self, region: Region) -> None:
        self.selector = None
        self.region = region
        self.cfg["last_region"] = region.to_dict()
        self.cfg.save()
        self.overlay.clear()
        self.overlay.place(region)
        self.pipeline.reset_auto()      # в новой области язык может быть другим
        self._last_thumb = None
        self._refresh_status()
        if self.cfg.get("translate_mode") == "live":
            self.set_live(True)
        else:
            QTimer.singleShot(50, self.translate_now)

    def show_zone(self, hold: bool = False) -> None:
        """Показать рамку зоны: на пару секунд или (hold) пока её не скроют."""
        if not self.region:
            self._set_status(tr("Область ещё не выделена."), True)
            return
        self.overlay.place(self.region)
        self.overlay.show_frame = True
        self.overlay.user_hidden = False
        self.overlay.show()
        self.overlay.update()
        if hold:
            self.zone_timer.stop()
        else:
            self.zone_timer.start(2500)
        self._sync_mini()

    def _hide_zone(self) -> None:
        self.zone_timer.stop()
        self.overlay.show_frame = False
        self.overlay.update()
        self._sync_mini()

    # ================================================================ перевод
    def translate_now(self) -> None:
        if self.region is None:
            self.start_selection()
            return
        if self.busy:
            return
        # Если окно перевода (или компактная панель) не скрыто от снимков — прячем на время снимка
        hide_overlay = self.overlay.isVisible() and not self.overlay.excluded
        hide_mini = self._mini_in_shot()
        if hide_overlay or hide_mini:
            if hide_overlay:
                self.overlay.hide()
            if hide_mini:
                self._mini_hide_for_shot(True)
            QTimer.singleShot(120, lambda: self._grab_and_run(hide_overlay, hide_mini))
        else:
            self._grab_and_run(False, False)

    def _grab_and_run(self, restore: bool, restore_mini: bool = False) -> None:
        image, k = grab_region(self.region)
        if restore and not self.overlay.user_hidden:
            self.overlay.show()
        if restore_mini and self.mini is not None:
            self._mini_hide_for_shot(False)
        self._run(image, k)

    def _run(self, image: Image.Image, k: float, token=None, live: bool = False) -> None:
        self._set_busy(True)
        self._token += 1
        self._set_status(tr("Распознаю и перевожу…"))
        self.pool.start(_Job(self.job_signals, self.pipeline, image, k,
                             token if token is not None else self._token, live))

    def _handle_done(self, result: PageResult, k: float, token) -> None:
        self._on_done(result, k, token)

    def _handle_failed(self, tb: str) -> None:
        self._on_failed(tb)

    def _on_done(self, result: PageResult, k: float, token) -> None:
        self._set_busy(False)
        if result.detected_lang:
            self._on_language_detected(result.detected_lang)
        self._remember(result)
        if token == "file":
            self._report(result)
            self._show_file_result(result)
            return
        mode = self.cfg["display_mode"]
        self.shown_result = result
        if mode in ("overlay", "both") and self.region:
            self.overlay.set_result(result, k)
        if mode in ("list", "both"):
            self.list_window.set_result(result)
            if not self.list_window.isVisible():
                self.list_window.show()
        self._report(result)
        self._fill_titles_counts()
        self._sync_mini()

    def _fill_titles_counts(self) -> None:
        """Обновить число сохранённых реплик у активного тайтла."""
        t = titles.load(self.cfg.get("active_title", ""))
        i = self.title_combo.currentIndex()
        if t and i > 0:
            self.title_combo.setItemText(i, tr("{0} · {1} реплик", t.name, len(t.history)))

    def _on_failed(self, tb: str) -> None:
        self._set_busy(False)
        self._set_status(tr("Непредвиденная ошибка, подробности в error.log"), True)
        try:
            (DATA_ROOT / "error.log").write_text(tb, encoding="utf-8")
        except OSError:
            pass

    def _report(self, result: PageResult) -> None:
        if self.cfg.get("dev_mode") and result.debug is not None:
            self._show_dev(result.debug.report())
        if result.missing_ocr_lang:
            self._set_status(result.error.split("\n")[0], True)
            self.offer_ocr_install(result.missing_ocr_lang, force=True)
            return
        if result.error:
            if "\n" in result.error:
                QMessageBox.warning(self, "ATlate", result.error)
            self._set_status(result.error.split("\n")[0], True)
            return
        n = sum(1 for b in result.bubbles if b.translation)
        parts = [tr("Переведено облаков: {0}", n)]
        if result.detected_lang:
            parts.append(tr("язык: ") + i18n.lower(languages.src(result.detected_lang)["name"].split(" (")[0]))
        if result.source_detected:
            parts.append(tr("язык определил переводчик: ") + i18n.lower(languages.name_of(result.source_detected)))
        if result.fallback:
            parts.append(result.fallback)
        if result.glossary_added:
            parts.append(tr("в глоссарий добавлено: {0}", result.glossary_added))
            self._fill_titles()         # число записей в списке глоссариев
        # технические подробности — только в режиме разработчика
        if self.cfg.get("dev_mode") and result.debug is not None:
            parts.append(result.debug.summary())
        if not result.bubbles:
            parts = [tr("Текст не найден. Попробуйте режим распознавания «Тщательный» или «Максимальный».")]
        self._set_status(" · ".join(parts), notify=not result.bubbles)

    # ================================================================ режим Live
    def set_live(self, on: bool) -> None:
        if on and self.region is None:
            self.start_selection()
            return
        self.live_on = on
        if on:
            self._last_thumb = None
            self._dirty = True
            self.auto_timer.start(int(self.cfg["auto_interval_ms"]))
            self._set_status(tr("Live: перевожу, когда страница перестаёт двигаться."))
        else:
            self.auto_timer.stop()
            self._set_status(tr("Live на паузе."))
        self._refresh_status()

    def _auto_tick(self) -> None:
        if self.busy or self.region is None or self.selector is not None:
            return
        hidden = False
        hidden_mini = self._mini_in_shot()
        if hidden_mini:
            self._mini_hide_for_shot(True)
        if self.overlay.isVisible() and not self.overlay.excluded:
            # Без скрытия от снимков приходится прятать окно — будет лёгкое мигание
            self.overlay.hide()
            hidden = True
        if hidden or hidden_mini:
            QApplication.processEvents()
        # кадр остаётся в виде QImage: пока страница движется, полная картинка не нужна —
        # раньше каждый кадр Live целиком переводился в PIL, хотя сравнивалась только маленькая копия
        frame, k = grab_region_raw(self.region)
        if hidden and not self.overlay.user_hidden:
            self.overlay.show()
        if hidden_mini:
            self._mini_hide_for_shot(False)
        thumb = gray_thumb(frame)
        changed = self._last_thumb is None or self._last_thumb.shape != thumb.shape \
            or float(np.abs(thumb - self._last_thumb).mean()) > 1.5
        self._last_thumb = thumb
        if changed:
            # Страница двигается: убираем старый перевод и ждём остановки
            self._dirty = True
            self.overlay.clear()
            return
        if self._dirty:
            self._dirty = False
            self._run(qimage_to_pil(frame), k, live=True)

    # ================================================================ картинка из файла
    def translate_file(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, tr("Картинка"), "", tr("Изображения (*.png *.jpg *.jpeg *.webp *.bmp)"))
        if not path or self.busy:
            return
        try:
            image = Image.open(path).convert("RGB")
        except OSError as e:
            self._set_status(tr("Не удалось открыть: {0}", e), True)
            return
        self._file_image = image
        self._run(image, 1.0, token="file")

    def _show_file_result(self, result: PageResult) -> None:
        dlg = ResultDialog(self, result, self._file_image, self.cfg)
        dlg.exec()

    def _show_list(self) -> None:
        self.list_window.show()
        self.list_window.raise_()

    def copy_translation(self) -> None:
        """Скопировать перевод страницы, которая сейчас показана: облака по порядку, через пустую строку."""
        res = self.shown_result
        text = res.copy_text() if res is not None else ""
        if not text:
            self._set_status(tr("Копировать нечего — сначала переведите страницу."), notify=True)
            return
        QApplication.clipboard().setText(text)
        self._set_status(tr("Перевод скопирован: облаков {0}.", len(res.translations())), notify=True)

    def toggle_overlay(self) -> None:
        if self.overlay.isVisible():
            self.overlay.user_hidden = True
            self.overlay.hide()
            self.list_window.hide()
        else:
            self.overlay.user_hidden = False
            mode = self.cfg["display_mode"]
            if mode in ("overlay", "both") and self.region:
                self.overlay.show()
            if mode in ("list", "both"):
                self.list_window.show()
        self._sync_mini()

    def reset_session(self) -> None:
        tid = self.cfg.get("active_title", "")
        if tid:
            titles.clear_context(tid)
        self.pipeline.reset_session()
        self.overlay.clear()
        self.shown_result = None
        self.list_window.set_result(None)
        self._fill_titles()
        self._set_status(tr("Контекст и кэш очищены."))

    def show_history(self) -> None:
        from .history_window import HistoryDialog
        HistoryDialog(self).exec()

    def _remember(self, result: PageResult) -> None:
        """Режим разработчика: записать перевод в историю."""
        if not self.cfg.get("dev_mode"):
            return
        from .. import history
        t = titles.load(self.cfg.get("active_title", ""))
        history.add(result, self.cfg, t.name if t else "")

    def show_help(self) -> None:
        HelpDialog(self, self.cfg["hotkeys"]).exec()

    # ================================================================ докачка OCR
    def check_ocr_language(self) -> None:
        """Если для языка оригинала нет Windows OCR — предложить установить."""
        if sys.platform != "win32" or self.cfg["ocr_engine"] != "windows":
            return
        try:
            from ..win_setup import missing_ocr_languages
            missing = missing_ocr_languages([self.cfg["source_lang"]])
        except Exception:  # noqa: BLE001
            return
        if missing:
            self.offer_ocr_install(missing[0])

    def offer_ocr_install(self, lang: str, force: bool = False) -> None:
        if self._installing or sys.platform != "win32":
            return
        if lang in self._install_declined and not force:
            return
        name = i18n.lower(languages.src(lang)["name"].split(" (")[0])
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Question)
        box.setWindowTitle(tr("Нужно докачать распознавание текста"))
        box.setText(tr("В Windows не установлено распознавание текста для языка: {0}.\n\nПрограмма может докачать "
                       "его сама (это компонент Windows, несколько МБ).\nWindows один раз спросит разрешение "
                       "администратора — нажмите «Да».\nПотом программа перезапустится и права больше не "
                       "понадобятся.", name))
        btn_one = box.addButton(tr("Установить: {0}", name), QMessageBox.ButtonRole.AcceptRole)
        btn_all = box.addButton(tr("Установить все языки"), QMessageBox.ButtonRole.AcceptRole)
        box.addButton(tr("Позже"), QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(btn_one)
        box.exec()
        clicked = box.clickedButton()
        if clicked is btn_one:
            self._start_install([lang])
        elif clicked is btn_all:
            self._start_install(list(languages.SOURCE_LANGUAGES))
        else:
            self._install_declined.add(lang)
            self._set_status(tr("OCR не установлен — перевод не заработает. "
                                "Окно установки появится при следующем переводе."), True)

    def _start_install(self, langs: list[str]) -> None:
        from ..win_setup import install_ocr_languages

        self._installing = True
        self.set_live(False)
        self._set_status(tr("Устанавливаю распознавание текста… Следите за окном PowerShell."))

        def work():
            try:
                code = install_ocr_languages(langs)
            except OSError as e:
                code = str(e)
            self.install_bridge.finished.emit(code, langs)

        threading.Thread(target=work, daemon=True).start()

    def _on_install_finished(self, code, langs) -> None:
        self._installing = False
        if code is None:
            self._set_status(tr("Установка отменена: Windows не получила разрешение администратора."), True)
            return
        if code == 0:
            QMessageBox.information(self, tr("Готово"),
                                    tr("Распознавание текста установлено.\nПрограмма сейчас перезапустится."))
            self.restart_app()
            return
        cap = languages.src(langs[0])["name"].split(" (")[0]
        QMessageBox.warning(
            self, tr("Не получилось установить"),
            tr("Windows не смогла докачать компонент (нужен интернет и работающий Центр "
               "обновления).\n\nМожно установить вручную: Параметры Windows → Время и язык → Язык и "
               "регион → Добавить язык → {0}. Отметьте «Распознавание текста».\nПосле этого "
               "перезапустите программу.", cap)
            + (tr("\n\nОшибка: {0}", code) if isinstance(code, str) else ""))
        self._set_status(tr("OCR не установлен."), True)

    def restart_app(self) -> None:
        from PySide6.QtCore import QProcess

        self.cfg.save()
        if getattr(sys, "frozen", False):
            program, args = sys.executable, []
        else:
            program, args = sys.executable, [str(ROOT / "main.py")]
        QProcess.startDetached(program, args, str(ROOT))
        self.close()

    # ================================================================ закрытие
    def closeEvent(self, e):
        self.cfg["window_size"] = [self.width(), self.height()]
        self.cfg.save()
        self.auto_timer.stop()
        self._stop_hotkeys()
        if self._voice_dl is not None:
            self._voice_dl.cancel()
        self.voice.shutdown()
        if self.mini is not None:
            self.mini.shutdown()
        self.overlay.close()
        self.list_window.close()
        super().closeEvent(e)
        QApplication.quit()


# ================================================================ диалоги
class ResultDialog(QDialog):
    """«Перевести картинку»: сама картинка с плашками перевода и список реплик."""

    def __init__(self, parent, result: PageResult, image: Image.Image | None, cfg):
        super().__init__(parent)
        self.setWindowTitle(tr("Результат"))
        self.resize(760, 820)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(18, 16, 18, 16)
        lay.setSpacing(10)
        lay.addWidget(label(tr("Результат"), "cardTitle"))
        n = sum(1 for b in result.bubbles if b.translation)
        lay.addWidget(caption(tr("Строк: {0} · облаков: {1}", result.line_count, n)
                              + (tr(" · из кэша {0}", result.cache_hits) if result.cache_hits else "")))
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        inner = QWidget()
        il = QVBoxLayout(inner)
        il.setContentsMargins(0, 0, 0, 0)
        il.setSpacing(10)
        if image is not None and n:
            pic = QLabel()
            pic.setAlignment(Qt.AlignmentFlag.AlignCenter)
            pm = _render_translated(image, result, cfg)
            pic.setPixmap(pm.scaledToWidth(min(700, pm.width()), Qt.TransformationMode.SmoothTransformation))
            il.addWidget(pic)
        text = QTextBrowser()
        text.setOpenLinks(False)
        parts = []
        if result.error:
            parts.append(f"<p style='color:{theme.BOW}'>{html.escape(result.error)}</p>")
        for i, b in enumerate(result.bubbles, 1):
            shown = html.escape(b.translation) if b.translation else \
                f"<i>— {html.escape(tr(b.skip_reason or 'нет перевода'))} —</i>"
            tag = f"{b.lang.upper()} · " if b.lang else ""
            parts.append(f"<p><b style='color:{theme.palette().primary}'>{i}.</b> "
                         f"<span style='font-size:11pt'>{shown}</span><br>"
                         f"<span style='color:{theme.palette().text2}'>{tag}{html.escape(b.text)}</span></p>")
        text.setHtml("".join(parts) or tr("<i>Текст не найден.</i>"))
        text.setMinimumHeight(260)
        il.addWidget(text)
        scroll.setWidget(inner)
        lay.addWidget(scroll, 1)
        row = QHBoxLayout()
        row.addStretch(1)
        if result.translations():
            copy = pill(tr("Копировать перевод"), "outline", "copy")

            def do_copy() -> None:
                QApplication.clipboard().setText(result.copy_text())
                copy.setText(tr("Скопировано"))
            copy.clicked.connect(do_copy)
            row.addWidget(copy)
        ok = pill(tr("Готово"), "primary")
        ok.clicked.connect(self.accept)
        row.addWidget(ok)
        lay.addLayout(row)


def _render_translated(image: Image.Image, result: PageResult, cfg) -> QPixmap:
    rgb = image.convert("RGB")
    data = rgb.tobytes()
    qimg = QImage(data, rgb.width, rgb.height, rgb.width * 3, QImage.Format.Format_RGB888).copy()
    p = QPainter(qimg)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setRenderHint(QPainter.RenderHint.TextAntialiasing)
    paint_plates(p, result.bubbles, 1.0, QRect(0, 0, rgb.width, rgb.height), cfg)
    p.end()
    return QPixmap.fromImage(qimg)


class HelpDialog(QDialog):
    def __init__(self, parent, hk: dict):
        super().__init__(parent)
        self.setWindowTitle(tr("Как пользоваться"))
        self.resize(560, 560)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 18, 20, 16)
        lay.setSpacing(12)
        lay.addWidget(label(tr("Как пользоваться"), "cardTitle"))
        steps = [
            tr("Откройте главу в браузере или читалке. Нажмите «Выделить область» ({0}) и обведите "
               "мышкой место, где идёт текст. Область запомнится.", hk.get('select', '')),
            tr("Режим «Скриншот»: нажмите «Перевести» ({0}) — перевод появится прямо поверх облаков. "
               "{1} — показать или скрыть перевод.", hk.get('translate', ''), hk.get('toggle', '')),
            tr("Режим Live: листайте главу, перевод появится сам, когда страница остановится. {0} — "
               "пауза.", hk.get('auto', '')),
            tr("Создайте тайтл для каждой манхвы: переводчик будет помнить имена и прошлые реплики даже после перезапуска."),
            tr("Скопировать перевод страницы — {0} или значок в компактной панели. В окне со списком отдельная "
               "реплика копируется щелчком.", hk.get('copy') or "—"),
            tr("Если текст не находится на тёмном или пёстром фоне — в «Тонкой настройке» выберите режим "
               "распознавания «Тщательный» или «Максимальный»."),
            tr("Для чата в онлайн-игре — вкладка «Голос»: скажите фразу ({0}), и перевод окажется в буфере "
               "обмена — вставьте его в чат (Ctrl+V). Игровые слова и ники добавьте в «Словарь для чата».",
               hk.get("voice") or "—"),
        ]
        for i, s in enumerate(steps, 1):
            row = QHBoxLayout()
            num = QLabel(str(i))
            num.setFixedSize(26, 26)
            num.setAlignment(Qt.AlignmentFlag.AlignCenter)
            c = theme.palette()
            num.setStyleSheet(f"background: {c.primary}; color: {c.on_primary}; border-radius: 13px; font-weight: 700;")
            row.addWidget(num, 0, Qt.AlignmentFlag.AlignTop)
            row.addWidget(label(s), 1)
            lay.addLayout(row)
        lay.addStretch(1)
        ok = pill(tr("Понятно"), "primary")
        ok.clicked.connect(self.accept)
        lay.addWidget(ok, 0, Qt.AlignmentFlag.AlignRight)


# ================================================================ запуск
def _excepthook(exc_type, exc, tb):
    text = "".join(traceback.format_exception(exc_type, exc, tb))
    try:
        (DATA_ROOT / "error.log").write_text(text, encoding="utf-8")
    except OSError:
        pass
    sys.__excepthook__(exc_type, exc, tb)


def create_app() -> tuple[QApplication, Config]:
    app = QApplication.instance() or QApplication(sys.argv)
    app.setApplicationName("ATlate")
    icon = theme.resource("icon.png")
    if icon.exists():
        app.setWindowIcon(QIcon(str(icon)))
    cfg = Config()
    theme.apply(app, cfg.get("theme", "system"))
    return app, cfg


def run_gui() -> int:
    sys.excepthook = _excepthook
    app, cfg = create_app()
    app.setQuitOnLastWindowClosed(False)
    win = MainWindow(cfg)
    if cfg.get("mini_mode"):
        win.enter_mini()        # в прошлый раз программу закрыли в компактном режиме
    else:
        win.show()
    return app.exec()


__all__ = ["MainWindow", "run_gui", "create_app"]
