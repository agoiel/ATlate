"""Глобальные горячие клавиши, которые работают при любой раскладке.

В Windows клавиши регистрируются в самой системе (RegisterHotKey) по коду клавиши, а не по
символу. Раньше использовалась библиотека pynput: она сравнивает символ, который клавиша даёт
в текущей раскладке, поэтому при русской раскладке Alt+Q давал «й» и не срабатывал. Заодно
не нужен перехват всей клавиатуры — система сама сообщает только о нужных сочетаниях.

Формат записи прежний (как в pynput): «<alt>+q», «<ctrl>+<shift>+t», «<f8>».

На других системах (для разработки) остаётся pynput.
"""

from __future__ import annotations

import sys
from collections.abc import Callable

from .i18n import tr

MOD_ALT, MOD_CONTROL, MOD_SHIFT, MOD_WIN, MOD_NOREPEAT = 0x1, 0x2, 0x4, 0x8, 0x4000
WM_HOTKEY = 0x0312
ERROR_HOTKEY_ALREADY_REGISTERED = 1409

_MODS = {
    "alt": MOD_ALT, "alt_l": MOD_ALT, "alt_r": MOD_ALT, "alt_gr": MOD_ALT | MOD_CONTROL,
    "ctrl": MOD_CONTROL, "ctrl_l": MOD_CONTROL, "ctrl_r": MOD_CONTROL,
    "shift": MOD_SHIFT, "shift_l": MOD_SHIFT, "shift_r": MOD_SHIFT,
    "cmd": MOD_WIN, "cmd_l": MOD_WIN, "cmd_r": MOD_WIN, "win": MOD_WIN,
}
_NAMED = {
    "space": 0x20, "enter": 0x0D, "return": 0x0D, "tab": 0x09, "esc": 0x1B, "escape": 0x1B,
    "backspace": 0x08, "insert": 0x2D, "delete": 0x2E, "home": 0x24, "end": 0x23,
    "page_up": 0x21, "page_down": 0x22, "left": 0x25, "up": 0x26, "right": 0x27, "down": 0x28,
    "pause": 0x13, "print_screen": 0x2C, "scroll_lock": 0x91, "caps_lock": 0x14, "num_lock": 0x90,
    "menu": 0x5D,
}
# знаки — по месту на клавиатуре (коды Windows для американской раскладки)
_PUNCT = {";": 0xBA, "=": 0xBB, ",": 0xBC, "-": 0xBD, ".": 0xBE, "/": 0xBF, "`": 0xC0,
          "[": 0xDB, "\\": 0xDC, "]": 0xDD, "'": 0xDE}
# русские буквы на месте латинских: если кто-то записал клавишу в русской раскладке
_RU = dict(zip("йцукенгшщзхъфывапролджэячсмитьбю", "qwertyuiop[]asdfghjkl;'zxcvbnm,."))


class HotkeyError(ValueError):
    """Сочетание записано неправильно или его нельзя зарегистрировать."""


def parse(combo: str) -> tuple[int, int]:
    """«<ctrl>+<shift>+t» → (модификаторы, код клавиши Windows)."""
    mods, vk = 0, None
    parts = [p for p in combo.strip().lower().split("+") if p]
    if not parts:
        raise HotkeyError(tr("пустое сочетание"))
    for p in parts:
        name = p[1:-1] if p.startswith("<") and p.endswith(">") else p
        if name in _MODS:
            mods |= _MODS[name]
            continue
        if vk is not None:
            raise HotkeyError(tr("в сочетании «{0}» больше одной обычной клавиши", combo))
        name = _RU.get(name, name)
        if len(name) == 1 and ("a" <= name <= "z" or "0" <= name <= "9"):
            vk = ord(name.upper())
        elif len(name) == 1 and name in _PUNCT:
            vk = _PUNCT[name]
        elif name in _NAMED:
            vk = _NAMED[name]
        elif name.startswith("f") and name[1:].isdigit() and 1 <= int(name[1:]) <= 24:
            vk = 0x70 + int(name[1:]) - 1
        elif name.isdigit():
            vk = int(name)          # «<65>» — код клавиши напрямую, как в pynput
        else:
            raise HotkeyError(tr("неизвестная клавиша «{0}» в сочетании «{1}»", p, combo))
    if vk is None:
        raise HotkeyError(tr("в сочетании «{0}» нет обычной клавиши, только модификаторы", combo))
    return mods, vk


