"""Настройки программы. Хранятся в config.json рядом с программой (или в папке пользователя)."""

from __future__ import annotations

import copy
import json
import os
import shutil
import sys
from pathlib import Path

# Где лежит сама программа (в собранном exe — папка с ATlate.exe): встроенные глоссарии и шрифты
if getattr(sys, "frozen", False):
    ROOT = Path(sys.executable).resolve().parent
else:
    ROOT = Path(__file__).resolve().parent.parent


def _writable(folder: Path) -> bool:
    try:
        folder.mkdir(parents=True, exist_ok=True)
        probe = folder / ".atlate-write-test"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        return True
    except OSError:
        return False


def data_root(root: Path, writable=_writable) -> Path:
    """Где хранить настройки, глоссарии, тайтлы и журналы.

    Рядом с программой, если туда можно писать — как раньше, программа остаётся «переносной».
    Если нельзя (например, её положили в Program Files), — в папке пользователя
    (%APPDATA%\\ATlate); при первом таком запуске туда переносится то, что уже лежит рядом
    с программой. Раньше в этом случае настройки не сохранялись, а программа падала.
    """
    if writable(root):
        return root
    base = Path(os.environ.get("APPDATA") or (Path.home() / ".config"))
    data = base / "ATlate"
    data.mkdir(parents=True, exist_ok=True)
    if (root / "config.json").exists() and not (data / "config.json").exists():
        shutil.copy2(root / "config.json", data / "config.json")
    for folder in ("glossaries", "titles"):
        src = root / folder
        if src.is_dir():
            (data / folder).mkdir(exist_ok=True)
            for f in src.iterdir():
                # своих файлов пользователя не трогаем — копируем только недостающие
                if f.is_file() and not (data / folder / f.name).exists():
                    shutil.copy2(f, data / folder / f.name)
    return data


DATA_ROOT = data_root(ROOT)
CONFIG_PATH = DATA_ROOT / "config.json"
GLOSSARY_DIR = DATA_ROOT / "glossaries"

# Модель Gemini по умолчанию. «-latest» — постоянное имя, которое Google сам переводит на новую
# Flash-Lite, поэтому отключение старой модели не ломает перевод.
GEMINI_MODEL = "gemini-flash-lite-latest"
# Модель, которая стояла по умолчанию в прошлых версиях и уже отключена Google: при загрузке
# настроек она заменяется на GEMINI_MODEL (иначе каждый запрос получал ошибку 404).
# Модель, выбранную вручную, не трогаем — при ошибке 404 программа подскажет сменить её.
RETIRED_GEMINI = {"gemini-2.5-flash-lite"}

