"""Функции 0.6.6: проверка обновлений, копирование перевода, журнал переводов, профили тайтлов.

    QT_QPA_PLATFORM=offscreen python tools/test_features.py out/

Код возврата 1 при ошибках.
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("ATLATE_UI_LANG", "ru")   # проверки написаны для русского интерфейса
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QLabel  # noqa: E402

from app import __version__, updates  # noqa: E402
from app.config import Config  # noqa: E402

out = Path(sys.argv[1] if len(sys.argv) > 1 else "out")
out.mkdir(parents=True, exist_ok=True)
rows: list[tuple[bool, str]] = []
app = QApplication.instance() or QApplication(sys.argv)


def check(name: str, ok: bool, details: str = "") -> None:
    rows.append((ok, name))
    print(f"{'OK  ' if ok else 'FAIL'} {name:62} {details}", flush=True)


def settle(n: int = 5) -> None:
    for _ in range(n):
        app.processEvents()


def new_cfg() -> Config:
    return Config(Path(tempfile.mkdtemp()) / "config.json")


def texts(w) -> list[str]:
    return [lb.text() for lb in w.findChildren(QLabel)]


# ---------------------------------------------------------------- обновления
def test_updates() -> None:
    check("версии: 0.6.6 новее 0.6.5", updates.is_newer("v0.6.6", "0.6.5"))
    check("версии: 0.7 новее 0.6.12", updates.is_newer("0.7", "0.6.12"))
    check("версии: та же и старая — не новее",
          not updates.is_newer("v0.6.5", "0.6.5") and not updates.is_newer("0.6.4", "0.6.5")
          and not updates.is_newer("", "0.6.5"))
    cfg = new_cfg()
    check("проверка: в первый раз — пора", updates.due(cfg, now=1_000_000))
    cfg["update_checked_at"] = 1_000_000
    check("проверка: через час — рано, через сутки — пора",
          not updates.due(cfg, now=1_003_600) and updates.due(cfg, now=1_000_000 + 86_400))
    cfg["update_check"] = False
    check("проверка выключена — не проверяем", not updates.due(cfg, now=9_999_999_999))
    newer = "9.9.9"
    cfg["update_skip"] = ""
    check("новая версия предлагается", updates.offer(cfg, (newer, "https://github.com/x")) == (newer, "https://github.com/x"))
    check("чужая ссылка заменяется страницей релизов",
          updates.offer(cfg, (newer, "https://evil.example/x")) == (newer, updates.PAGE))
    cfg["update_skip"] = newer
    check("«Позже» — об этой версии не напоминаем", updates.offer(cfg, (newer, "https://github.com/x")) is None)
    check("испорченная запись в настройках не ломает программу", updates.offer(cfg, ("x",)) is None)

    from app.ui.main_window import MainWindow
    calls: list[int] = []
    real_fetch = updates.fetch
    updates.fetch = lambda timeout=10: (calls.append(1), (newer, "https://github.com/agoiel/ATlate/releases/tag/v9.9.9"))[1]
    try:
        cfg = new_cfg()
        win = MainWindow(cfg)
        check("без новой версии плашки нет", not any("Вышла версия" in t for t in texts(win)))
        win.check_updates()
        for _ in range(50):
            settle()
            if calls and any("Вышла версия" in t for t in texts(win)):
                break
            import time
            time.sleep(0.02)
        check("новая версия — плашка появилась", any(f"Вышла версия {newer}" in t for t in texts(win)))
        check("время проверки и версия запомнены",
              cfg["update_checked_at"] > 0 and cfg["update_found"][0] == newer)
        win.check_updates()
        settle()
        check("второй раз за сутки GitHub не спрашиваем", len(calls) == 1, f"запросов {len(calls)}")
        win.close()
        win2 = MainWindow(cfg)
        check("после перезапуска плашка на месте", any(f"Вышла версия {newer}" in t for t in texts(win2)))
        win2._skip_update()
        settle()
        check("«Позже» убирает плашку", not any("Вышла версия" in t for t in texts(win2)) and cfg["update_skip"] == newer)
        win2.close()
        win3 = MainWindow(cfg)
        check("после «Позже» и перезапуска плашки нет", not any("Вышла версия" in t for t in texts(win3)))
        win3.close()
    finally:
        updates.fetch = real_fetch
    print(f"     текущая версия: {__version__}")


# ---------------------------------------------------------------- копирование
def page(*pairs) -> "PageResult":
    from app.models import Box, Bubble, PageResult
    return PageResult(bubbles=[Bubble(Box(0, i * 50, 100, 40), [], text=o, translation=t)
                               for i, (o, t) in enumerate(pairs)])


def test_copy() -> None:
    from PySide6.QtCore import QUrl

    from app.ui.list_window import ListWindow
    from app.ui.main_window import MainWindow
    from app.ui import mini as mini_mod

    res = page(("어디 가?", "Куда идёшь?"), ("소리", ""), ("기다려", "Подожди!"))
    check("текст для копирования: облака по порядку, пустые пропущены",
          res.copy_text() == "Куда идёшь?\n\nПодожди!", repr(res.copy_text()))
    clip = app.clipboard()

    cfg = new_cfg()
    check("горячая клавиша «копировать» по умолчанию", cfg["hotkeys"].get("copy") == "<alt>+c")
    win = MainWindow(cfg)
    clip.setText("старое")
    win.copy_translation()
    check("нечего копировать — буфер не трогаем, говорим почему",
          clip.text() == "старое" and "Копировать нечего" in win.status.text(), win.status.text())
    win.shown_result = res
    win._on_hotkey("copy")
    check("горячая клавиша копирует перевод страницы", clip.text() == res.copy_text())
    check("в строке состояния — сколько скопировано", "облаков 2" in win.status.text(), win.status.text())
    check("в подсказке шапки есть клавиша копирования", "<alt>+c — копировать перевод" in win.hint.text())

    # компактная панель: значок появляется, только когда есть что копировать
    win.enter_mini()
    settle()
    keys = [it.key for it in win.mini._items("translate")]
    check("компактная панель: значок «копировать», когда перевод есть", "copy" in keys, str(keys))
    win.shown_result = None
    keys = [it.key for it in win.mini._items("translate")]
    check("компактная панель: без перевода значка нет", "copy" not in keys, str(keys))
    win.shown_result = res
    clip.setText("")
    win._mini_action("copy")
    check("компактная панель: значок копирует", clip.text() == res.copy_text())
    for i, g in enumerate(win.mini.groups):
        win.mini._layout(i)
    widest = max(g.width for g in win.mini.groups)
    check("компактная панель: вытянутая кнопка помещается в окно", widest <= mini_mod.EXT, f"{widest:.0f} из {mini_mod.EXT}")
    copy_item = next(it for it in win.mini.groups[0].items if it.key == "copy")
    check("значок без надписи, с подсказкой", copy_item.text == "" and copy_item.hint == "Копировать перевод")
    win.exit_mini()
    win.close()

    lw = ListWindow()
    lw.set_result(res)
    clip.setText("")
    lw._on_link(QUrl("copy:2"))
    check("окно списка: щелчок по переводу копирует это облако", clip.text() == "Подожди!", repr(clip.text()))
    lw._on_link(QUrl("copy:1"))
    check("окно списка: облако без перевода не копируется", clip.text() == "Подожди!")
    lw._copy_all()
    check("окно списка: «Копировать всё»", clip.text() == res.copy_text())
    lw.set_result(None)
    check("окно списка: без перевода кнопка неактивна", not lw.copy_all.isEnabled())


def main() -> int:
    test_updates()
    test_copy()
    failed = [r for r in rows if not r[0]]
    print(f"failed: {len(failed)}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
