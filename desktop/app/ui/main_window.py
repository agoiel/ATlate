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

from .. import __version__, languages, titles
from ..capture import Region, grab_region
from ..config import GLOSSARY_DIR, ROOT, Config
from ..glossary import Glossary, list_glossaries, universal_for
from ..models import PageResult
from ..pipeline import Pipeline
from . import theme
from .glossary_editor import GlossaryEditor, new_glossary_file
from .list_window import ListWindow
from .overlay import OverlayWindow, paint_plates
from .selector import SelectionWindow
from .widgets import (Card, Columns, Dropdown, Field, FlowLayout, Hero, ResponsiveRow, Segmented, StatusChip,
                      ScrollContent, Select, Stepper, SwitchRow, Tile, caption, label, pill, relayout, round_pixmap,
                      set_pill)

# Описание переводчиков для списка (как в Android-версии)
TRANSLATOR_INFO = [
    ("google_free", "Google", "Без ключа · среднее качество", "Бесплатно, без ключа"),
    ("yandex", "Яндекс", "Без ключа · хорошо знает русский", "Бесплатно, без ключа"),
    ("duckduckgo", "DuckDuckGo", "Без ключа · переводит через Bing", "Бесплатно, без ключа"),
    ("lingva", "Lingva", "Без ключа · Google через посредника, выручает при VPN", "Бесплатно, без ключа"),
    ("deepl", "DeepL", "Ключ · 500 тыс. символов в месяц бесплатно", "С ключом"),
    ("microsoft", "Microsoft", "Ключ · 2 млн символов в месяц бесплатно", "С ключом"),
    ("papago", "Papago", "Ключ Naver Cloud · лучший для корейского", "С ключом"),
    ("gemini", "Gemini", "ИИ · есть бесплатный лимит", "ИИ — понимают контекст"),
    ("claude", "Claude", "ИИ · естественный живой перевод", "ИИ — понимают контекст"),
    ("openai", "OpenAI-совместимый", "ИИ · OpenAI, OpenRouter, DeepSeek, Ollama, LM Studio", "ИИ — понимают контекст"),
]


# ================================================================ фоновая работа
class _Signals(QObject):
    done = Signal(object, float, object)    # PageResult, k, токен задачи
    failed = Signal(str)


class _Job(QRunnable):
    def __init__(self, pipeline: Pipeline, image: Image.Image, k: float, token, live: bool = False):
        super().__init__()
        self.pipeline, self.image, self.k, self.token, self.live = pipeline, image, k, token, live
        self.signals = _Signals()

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

    def __init__(self, pipeline: Pipeline, image: Image.Image):
        super().__init__()
        self.pipeline, self.image = pipeline, image
        self.signals = _CompareSignals()

    def run(self):
        try:
            text = self.pipeline.compare(self.image)
        except Exception:  # noqa: BLE001
            text = "Ошибка сравнения:\n" + traceback.format_exc()
        self.signals.done.emit(text)


