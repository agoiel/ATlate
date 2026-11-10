"""Настройки программы. Хранятся в config.json рядом с программой."""

from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

# В собранном exe настройки и глоссарии лежат рядом с ATlate.exe
if getattr(sys, "frozen", False):
    ROOT = Path(sys.executable).resolve().parent
else:
    ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "config.json"
GLOSSARY_DIR = ROOT / "glossaries"

DEFAULTS: dict = {
    "source_lang": "ko",
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
    "glossary": "",                   # имя файла глоссария в папке glossaries (пусто — без глоссария)
    "active_title": "",               # активный тайтл (его контекст хранится в папке titles)
    "content_mode": "manhwa",         # manhwa — облака манхвы | screen — обычный текст на экране
    "full_width_select": False,       # выделять область по всей ширине экрана
    "theme": "system",                # system | light | dark
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
        "gemini": {"api_key": "", "model": "gemini-2.5-flash-lite"},
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
    },

    # Последняя выделенная область (заполняется автоматически)
    "last_region": None,
}


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

    def save(self) -> None:
        self.path.write_text(json.dumps(self, ensure_ascii=False, indent=2), encoding="utf-8")

    def translator_settings(self, name: str) -> dict:
        return self["translators"].get(name, {})
