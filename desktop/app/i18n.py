"""Язык интерфейса: русский или английский, по умолчанию — как в системе.

Как в Android-версии: русский текст в коде — это ключ; при английском интерфейсе подставляется
перевод из словаря i18n_en.py. Нет перевода — показывается русский.

    tr("Готово")                       → "Done"
    tr("Записей: {0}", n)              → "Entries: 5"

Язык выбирается один раз при первом импорте (из настроек: ui_lang = system / ru / en), поэтому
строки, заданные при загрузке модулей, тоже переводятся. Смена языка — после перезапуска.
Для проверок язык можно задать переменной окружения ATLATE_UI_LANG.
"""

from __future__ import annotations

import json
import os

# языки системы, при которых по умолчанию остаётся русский интерфейс (как в Android-версии)
RUSSIAN_LOCALES = {"ru", "uk", "be", "kk", "uz", "ky", "tg"}
CHOICES = ("system", "ru", "en")

english = False


def system_language() -> str:
    """Код языка интерфейса системы: «ru», «en», «de»…"""
    try:
        from PySide6.QtCore import QLocale
        for name in QLocale.system().uiLanguages():
            code = name.replace("_", "-").split("-")[0].lower()
            if code and code != "c":
                return code
    except Exception:  # noqa: BLE001 — нет Qt: ниже по переменным окружения
        pass
    import locale
    for value in (os.environ.get("LANGUAGE", ""), os.environ.get("LC_ALL", ""),
                  os.environ.get("LC_MESSAGES", ""), os.environ.get("LANG", ""), locale.getlocale()[0] or ""):
        code = value.split(":")[0].replace("_", "-").split("-")[0].split(".")[0].lower()
        if code and code not in ("c", "posix"):
            return code
    return "en"


def resolve(choice: str) -> bool:
    """Выбор из настроек → английский ли интерфейс."""
    if choice == "en":
        return True
    if choice == "ru":
        return False
    return system_language() not in RUSSIAN_LOCALES


def saved_choice() -> str:
    """Что выбрано в настройках (config.json), без загрузки всех настроек."""
    env = os.environ.get("ATLATE_UI_LANG", "").strip().lower()
    if env in CHOICES:
        return env
    try:
        from .config import CONFIG_PATH
        value = json.loads(CONFIG_PATH.read_text(encoding="utf-8")).get("ui_lang", "system")
        return value if value in CHOICES else "system"
    except (OSError, ValueError, AttributeError):
        return "system"


def init(choice: str | None = None) -> None:
    global english
    english = resolve(choice or saved_choice())


def lower(text: str) -> str:
    """Название языка посреди фразы: по-русски — со строчной («сейчас корейский»), по-английски — как есть."""
    return text if english else text.lower()


def tr(text: str, *args, **kwargs) -> str:
    """Перевод строки интерфейса; с аргументами — подставляет их в {0}, {1}… ({name})."""
    if english:
        from .i18n_en import EN
        text = EN.get(text, text)
    return text.format(*args, **kwargs) if args or kwargs else text


init()