DEFAULTS: dict = {
    "source_lang": "ko",
    "auto_lang": False,               # экранный переводчик сам определяет язык оригинала (source_lang — последний найденный)
    "target_lang": "ru",

    # --- Распознавание ---
    "ocr_engine": "windows",          # windows | easyocr
    "ocr_mode": "thorough",           # fast | thorough | max — сколько проходов распознавания
    "ocr_upscale": 1.5,               # увеличение картинки перед OCR (мелкий текст читается лучше)
    "ocr_min_conf": 0.3,              # строки с меньшей уверенностью отбрасываются (только EasyOCR)
    "easyocr_gpu": False,

    # --- Сборка облаков ---
    "bubble_gap": 1.1,                # макс. зазор между строками одного облака (в высотах строки)
    "bubble_row_gap": 1.5,            # макс. зазор между словами в одной строке (в высотах строки)
    "bubble_border_check": True,      # не склеивать строки, если между ними контур облака
    "skip_free_text": False,          # пропускать текст поверх рисунка (звуки и т.п.)
    "skip_cut_bubbles": True,         # пропускать текст, обрезанный краем области
    "keep_tilt": True,                # перевод под тем же углом, что и наклонённый оригинал

    # --- Перевод ---
    "translator": "google_free",      # google_free | yandex | duckduckgo | lingva | deepl | microsoft |
                                      # papago | gemini | claude | openai
    "context_size": 12,               # сколько прошлых реплик передавать как контекст
    "request_interval": 1.0,          # мин. пауза между запросами к переводчику, секунды
    "free_fallback": True,            # бесплатный переводчик не ответил — пробовать другие бесплатные
    "glossary": "",                   # имя файла глоссария в папке glossaries (пусто — без глоссария)
    "active_title": "",               # активный тайтл (его контекст хранится в папке titles)
    "content_mode": "manhwa",         # manhwa — облака манхвы | screen — обычный текст на экране
    "full_width_select": False,       # выделять область по всей ширине экрана
    "theme": "system",                # system | light | dark
    "ui_lang": "system",              # язык интерфейса: system — как в системе | ru | en
    "update_check": True,             # раз в день узнавать на GitHub, не вышла ли новая версия
    "update_checked_at": 0,           # когда проверяли в последний раз (время Unix)
    "update_skip": "",                # версия, о которой попросили не напоминать («Позже»)
    "update_found": None,             # последняя найденная версия [номер, ссылка]
    "translate_mode": "shot",         # shot — перевод по кнопке | live — сам, когда страница остановилась
    "always_on_top": False,           # главное окно поверх всех окон
    "window_size": None,              # размер главного окна (запоминается)
    "dev_mode": False,                # режим разработчика: время этапов, проходы, журнал
    "parallel_ocr": True,             # проходы распознавания идут одновременно
    "studio_load": "normal",          # нагрузка на ПК при переводе главы в Студии: low | normal | high
    "debug_boxes": True,              # в режиме разработчика — рамки распознанных строк
    "translators": {
        "yandex": {"api_key": "", "folder_id": ""},
        "lingva": {"base_url": "https://lingva.ml"},
        "papago": {"client_id": "", "client_secret": ""},
        "claude": {"api_key": "", "model": "claude-haiku-5-5"},
        "deepl": {"api_key": ""},
        "microsoft": {"api_key": "", "region": "global"},
        "gemini": {"api_key": "", "model": GEMINI_MODEL},
        "openai": {
            # Подходит для OpenAI, OpenRouter, DeepSeek и локальных моделей (Ollama, LM Studio)
            "base_url": "http://localhost:11434/v1",
            "api_key": "",
            "model": "qwen2.5:7b",
        },
    },

    # --- Отображение ---
    "display_mode": "overlay",        # overlay — поверх облаков | list — окно со списком
    "overlay_font": "Segoe UI",
    "overlay_max_font_size": 20,
    "overlay_opacity": 0.95,
    "auto_interval_ms": 700,          # как часто проверять область в режиме «Авто»

    # --- Компактный режим: маленькая панель поверх экрана вместо большого окна ---
    "mini_mode": False,               # программа запускается сразу в компактном режиме
    "mini_pos": None,                 # где стоит панель (запоминается)
    "mini_collapse_delay": 5,         # через сколько секунд после ухода мыши панель сворачивается

    # --- Горячие клавиши (формат pynput) ---
    "hotkeys": {
        "select": "<alt>+q",
        "translate": "<alt>+w",
        "toggle": "<alt>+e",
        "auto": "<alt>+r",
        "mini": "",                   # компактный режим вкл/выкл (по умолчанию без клавиши)
        "copy": "<alt>+c",            # скопировать перевод страницы, которая сейчас показана
    },

    # Последняя выделенная область (заполняется автоматически)
    "last_region": None,
}


def _migrate(cfg: dict) -> None:
    """Поправить устаревшие настройки из прошлых версий."""
    gemini = cfg.get("translators", {}).get("gemini")
    if isinstance(gemini, dict) and str(gemini.get("model", "")).strip() in RETIRED_GEMINI:
        gemini["model"] = GEMINI_MODEL


def _merge(base: dict, override: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


class Config(dict):
    """Словарь настроек с загрузкой/сохранением."""

    def __init__(self, path: Path = CONFIG_PATH):
        super().__init__()
        self.path = path
        self.load()

    def load(self) -> None:
        data = {}
        if self.path.exists():
            try:
                data = json.loads(self.path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                data = {}
        self.clear()
        self.update(_merge(DEFAULTS, data))
        _migrate(self)

    save_error = ""            # почему не удалось сохранить в последний раз (пусто — сохранилось)
    on_save_error = None       # кому сообщить, что сохранить не удалось (окно показывает это в строке состояния)

    def save(self) -> None:
        """Сохранить через временный файл: если программу закроют посреди записи, настройки
        и ключи не пропадут. Ошибка записи не роняет программу — она запоминается в save_error."""
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self, ensure_ascii=False, indent=2), encoding="utf-8")
            tmp.replace(self.path)
            self.save_error = ""
        except OSError as e:
            self.save_error = str(e)
            if self.on_save_error is not None:
                self.on_save_error(self.save_error)

    def translator_settings(self, name: str) -> dict:
        return self["translators"].get(name, {})