DEV_LOG = ROOT / "dev_log.txt"


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
    def __init__(self, cfg: Config):
        super().__init__(None, Qt.WindowType.Window)
        self.cfg = cfg
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
        self._jobs: set[_Job] = set()
        self.live_on = False
        self._file_image: Image.Image | None = None

        self.setWindowTitle(f"agolate {__version__}")
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
        bl.addWidget(self._build_tiles())
        bl.addWidget(self._build_studio_banner())
        self.columns = Columns([self._build_languages(), self._build_translator(), self._build_title(),
                                self._build_tuning(), self._build_hotkeys(), self._build_developer()])
        bl.addWidget(self.columns)
        foot = caption(f"agolate {__version__} · распознавание: "
                       f"{'Windows OCR' if self.cfg['ocr_engine'] == 'windows' else 'EasyOCR'}")
        foot.setAlignment(Qt.AlignmentFlag.AlignCenter)
        bl.addWidget(foot)
        v.addWidget(body)
        v.addStretch(1)
        old = self.scroll.takeWidget()
        self.scroll.setWidget(content)
        if old is not None:
            old.deleteLater()

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
        t = QLabel("agolate")
        t.setStyleSheet("color: white; font-size: 20pt; font-weight: 700; background: transparent;")
        sub = QLabel("Перевод манхвы прямо с экрана")
        sub.setStyleSheet(f"color: {theme.CREAM}; background: transparent;")
        sub.setWordWrap(True)
        names.addWidget(t)
        names.addWidget(sub)
        brand.addLayout(names, 1)
        self.pin = pill("", "heroOutline", "pin", "Окно поверх всех окон")
        self.pin.setCheckable(True)
        self.pin.setChecked(bool(self.cfg.get("always_on_top", False)))
        self.pin.setFixedSize(QSize(44, 40))
        self.pin.toggled.connect(self._toggle_on_top)
        brand.addWidget(self.pin, 0, Qt.AlignmentFlag.AlignTop)
        lay.addLayout(brand)

        self.chip = StatusChip()
        lay.addWidget(self.chip, 0, Qt.AlignmentFlag.AlignLeft)

        self.mode_switch = Segmented([("shot", "Скриншот"), ("live", "Live")], self.cfg.get("translate_mode", "shot"))
        self.mode_switch.setMaximumWidth(360)
        self.mode_switch.changed.connect(self._set_mode)
        lay.addWidget(self.mode_switch)

        flow_host = QWidget()
        flow_host.setObjectName("transparent")
        flow = FlowLayout(10)
        flow_host.setLayout(flow)
        hk = self.cfg["hotkeys"]
        self.btn_main = pill("", "hero", "play")
        self.btn_main.clicked.connect(self._main_action)
        self.btn_select = pill("Выделить область", "heroOutline", "select", f"Горячая клавиша: {hk.get('select', '')}")
        self.btn_select.clicked.connect(self.start_selection)
        self.btn_zone = pill("Показать зону", "heroOutline", "eye", "Показать рамку текущей зоны перевода")
        self.btn_zone.clicked.connect(self.show_zone)
        self.btn_hide = pill("Скрыть перевод", "heroOutline", "hide", f"Горячая клавиша: {hk.get('toggle', '')}")
        self.btn_hide.clicked.connect(self.toggle_overlay)
        for b in (self.btn_main, self.btn_select, self.btn_zone, self.btn_hide):
            flow.addWidget(b)
        lay.addWidget(flow_host)

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
            Tile("Перевести\nкартинку", "image", "Перевести сохранённый скриншот или страницу"),
            Tile("Новый\nтайтл", "refresh", "Создать тайтл со своим контекстом и глоссарием"),
            Tile("Окно\nсо списком", "list", "Оригинал и перевод каждой реплики списком"),
            Tile("Как\nпользоваться", "help", "Короткая инструкция"),
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
        col.addWidget(label("Студия перевода", "cardTitle"))
        col.addWidget(caption("Перевести главу целиком: найти текст, стереть оригинал, вписать перевод и выгрузить "
                              "готовые страницы"))
        lay.addLayout(col, 1)
        btn = pill("Открыть", "primary", "brush")
        btn.clicked.connect(self.open_studio)
        lay.addWidget(btn)
        return box

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
        card = Card("Языки", "Откуда и куда переводить", "lang")
        self.src_combo = self._combo([(k, v["name"]) for k, v in languages.SOURCE_LANGUAGES.items()],
                                     self.cfg["source_lang"], self._set_source)
        self.tgt_combo = self._combo([(k, v["name"]) for k, v in languages.TARGET_LANGUAGES.items()],
                                     self.cfg["target_lang"], lambda v: self._set("target_lang", v))
        arrow = QLabel()
        arrow.setPixmap(theme.pixmap("arrow", theme.palette().primary, 20))
        card.add(ResponsiveRow(self._labeled("С языка", self.src_combo), arrow,
                               self._labeled("На язык", self.tgt_combo), anchor=self.src_combo.header))
        content = Dropdown("Что переводим", [
            ("manhwa", "Манхва, манга, маньхуа", "Облака с репликами, звуки, плашки", ""),
            ("screen", "Текст на экране", "Статьи, чаты, игры, приложения — перевод прямо поверх текста", ""),
        ], self.cfg.get("content_mode", "manhwa"))
        content.changed.connect(lambda v: self._set("content_mode", v))
        card.add(content)
        return card

    # ---------------------------------------------------------------- переводчик
    def _build_translator(self) -> Card:
        card = Card("Переводчик", "Чем переводить текст", "bolt")
        dd = Dropdown("Переводчик", TRANSLATOR_INFO, self.cfg["translator"])
        dd.changed.connect(self._set_translator)
        card.add(dd)
        self.keys_box = QWidget()
        self.keys_lay = QVBoxLayout(self.keys_box)
        self.keys_lay.setContentsMargins(0, 0, 0, 0)
        self.keys_lay.setSpacing(8)
        card.add(self.keys_box)
        self._render_keys()
        ctx = Stepper("Контекст", "Сколько прошлых реплик учитывать", float(self.cfg["context_size"]),
                      1, 0, 40, lambda v: str(int(v)))
        ctx.changed.connect(lambda v: self._set("context_size", int(v)))
        card.add(ctx)
        pause = Stepper("Пауза между запросами", "Защищает от блокировки за частые запросы",
                        float(self.cfg.get("request_interval", 1.0)), 0.5, 0, 10, lambda v: f"{v:.1f} с")
        pause.changed.connect(lambda v: self._set("request_interval", v))
        card.add(pause)
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
            note("Ключ не нужен. Это неофициальный способ: при VPN Google часто ограничивает запросы — "
                 "тогда выберите Lingva или Яндекс.")
        elif name == "yandex":
            note("Работает без ключа. Если бесплатный способ перестанет отвечать, укажите ключ Yandex Cloud — "
                 "тогда перевод пойдёт через официальный API (платно, по символам).")
            field("API-ключ Yandex Cloud (необязательно)", "api_key", "AQVN…", secret=True)
            field("ID каталога (нужен вместе с ключом)", "folder_id", "b1g…")
            link("Как получить ключ", "https://yandex.cloud/ru/docs/translate/quickstart")
        elif name == "duckduckgo":
            note("Ключ не нужен. DuckDuckGo переводит через Microsoft Bing. Способ неофициальный "
                 "и может временно не отвечать.")
        elif name == "lingva":
            note("Ключ не нужен. Lingva — открытый посредник к Google Переводчику: Google видит запрос "
                 "от сервера Lingva, а не от вас, поэтому ограничения VPN обычно не мешают.")
            field("Адрес сервера Lingva", "base_url", "https://lingva.ml", default="https://lingva.ml")
            link("Список других серверов", "https://github.com/thedaviddelta/lingva-translate#instances")
        elif name == "deepl":
            field("API-ключ DeepL", "api_key", "xxxxxxxx-xxxx:fx", secret=True)
            link("Получить бесплатный ключ", "https://www.deepl.com/pro-api")
        elif name == "microsoft":
            field("Ключ Azure Translator", "api_key", "ключ ресурса", secret=True)
            field("Регион", "region", "global", default="global")
            link("Открыть портал Azure", "https://portal.azure.com")
        elif name == "papago":
            note("Переводчик Naver — лучший для корейского. Нужна регистрация в Naver Cloud Platform. "
                 "На русский Papago переводит только с корейского и английского.")
            field("Client ID", "client_id", secret=True)
            field("Client Secret", "client_secret", secret=True)
            link("Открыть Naver Cloud Platform", "https://www.ncloud.com/product/aiService/papagoTranslation")
        elif name == "gemini":
            field("API-ключ Gemini", "api_key", "AIza…", secret=True)
            field("Модель", "model", "gemini-2.5-flash-lite", default="gemini-2.5-flash-lite")
            link("Получить ключ в Google AI Studio", "https://aistudio.google.com/apikey")
        elif name == "claude":
            field("API-ключ Claude", "api_key", "sk-ant-…", secret=True)
            field("Модель", "model", "claude-haiku-5-5", default="claude-haiku-5-5")
            link("Получить ключ", "https://platform.claude.com")
        elif name == "openai":
            note("Подходит для OpenAI, OpenRouter, DeepSeek и бесплатных локальных моделей: "
                 "Ollama (http://localhost:11434/v1) или LM Studio (http://localhost:1234/v1).")
            field("Адрес сервера", "base_url", "https://api.openai.com/v1")
            field("API-ключ", "api_key", "sk-…", secret=True)
            field("Модель", "model", "gpt-4o-mini")
        relayout(self.keys_box)

    # ---------------------------------------------------------------- тайтл и глоссарий
    def _build_title(self) -> Card:
        card = Card("Тайтл", "Контекст и глоссарий сохраняются для каждой манхвы", "book")
        self.title_combo = Select()
        self.title_combo.currentIndexChanged.connect(self._on_title_combo)
        card.add(self._labeled("Активный тайтл", self.title_combo))
        row = FlowLayout(8)
        host = QWidget()
        host.setLayout(row)
        manage = pill("Управление", "outline")
        manage.clicked.connect(lambda: self._title_menu(manage))
        new_t = pill("Новый тайтл", "tonal", "plus")
        new_t.clicked.connect(self.create_title)
        row.addWidget(manage)
        row.addWidget(new_t)
        card.add(host)

        self.gloss_combo = Select()
        self.gloss_combo.currentIndexChanged.connect(self._on_gloss_combo)
        card.add(self._labeled("Глоссарий", self.gloss_combo))
        grow = FlowLayout(8)
        ghost = QWidget()
        ghost.setLayout(grow)
        edit = pill("Изменить", "outline")
        edit.clicked.connect(self.edit_glossary)
        new_g = pill("Новый", "tonal", "plus")
        new_g.clicked.connect(self.new_glossary)
        folder = pill("Папка", "tonal", "folder", "Открыть папку с глоссариями")
        folder.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(GLOSSARY_DIR))))
        for b in (edit, new_g, folder):
            grow.addWidget(b)
        card.add(ghost)
        self._fill_titles()
        return card

    def _fill_titles(self) -> None:
        self._filling = True
        self.title_combo.clear()
        self.title_combo.addItem("Без тайтла — контекст только до выхода", "")
        for t in titles.list_titles():
            self.title_combo.addItem(f"{t.name} · {len(t.history)} реплик", t.id)
        idx = self.title_combo.findData(self.cfg.get("active_title", ""))
        if idx < 0:
            self.cfg["active_title"] = ""
            idx = 0
        self.title_combo.setCurrentIndex(idx)
        t = titles.load(self.cfg.get("active_title", ""))
        if t and t.glossary != self.cfg.get("glossary"):
            self.cfg["glossary"] = t.glossary
        self.gloss_combo.clear()
        self.gloss_combo.addItem("Без глоссария", "")
        for p in list_glossaries(GLOSSARY_DIR):
            try:
                g = Glossary.load(p)
                self.gloss_combo.addItem(f"{g.name or p.stem} · {len(g.entries)} зап.", p.name)
            except (OSError, ValueError):
                continue
        self.gloss_combo.setCurrentIndex(max(0, self.gloss_combo.findData(self.cfg.get("glossary", ""))))
        self._filling = False

    def _on_title_combo(self, _i: int) -> None:
        if getattr(self, "_filling", False):
            return
        tid = self.title_combo.currentData() or ""
        self.cfg["active_title"] = tid
        t = titles.load(tid)
        if t:
            self.cfg["glossary"] = t.glossary
        self._save_and_reload()
        self._fill_titles()
        self._refresh_status()

    def _on_gloss_combo(self, _i: int) -> None:
        if getattr(self, "_filling", False):
            return
        self.cfg["glossary"] = self.gloss_combo.currentData() or ""
        t = titles.load(self.cfg.get("active_title", ""))
        if t:
            t.glossary = self.cfg["glossary"]
            titles.save(t)
        self._save_and_reload()

    def _title_menu(self, anchor: QWidget) -> None:
        t = titles.load(self.cfg.get("active_title", ""))
        m = QMenu(self)
        if t is None:
            m.addAction("Создать тайтл", self.create_title)
            m.addAction("Очистить контекст", self.reset_session)
        else:
            m.addAction("Переименовать", self.rename_title)
            m.addAction("Очистить контекст", self.reset_session)
            m.addSeparator()
            m.addAction("Удалить тайтл", self.delete_title)
        m.exec(anchor.mapToGlobal(anchor.rect().bottomLeft()))

    def create_title(self) -> None:
        name, ok = QInputDialog.getText(self, "Новый тайтл", "Название тайтла:")
        if not ok or not name.strip():
            return
        t = titles.create(name, universal_for(GLOSSARY_DIR, self.cfg["source_lang"]))
        self.cfg["active_title"] = t.id
        self.cfg["glossary"] = t.glossary
        self._save_and_reload()
        self._fill_titles()
        self._set_status(f"Тайтл «{t.name}» создан. Контекст будет сохраняться между запусками.")

    def rename_title(self) -> None:
        t = titles.load(self.cfg.get("active_title", ""))
        if not t:
            return
        name, ok = QInputDialog.getText(self, "Переименовать", "Название тайтла:", text=t.name)
        if ok and name.strip():
            t.name = name.strip()
            titles.save(t)
            self._fill_titles()

    def delete_title(self) -> None:
        t = titles.load(self.cfg.get("active_title", ""))
        if not t:
            return
        if QMessageBox.question(self, "Удалить тайтл?",
                                f"Сохранённые реплики и кэш переводов «{t.name}» будут удалены. "
                                "Глоссарий останется.") != QMessageBox.StandardButton.Yes:
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
        card = Card("Тонкая настройка", "Распознавание, вид перевода, оформление", "tune",
                    collapsible=True, collapsed=True)
        rec = Dropdown("Распознавание текста", [
            ("fast", "Быстрый", "1 проход · для обычных облаков", ""),
            ("thorough", "Тщательный", "3 прохода · тёмные фоны, светлый текст", ""),
            ("max", "Максимальный", "5 проходов · пёстрые фоны, цветной текст", ""),
        ], c.get("ocr_mode", "thorough"))
        rec.changed.connect(lambda v: self._set("ocr_mode", v))
        card.add(rec)
        card.add(caption("Если текст на тёмном или пёстром фоне не находится, выберите режим потщательнее."))

        grid = QGridLayout()
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(8)
        self.disp_combo = self._combo([("overlay", "Поверх облаков"), ("list", "Окно со списком"),
                                       ("both", "И то, и другое")], c["display_mode"],
                                      lambda v: self._set("display_mode", v))
        engine = self._combo([("windows", "Windows OCR"), ("easyocr", "EasyOCR")], c["ocr_engine"],
                             self._set_engine)
        themes = self._combo([("system", "Как в Windows"), ("light", "Светлая"), ("dark", "Тёмная")],
                             c.get("theme", "system"), self._set_theme)
        grid.addWidget(self._labeled("Показ перевода", self.disp_combo), 0, 0)
        grid.addWidget(self._labeled("Движок OCR", engine), 0, 1)
        grid.addWidget(self._labeled("Тема", themes), 1, 0)
        card.add(grid)

        for title, sub, key, val, step, lo, hi, fmt in (
            ("Склейка строк", "Больше — охотнее собирает строки в одно облако", "bubble_gap",
             float(c["bubble_gap"]), 0.1, 0.2, 4.0, lambda v: f"{v:.1f}"),
            ("Увеличение перед OCR", "Мелкий текст читается лучше", "ocr_upscale",
             float(c["ocr_upscale"]), 0.25, 1.0, 4.0, lambda v: f"×{v:.2f}"),
            ("Шрифт перевода", "Максимальный размер", "overlay_max_font_size",
             float(c["overlay_max_font_size"]), 1, 8, 48, lambda v: f"{int(v)} px"),
            ("Непрозрачность плашек", "", "overlay_opacity",
             float(c["overlay_opacity"]), 0.05, 0.3, 1.0, lambda v: f"{int(v * 100)} %"),
            ("Live: проверка экрана", "Как часто смотреть, остановилась ли страница", "auto_interval_ms",
             float(c["auto_interval_ms"]), 100, 300, 5000, lambda v: f"{int(v)} мс"),
        ):
            st = Stepper(title, sub, val, step, lo, hi, fmt)
            st.changed.connect(lambda v, k=key: self._set(k, int(v) if k in ("overlay_max_font_size",
                                                                                "auto_interval_ms") else v))
            card.add(st)

        for title, sub, key in (
            ("Не переводить звуки", "Текст поверх рисунка, а не в облаке", "skip_free_text"),
            ("Пропускать обрезанное", "Текст, который режет край области", "skip_cut_bubbles"),
            ("Разделять облака по контуру", "Не склеивать облака, стоящие вплотную", "bubble_border_check"),
            ("Выделять по всей ширине", "Проводите мышкой только сверху вниз — ширина берётся вся",
             "full_width_select"),
        ):
            sw = SwitchRow(title, sub, bool(c.get(key, False)))
            sw.toggled.connect(lambda v, k=key: self._set(k, v))
            card.add(sw)
        return card

    # ---------------------------------------------------------------- горячие клавиши
    def _build_hotkeys(self) -> Card:
        card = Card("Горячие клавиши", "Работают, даже когда окно свёрнуто", "keyboard",
                    collapsible=True, collapsed=True)
        hk = self.cfg["hotkeys"]
        grid = QGridLayout()
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(8)
        for i, (key, title) in enumerate((("select", "Выделить область"), ("translate", "Перевести"),
                                          ("toggle", "Показать или скрыть перевод"), ("auto", "Live вкл/выкл"))):
            f = Field(title, hk.get(key, ""), "<alt>+q")
            f.edited.connect(lambda v, k=key: self._set_hotkey(k, v))
            grid.addWidget(f, i // 2, i % 2)
        card.add(grid)
        card.add(caption("Формат: <alt>+q, <ctrl>+<shift>+t, <f8>. Пустое поле — клавиша отключена."))
        return card

    # ---------------------------------------------------------------- для разработчиков
    def _build_developer(self) -> Card:
        c = self.cfg
        card = Card("Для разработчиков", "Отладочная информация и проверка скорости", "code",
                    collapsible=True, collapsed=True)
        dev = SwitchRow("Режим разработчика", "Время каждого этапа, проходы распознавания, рамки строк и журнал",
                        bool(c.get("dev_mode")))
        card.add(dev)
        par = SwitchRow("Параллельное распознавание",
                        "Проходы распознавания идут одновременно — быстрее на многоядерных процессорах",
                        bool(c.get("parallel_ocr", True)))
        par.toggled.connect(lambda v: self._set("parallel_ocr", v))
        card.add(par)

        extra = QWidget()
        el = QVBoxLayout(extra)
        el.setContentsMargins(0, 0, 0, 0)
        el.setSpacing(10)
        boxes = SwitchRow("Рамки распознанных строк", "Поверх страницы, своим цветом для каждого прохода",
                          bool(c.get("debug_boxes", True)))
        boxes.toggled.connect(lambda v: self._set("debug_boxes", v))
        el.addWidget(boxes)
        el.addWidget(label("ПОСЛЕДНИЙ ЗАПУСК", "fieldLabel"))
        self.dev_report = QPlainTextEdit()
        self.dev_report.setReadOnly(True)
        self.dev_report.setPlaceholderText("Здесь появятся подробности после перевода.")
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
        cmp_btn = pill("Сравнить на текущей зоне", "outline", "bolt",
                       "Распознать зону по очереди и параллельно: что быстрее и совпал ли результат")
        cmp_btn.clicked.connect(self.compare_now)
        fl.addWidget(cmp_btn)
        copy_btn = pill("Копировать журнал", "tonal", "link")
        copy_btn.clicked.connect(self._copy_dev_log)
        fl.addWidget(copy_btn)
        open_btn = pill("Открыть журнал", "tonal", "folder")
        open_btn.clicked.connect(lambda: DEV_LOG.exists() and QDesktopServices.openUrl(QUrl.fromLocalFile(str(DEV_LOG))))
        fl.addWidget(open_btn)
        clear_btn = pill("Очистить", "tonal", "refresh")
        clear_btn.clicked.connect(self._clear_dev_log)
        fl.addWidget(clear_btn)
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
            self._set_status("Журнал пуст: включите режим разработчика и выполните перевод.")
            return
        QApplication.clipboard().setText(text)
        self._set_status("Журнал скопирован.")

    def _clear_dev_log(self) -> None:
        try:
            DEV_LOG.unlink()
        except OSError:
            pass
        self._set_status("Журнал очищен.")

    def _show_dev(self, text: str) -> None:
        if getattr(self, "dev_report", None) is not None:
            self.dev_report.setPlainText(text)
        dev_log(text)

    def compare_now(self) -> None:
        if self.region is None:
            self._set_status("Область ещё не выделена.", True)
            return
        if self.busy:
            return
        self.busy = True
        self._set_status("Сравниваю… это займёт несколько секунд")
        hidden = self.overlay.isVisible() and not self.overlay.excluded
        if hidden:
            self.overlay.hide()

        def go():
            image, _k = grab_region(self.region)
            if hidden and not self.overlay.user_hidden:
                self.overlay.show()
            job = _CompareJob(self.pipeline, image)
            job.signals.done.connect(self._on_compared)
            self._jobs.add(job)
            job.setAutoDelete(False)
            self.pool.start(job)

        QTimer.singleShot(120 if hidden else 0, go)

    def _on_compared(self, text: str) -> None:
        self.busy = False
        self._jobs.clear()
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
        self._refresh_status()

    def _set_key(self, translator: str, key: str, value: str) -> None:
        self.cfg["translators"].setdefault(translator, {})[key] = value
        self._save_and_reload()

    def _set_translator(self, name: str) -> None:
        self._set("translator", name)
        self._render_keys()

    def _set_source(self, lang: str) -> None:
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
        QTimer.singleShot(200, self.check_ocr_language)

    def _set_engine(self, engine: str) -> None:
        self._set("ocr_engine", engine)
        QTimer.singleShot(200, self.check_ocr_language)

    def _set_theme(self, mode: str) -> None:
        self.cfg["theme"] = mode
        self.cfg.save()
        theme.apply(QApplication.instance(), mode)
        QTimer.singleShot(0, self._rebuild)

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
        live = self.cfg.get("translate_mode", "shot") == "live"
        hk = self.cfg["hotkeys"]
        if self.region:
            area = f"Область {self.region.w}×{self.region.h}"
        else:
            area = "Область не выделена"
        if live:
            self.chip.set(self.live_on, f"{area} · Live {'работает' if self.live_on else 'на паузе'}")
            set_pill(self.btn_main, "Пауза Live" if self.live_on else "Включить Live", "hero",
                     "stop" if self.live_on else "play")
            self.hint.setText("Live: листайте главу — перевод появится сам, когда страница остановится. "
                              f"{hk.get('auto', '')} — пауза, {hk.get('select', '')} — новая область.")
        else:
            self.chip.set(bool(self.region), area)
            set_pill(self.btn_main, "Перевести", "hero", "play")
            self.btn_main.setToolTip(f"Горячая клавиша: {hk.get('translate', '')}")
            self.hint.setText(f"{hk.get('select', '')} — выделить область · {hk.get('translate', '')} — перевести · "
                              f"{hk.get('toggle', '')} — показать или скрыть перевод · "
                              f"{hk.get('auto', '')} — Live")
        self.mode_switch.select("live" if live else "shot")
        self.btn_zone.setEnabled(self.region is not None)

    def _set_status(self, text: str, error: bool = False) -> None:
        color = "#FFB4A8" if error else theme.CREAM
        self.status.setStyleSheet(f"color: {color}; background: transparent; font-weight: 600;")
        self.status.setText(text)

    # ================================================================ горячие клавиши
    def _start_hotkeys(self) -> None:
        self._stop_hotkeys()
        try:
            from pynput import keyboard
        except Exception:  # noqa: BLE001 — нет pynput или нет экрана
            return
        mapping = {}
        for action, combo in self.cfg["hotkeys"].items():
            if combo:
                mapping[combo] = (lambda a=action: self.bridge.fired.emit(a))
        if not mapping:
            return
        try:
            self._hotkeys = keyboard.GlobalHotKeys(mapping)
            self._hotkeys.daemon = True
            self._hotkeys.start()
        except Exception as e:  # noqa: BLE001
            self._hotkeys = None
            self._set_status(f"Ошибка в горячих клавишах: {e}", True)

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

    def _main_action(self) -> None:
        if self.cfg.get("translate_mode") == "live":
            self.set_live(not self.live_on)
        else:
            self.translate_now()

    # ================================================================ выделение и зона
    def start_selection(self) -> None:
        if self.selector is not None:
            return
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
        self._last_thumb = None
        self._refresh_status()
        if self.cfg.get("translate_mode") == "live":
            self.set_live(True)
        else:
            QTimer.singleShot(50, self.translate_now)

    def show_zone(self) -> None:
        if not self.region:
            self._set_status("Область ещё не выделена.", True)
            return
        self.overlay.place(self.region)
        self.overlay.show_frame = True
        self.overlay.user_hidden = False
        self.overlay.show()
        self.overlay.update()
        self.zone_timer.start(2500)

    def _hide_zone(self) -> None:
        self.overlay.show_frame = False
        self.overlay.update()

    # ================================================================ перевод
    def translate_now(self) -> None:
        if self.region is None:
            self.start_selection()
            return
        if self.busy:
            return
        # Если окно перевода не скрыто от снимков — прячем его на время снимка
        if self.overlay.isVisible() and not self.overlay.excluded:
            self.overlay.hide()
            QTimer.singleShot(120, lambda: self._grab_and_run(restore=True))
        else:
            self._grab_and_run(restore=False)

    def _grab_and_run(self, restore: bool) -> None:
        image, k = grab_region(self.region)
        if restore and not self.overlay.user_hidden:
            self.overlay.show()
        self._run(image, k)

    def _run(self, image: Image.Image, k: float, token=None, live: bool = False) -> None:
        self.busy = True
        self._token += 1
        self._set_status("Распознаю и перевожу…")
        job = _Job(self.pipeline, image, k, token if token is not None else self._token, live)
        job.signals.done.connect(self._on_done)
        job.signals.failed.connect(self._on_failed)
        self._jobs.add(job)
        job.setAutoDelete(False)
        self.pool.start(job)

    def _on_done(self, result: PageResult, k: float, token) -> None:
        self.busy = False
        self._jobs.clear()
        if token == "file":
            self._report(result)
            self._show_file_result(result)
            return
        mode = self.cfg["display_mode"]
        if mode in ("overlay", "both") and self.region:
            self.overlay.set_result(result, k)
        if mode in ("list", "both"):
            self.list_window.set_result(result)
            if not self.list_window.isVisible():
                self.list_window.show()
        self._report(result)
        self._fill_titles_counts()

    def _fill_titles_counts(self) -> None:
        """Обновить число сохранённых реплик у активного тайтла."""
        t = titles.load(self.cfg.get("active_title", ""))
        i = self.title_combo.currentIndex()
        if t and i > 0:
            self.title_combo.setItemText(i, f"{t.name} · {len(t.history)} реплик")

    def _on_failed(self, tb: str) -> None:
        self.busy = False
        self._jobs.clear()
        self._set_status("Непредвиденная ошибка, подробности в error.log", True)
        (ROOT / "error.log").write_text(tb, encoding="utf-8")

    def _report(self, result: PageResult) -> None:
        if self.cfg.get("dev_mode") and result.debug is not None:
            self._show_dev(result.debug.report())
        if result.missing_ocr_lang:
            self._set_status(result.error.split("\n")[0], True)
            self.offer_ocr_install(result.missing_ocr_lang, force=True)
            return
        if result.error:
            if "\n" in result.error:
                QMessageBox.warning(self, "agolate", result.error)
            self._set_status(result.error.split("\n")[0], True)
            return
        n = sum(1 for b in result.bubbles if b.translation)
        parts = [f"Переведено облаков: {n}"]
        # технические подробности — только в режиме разработчика
        if self.cfg.get("dev_mode") and result.debug is not None:
            parts.append(result.debug.summary())
        if not result.bubbles:
            parts = ["Текст не найден. Попробуйте режим распознавания «Тщательный» или «Максимальный»."]
        self._set_status(" · ".join(parts))

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
            self._set_status("Live: перевожу, когда страница перестаёт двигаться.")
        else:
            self.auto_timer.stop()
            self._set_status("Live на паузе.")
        self._refresh_status()

    def _auto_tick(self) -> None:
        if self.busy or self.region is None or self.selector is not None:
            return
        hidden = False
        if self.overlay.isVisible() and not self.overlay.excluded:
            # Без скрытия от снимков приходится прятать окно — будет лёгкое мигание
            self.overlay.hide()
            QApplication.processEvents()
            hidden = True
        image, k = grab_region(self.region)
        if hidden and not self.overlay.user_hidden:
            self.overlay.show()
        thumb = np.asarray(image.convert("L").resize((64, max(1, int(64 * image.height / max(1, image.width))))),
                           dtype=np.int16)
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
            self._run(image, k, live=True)

    # ================================================================ картинка из файла
    def translate_file(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Картинка", "", "Изображения (*.png *.jpg *.jpeg *.webp *.bmp)")
        if not path or self.busy:
            return
        try:
            image = Image.open(path).convert("RGB")
        except OSError as e:
            self._set_status(f"Не удалось открыть: {e}", True)
            return
        self._file_image = image
        self._run(image, 1.0, token="file")

    def _show_file_result(self, result: PageResult) -> None:
        dlg = ResultDialog(self, result, self._file_image, self.cfg)
        dlg.exec()

    def _show_list(self) -> None:
        self.list_window.show()
        self.list_window.raise_()

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

    def reset_session(self) -> None:
        tid = self.cfg.get("active_title", "")
        if tid:
            titles.clear_context(tid)
        self.pipeline.reset_session()
        self.overlay.clear()
        self.list_window.set_result(None)
        self._fill_titles()
        self._set_status("Контекст и кэш очищены.")

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
        name = languages.src(lang)["name"].split(" (")[0].lower()
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Question)
        box.setWindowTitle("Нужно докачать распознавание текста")
        box.setText(f"В Windows не установлено распознавание текста для языка: {name}.\n\n"
                    "Программа может докачать его сама (это компонент Windows, несколько МБ).\n"
                    "Windows один раз спросит разрешение администратора — нажмите «Да».\n"
                    "Потом программа перезапустится и права больше не понадобятся.")
        btn_one = box.addButton(f"Установить: {name}", QMessageBox.ButtonRole.AcceptRole)
        btn_all = box.addButton("Установить все языки", QMessageBox.ButtonRole.AcceptRole)
        box.addButton("Позже", QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(btn_one)
        box.exec()
        clicked = box.clickedButton()
        if clicked is btn_one:
            self._start_install([lang])
        elif clicked is btn_all:
            self._start_install(list(languages.SOURCE_LANGUAGES))
        else:
            self._install_declined.add(lang)
            self._set_status("OCR не установлен — перевод не заработает. "
                             "Окно установки появится при следующем переводе.", True)

    def _start_install(self, langs: list[str]) -> None:
        from ..win_setup import install_ocr_languages

        self._installing = True
        self.set_live(False)
        self._set_status("Устанавливаю распознавание текста… Следите за окном PowerShell.")

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
            self._set_status("Установка отменена: Windows не получила разрешение администратора.", True)
            return
        if code == 0:
            QMessageBox.information(self, "Готово",
                                    "Распознавание текста установлено.\nПрограмма сейчас перезапустится.")
            self.restart_app()
            return
        cap = languages.src(langs[0])["name"].split(" (")[0]
        QMessageBox.warning(
            self, "Не получилось установить",
            "Windows не смогла докачать компонент (нужен интернет и работающий Центр обновления).\n\n"
            "Можно установить вручную: Параметры Windows → Время и язык → Язык и регион → "
            f"Добавить язык → {cap}. Отметьте «Распознавание текста».\n"
            "После этого перезапустите программу."
            + (f"\n\nОшибка: {code}" if isinstance(code, str) else ""))
        self._set_status("OCR не установлен.", True)

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
        self.overlay.close()
        self.list_window.close()
        super().closeEvent(e)
        QApplication.quit()


# ================================================================ диалоги
class ResultDialog(QDialog):
    """«Перевести картинку»: сама картинка с плашками перевода и список реплик."""

    def __init__(self, parent, result: PageResult, image: Image.Image | None, cfg):
        super().__init__(parent)
        self.setWindowTitle("Результат")
        self.resize(760, 820)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(18, 16, 18, 16)
        lay.setSpacing(10)
        lay.addWidget(label("Результат", "cardTitle"))
        n = sum(1 for b in result.bubbles if b.translation)
        lay.addWidget(caption(f"Строк: {result.line_count} · облаков: {n}"
                              + (f" · из кэша {result.cache_hits}" if result.cache_hits else "")))
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
            tr = html.escape(b.translation) if b.translation else \
                f"<i>— {html.escape(b.skip_reason or 'нет перевода')} —</i>"
            tag = f"{b.lang.upper()} · " if b.lang else ""
            parts.append(f"<p><b style='color:{theme.palette().primary}'>{i}.</b> "
                         f"<span style='font-size:11pt'>{tr}</span><br>"
                         f"<span style='color:{theme.palette().text2}'>{tag}{html.escape(b.text)}</span></p>")
        text.setHtml("".join(parts) or "<i>Текст не найден.</i>")
        text.setMinimumHeight(260)
        il.addWidget(text)
        scroll.setWidget(inner)
        lay.addWidget(scroll, 1)
        ok = pill("Готово", "primary")
        ok.clicked.connect(self.accept)
        lay.addWidget(ok, 0, Qt.AlignmentFlag.AlignRight)


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
        self.setWindowTitle("Как пользоваться")
        self.resize(560, 560)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 18, 20, 16)
        lay.setSpacing(12)
        lay.addWidget(label("Как пользоваться", "cardTitle"))
        steps = [
            f"Откройте главу в браузере или читалке. Нажмите «Выделить область» ({hk.get('select', '')}) и обведите "
            "мышкой место, где идёт текст. Область запомнится.",
            f"Режим «Скриншот»: нажмите «Перевести» ({hk.get('translate', '')}) — перевод появится прямо поверх облаков. "
            f"{hk.get('toggle', '')} — показать или скрыть перевод.",
            "Режим Live: листайте главу, перевод появится сам, когда страница остановится. "
            f"{hk.get('auto', '')} — пауза.",
            "Создайте тайтл для каждой манхвы: переводчик будет помнить имена и прошлые реплики даже после перезапуска.",
            "Если текст не находится на тёмном или пёстром фоне — в «Тонкой настройке» выберите режим "
            "распознавания «Тщательный» или «Максимальный».",
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
        ok = pill("Понятно", "primary")
        ok.clicked.connect(self.accept)
        lay.addWidget(ok, 0, Qt.AlignmentFlag.AlignRight)


# ================================================================ запуск
def _excepthook(exc_type, exc, tb):
    text = "".join(traceback.format_exception(exc_type, exc, tb))
    try:
        (ROOT / "error.log").write_text(text, encoding="utf-8")
    except OSError:
        pass
    sys.__excepthook__(exc_type, exc, tb)


def create_app() -> tuple[QApplication, Config]:
    app = QApplication.instance() or QApplication(sys.argv)
    app.setApplicationName("agolate")
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
    win.show()
    return app.exec()


__all__ = ["MainWindow", "run_gui", "create_app"]
