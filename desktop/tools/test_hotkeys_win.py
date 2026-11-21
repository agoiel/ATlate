"""Горячие клавиши на настоящей Windows при английской и русской раскладке.

    python tools/test_hotkeys_win.py

Нажатия делаются через систему (keybd_event), как будто человек нажал клавиши. Для сравнения
проверяется и прежний способ — библиотека pynput: при русской раскладке он не срабатывал.
Код возврата 1, если новый способ не сработал.
"""

from __future__ import annotations

import ctypes
import os
import sys
import time
from ctypes import wintypes
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("ATLATE_UI_LANG", "ru")   # проверки написаны для русского интерфейса

if sys.platform != "win32":
    print("только для Windows — пропускаю")
    sys.exit(0)

from PySide6.QtCore import QEventLoop, QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication, QLabel  # noqa: E402

from app.hotkeys import Hotkeys  # noqa: E402

user32 = ctypes.windll.user32
user32.LoadKeyboardLayoutW.restype = wintypes.HKL
user32.ActivateKeyboardLayout.argtypes = (wintypes.HKL, wintypes.UINT)
user32.GetKeyboardLayout.restype = wintypes.HKL
KEYUP = 0x2
VK_MENU, VK_CONTROL, VK_SHIFT = 0x12, 0x11, 0x10
LAYOUTS = {"английская": "00000409", "русская": "00000419"}

app = QApplication(sys.argv)
win = QLabel("ATlate — проверка горячих клавиш")
win.resize(420, 120)
win.show()
win.raise_()
win.activateWindow()
rows: list[tuple[bool, str]] = []


def spin(sec: float) -> None:
    loop = QEventLoop()
    QTimer.singleShot(int(sec * 1000), loop.quit)
    loop.exec()


def use_layout(klid: str) -> str:
    hkl = user32.LoadKeyboardLayoutW(klid, 0x1 | 0x100)   # KLF_ACTIVATE | KLF_SETFORPROCESS
    user32.ActivateKeyboardLayout(hkl, 0x100)
    spin(0.3)
    return hex(user32.GetKeyboardLayout(0) & 0xFFFF)


def press(*vks: int) -> None:
    for vk in vks:
        user32.keybd_event(vk, user32.MapVirtualKeyW(vk, 0), 0, 0)
        time.sleep(0.03)
    for vk in reversed(vks):
        user32.keybd_event(vk, user32.MapVirtualKeyW(vk, 0), KEYUP, 0)
        time.sleep(0.03)


def check(name: str, ok: bool, details: str = "") -> None:
    rows.append((ok, name))
    print(f"{'OK  ' if ok else 'FAIL'} {name:58} {details}", flush=True)


# ---------------------------------------------------------------- новый способ (RegisterHotKey)
hits = {"alt_q": 0, "ctrl_shift_f8": 0}
hk = Hotkeys()
errors = hk.start({"<alt>+q": lambda: hits.__setitem__("alt_q", hits["alt_q"] + 1),
                   "<ctrl>+<shift>+<f8>": lambda: hits.__setitem__("ctrl_shift_f8", hits["ctrl_shift_f8"] + 1)})
check("регистрация сочетаний", not errors, "; ".join(errors))

for name, klid in LAYOUTS.items():
    active = use_layout(klid)
    before = dict(hits)
    press(VK_MENU, ord("Q"))
    spin(0.5)
    check(f"Alt+Q · {name} раскладка (новый способ)", hits["alt_q"] == before["alt_q"] + 1,
          f"раскладка {active}, срабатываний {hits['alt_q'] - before['alt_q']}")
    press(VK_CONTROL, VK_SHIFT, 0x77)
    spin(0.5)
    check(f"Ctrl+Shift+F8 · {name} раскладка (новый способ)",
          hits["ctrl_shift_f8"] == before["ctrl_shift_f8"] + 1)

other = Hotkeys()
errs = other.start({"<alt>+q": lambda: None})
check("занятое сочетание — понятное сообщение", bool(errs) and "занято" in errs[0], errs[0] if errs else "ошибки нет")
other.stop()
hk.stop()
before = hits["alt_q"]
press(VK_MENU, ord("Q"))
spin(0.4)
check("после остановки сочетание больше не срабатывает", hits["alt_q"] == before)

# ---------------------------------------------------------------- прежний способ (pynput) — для сравнения
try:
    from pynput import keyboard
    old = {"n": 0}
    listener = keyboard.GlobalHotKeys({"<alt>+q": lambda: old.__setitem__("n", old["n"] + 1)})
    listener.start()
    spin(0.5)
    for name, klid in LAYOUTS.items():
        use_layout(klid)
        if hasattr(listener, "_translator"):
            listener._translator.update_layout()     # так pynput сам делает при смене раскладки
        n0 = old["n"]
        press(VK_MENU, ord("Q"))
        spin(0.6)
        print(f"INFO Alt+Q · {name} раскладка (прежний способ, pynput): срабатываний {old['n'] - n0}", flush=True)
    listener.stop()
except Exception as e:  # noqa: BLE001
    print(f"INFO прежний способ проверить не удалось: {e}")

failed = [r for r in rows if not r[0]]
print(f"failed: {len(failed)}")
sys.exit(1 if failed else 0)