class Hotkeys:
    """Набор глобальных сочетаний. start({сочетание: функция}) → список ошибок (пустой — всё хорошо).

    Функции вызываются в главном потоке программы.
    """

    def __init__(self):
        self._backend = None

    def start(self, mapping: dict[str, Callable[[], None]]) -> list[str]:
        self.stop()
        if not mapping:
            return []
        self._backend = _WindowsHotkeys() if sys.platform == "win32" else _PynputHotkeys()
        return self._backend.start(mapping)

    def stop(self) -> None:
        if self._backend is not None:
            self._backend.stop()
            self._backend = None


class _WindowsHotkeys:
    """RegisterHotKey: сообщения WM_HOTKEY приходят в очередь главного потока, их ловит фильтр Qt."""

    def __init__(self):
        import ctypes
        from ctypes import wintypes

        from PySide6.QtCore import QAbstractNativeEventFilter, QCoreApplication

        # своя копия user32 с use_last_error: код ошибки читается надёжно (ctypes.get_last_error)
        self._user32 = ctypes.WinDLL("user32", use_last_error=True)
        self._user32.RegisterHotKey.argtypes = (wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT)
        self._user32.RegisterHotKey.restype = wintypes.BOOL
        self._user32.UnregisterHotKey.argtypes = (wintypes.HWND, ctypes.c_int)
        self._ids: dict[int, Callable[[], None]] = {}
        owner = self

        class _Filter(QAbstractNativeEventFilter):
            def nativeEventFilter(self, event_type, message):
                if bytes(event_type) == b"windows_generic_MSG":
                    msg = wintypes.MSG.from_address(int(message))
                    if msg.message == WM_HOTKEY and msg.hWnd is None and int(msg.wParam) in owner._ids:
                        owner._fire(int(msg.wParam))
                        return True, 0
                return False, 0

        self._filter = _Filter()
        self._app = QCoreApplication.instance()
        self._app.installNativeEventFilter(self._filter)

    def _fire(self, hid: int) -> None:
        from PySide6.QtCore import QTimer
        # не изнутри системного сообщения: действие (например, окно выделения) запускается чуть позже
        QTimer.singleShot(0, self._ids[hid])

    def start(self, mapping: dict[str, Callable[[], None]]) -> list[str]:
        import ctypes
        errors: list[str] = []
        for i, (combo, fn) in enumerate(mapping.items(), start=1):
            try:
                mods, vk = parse(combo)
            except HotkeyError as e:
                errors.append(str(e))
                continue
            hid = 0xA700 + i
            # hwnd = None: сообщение придёт в очередь потока — окно может пересоздаваться (поверх всех окон)
            if self._user32.RegisterHotKey(None, hid, mods | MOD_NOREPEAT, vk):
                self._ids[hid] = fn
            else:
                code = ctypes.get_last_error()
                errors.append(tr("сочетание {0} уже занято другой программой",
                                 combo) if code == ERROR_HOTKEY_ALREADY_REGISTERED
                              else tr("не удалось зарегистрировать {0} (ошибка Windows {1})", combo, code))
        return errors

    def stop(self) -> None:
        for hid in list(self._ids):
            self._user32.UnregisterHotKey(None, hid)
        self._ids.clear()
        self._app.removeNativeEventFilter(self._filter)


class _PynputHotkeys:
    """Не Windows: прежний способ через pynput (там раскладка на сочетания не влияет так сильно)."""

    def __init__(self):
        self._listener = None

    def start(self, mapping: dict[str, Callable[[], None]]) -> list[str]:
        try:
            from pynput import keyboard
        except Exception:  # noqa: BLE001 — нет pynput или нет экрана
            return []
        from PySide6.QtCore import QObject, Qt, Signal

        class _Bridge(QObject):
            fired = Signal(str)

        self._bridge = _Bridge()
        # pynput зовёт из своего потока — в главный поток через сигнал
        self._bridge.fired.connect(lambda combo: mapping[combo](), Qt.ConnectionType.QueuedConnection)
        try:
            self._listener = keyboard.GlobalHotKeys({c: (lambda c=c: self._bridge.fired.emit(c)) for c in mapping})
            self._listener.daemon = True
            self._listener.start()
        except Exception as e:  # noqa: BLE001
            self._listener = None
            return [tr("горячие клавиши не запустились: {0}", e)]
        return []

    def stop(self) -> None:
        if self._listener is not None:
            try:
                self._listener.stop()
            except Exception:  # noqa: BLE001
                pass
            self._listener = None
