"""ATlate — переводчик манхвы/манги/маньхуа с экрана."""

__version__ = "0.6.7"

# что с FriBiDi: путь загруженной библиотеки, текст ошибки или пусто (нет файла / не Windows)
fribidi = ""


def _load_fribidi() -> None:
    """Windows: подгрузить FriBiDi раньше Pillow, если он лежит в папке libs.

    С ним Pillow верстает сложное письмо через HarfBuzz (raqm): арабские буквы соединяются и идут
    справа налево, тайские знаки встают над буквами. Pillow ищет библиотеку по имени один раз —
    когда загружается его модуль шрифтов, — а уже загруженная библиотека находится по имени из
    любой папки. Без неё всё работает как раньше, только с простой вёрсткой.
    """
    global fribidi
    import sys
    if sys.platform != "win32":
        return
    if "PIL._imagingft" in sys.modules:
        fribidi = "Pillow загрузил модуль шрифтов раньше — FriBiDi уже не подхватится"
        return
    import ctypes
    from pathlib import Path

    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[1])) / "libs"
    for name in ("libfribidi-0.dll", "fribidi-0.dll", "fribidi.dll"):
        if (base / name).exists():
            try:
                ctypes.WinDLL(str(base / name))
                fribidi = str(base / name)
                return
            except OSError as e:
                fribidi = f"{name}: {e}"


_load_fribidi()
