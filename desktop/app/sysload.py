"""Нагрузка на компьютер во время долгой работы (перевод главы в Студии).

* Уровни нагрузки: сколько проходов распознавания идёт одновременно и с каким
  приоритетом работает программа.
* Пониженный приоритет процесса (Windows): пока глава переводится, игры, браузер и
  остальные программы получают процессор первыми, поэтому компьютер не «тормозит».
* Пауза и остановка в любой момент: долгая работа время от времени вызывает
  checkpoint() — там она ждёт, пока пауза не снята, или прерывается.
"""

from __future__ import annotations

import os
import sys
import threading

# уровень → (сколько проходов распознавания одновременно, пониженный приоритет)
LOAD_LEVELS = {
    "low": (1, True),
    "normal": (2, True),
    "high": (0, False),     # 0 — по числу ядер, как в экранном переводчике
}
LOAD_NAMES = {
    "low": "Щадящая — медленнее, ПК почти не замечает",
    "normal": "Обычная — быстро и без тормозов",
    "high": "Максимальная — быстрее всего, ПК может тормозить",
}
DEFAULT_LOAD = "normal"


def ocr_threads(level: str) -> int:
    return LOAD_LEVELS.get(level, LOAD_LEVELS[DEFAULT_LOAD])[0]


def wants_low_priority(level: str) -> bool:
    return LOAD_LEVELS.get(level, LOAD_LEVELS[DEFAULT_LOAD])[1]


def limit_math_threads() -> None:
    """Не давать numpy занимать все ядра своими потоками.

    Библиотеки линейной алгебры (OpenBLAS, MKL) по умолчанию запускают поток на каждое ядро
    и держат их в активном ожидании — на больших картинках это 100% процессора впустую.
    Работает, только если вызвать до первого импорта numpy.
    """
    for var in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
        os.environ.setdefault(var, "2")


# ---------------------------------------------------------------- приоритет процесса

_NORMAL = 0x00000020
_BELOW_NORMAL = 0x00004000
_lock = threading.Lock()
_holders = 0


def _set_priority_class(cls: int) -> None:
    if sys.platform != "win32":
        return
    try:
        import ctypes
        k = ctypes.windll.kernel32
        k.SetPriorityClass(k.GetCurrentProcess(), cls)
    except Exception:  # noqa: BLE001
        pass


class LowPriority:
    """Пониженный приоритет программы, пока идёт долгая работа.

    set(True) — понизить, set(False) или release() — вернуть. Одновременные задачи
    учитываются: обычный приоритет возвращается, когда его отпустила последняя.
    """

    def __init__(self):
        self.active = False

    def set(self, on: bool) -> None:
        global _holders
        if on == self.active:
            return
        self.active = on
        with _lock:
            _holders += 1 if on else -1
            if on and _holders == 1:
                _set_priority_class(_BELOW_NORMAL)
            elif not on and _holders == 0:
                _set_priority_class(_NORMAL)

    def release(self) -> None:
        self.set(False)


# ---------------------------------------------------------------- пауза и остановка

class Cancelled(Exception):
    """Работу остановили кнопкой «Остановить»."""


class Control:
    """Пауза и остановка долгой работы из другого потока (из окна)."""

    def __init__(self):
        self._running = threading.Event()
        self._running.set()
        self.cancelled = False

    @property
    def paused(self) -> bool:
        return not self._running.is_set() and not self.cancelled

    def pause(self) -> None:
        if not self.cancelled:
            self._running.clear()

    def resume(self) -> None:
        self._running.set()

    def cancel(self) -> None:
        self.cancelled = True
        self._running.set()     # стоящая на паузе работа должна проснуться и завершиться

    def checkpoint(self) -> None:
        """Подождать, если на паузе; прервать работу, если её остановили."""
        if not self._running.is_set():
            self._running.wait()
        if self.cancelled:
            raise Cancelled()
