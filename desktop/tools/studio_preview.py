"""Снимки окна Студии (для проверки в CI). Сначала запустите tools/test_studio.py с той же папкой.

    python tools/studio_preview.py out/
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPoint, Qt  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from app.config import Config  # noqa: E402
from app.studio.window import StudioWindow  # noqa: E402
from app.ui import theme  # noqa: E402

out = Path(sys.argv[1] if len(sys.argv) > 1 else "out")
LOG: list[str] = []


def log(msg: str) -> None:
    print(msg)
    LOG.append(msg)
    (out / "report_studio_ui.txt").write_text("\n".join(LOG), encoding="utf-8")


def settle(ms: int = 300) -> None:
    """Подождать, обрабатывая события. time.sleep отпускает Python для фоновых задач Студии
    (QTest.qWait держит его, и фоновая отрисовка в тесте не шла бы вовсе)."""
    import time
    end = time.monotonic() + ms / 1000
    while time.monotonic() < end:
        QApplication.processEvents()
        time.sleep(0.02)


app = QApplication.instance() or QApplication(sys.argv)
for mode in ("light", "dark"):
    theme.apply(app, mode)
    cfg = Config(out / "config.json")
    win = StudioWindow(cfg)
    win.resize(1280, 820)
    win.show()
    settle()
    if mode == "light":
        win.grab().save(str(out / "studio_empty.png"))
    win.load(out / "chapter")
    settle(1200)
    first = next((i for i, pg in enumerate(win.project.pages) if len(pg.blocks) >= 3), 0)
    win.go(first)
    settle(1500)
    log(f"[{mode}] pages: {win.pages.count()}, page {first + 1} blocks: {len(win.cards)}, "
        f"rendered: {win._rendered is not None}, status: {win.status.text()}")
    if getattr(win, "last_error", ""):
        log(win.last_error)
    win.grab().save(str(out / f"studio_{mode}.png"))
    if mode == "light":
        # выбрать блок кликом по его рамке
        page = win.project.pages[first]
        b = page.blocks[2]
        item = win.view.items_[b.id]
        pt = win.view.mapFromScene(item.sceneBoundingRect().center())
        QTest.mouseClick(win.view.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, pt)
        settle(600)
        log(f"selected: {win.view.selected_id() == b.id}, card open: {win.cards[b.id].more.isVisible()}")
        win.grab().save(str(out / "studio_selected.png"))
        # правка перевода в карточке → страница перерисовывается
        card = win.cards[b.id]
        card.translation.setPlainText("Новый текст перевода, который длиннее прежнего")
        settle(1500)
        lay = win._rendered.layouts.get(b.id) if win._rendered else None
        log(f"edited: block text = {b.translation!r}, rendered lines = {len(lay.lines) if lay else 0}")
        # перетащить блок мышью
        old = (b.box.x, b.box.y)
        start = win.view.mapFromScene(item.sceneBoundingRect().center())
        QTest.mousePress(win.view.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, start)
        for k in range(1, 6):
            QTest.mouseMove(win.view.viewport(), start + QPoint(6 * k, 4 * k))
        QTest.mouseRelease(win.view.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier,
                           start + QPoint(30, 20))
        settle(800)
        log(f"moved: {old} -> {(round(b.box.x), round(b.box.y))}")
        # режимы просмотра
        win.mode.buttons["clean"].click()
        settle(400)
        win.grab().save(str(out / "studio_clean.png"))
        win.mode.buttons["result"].click()
        # страница-лента
        win.go(2)
        settle(1500)
        log(f"strip page blocks: {len(win.cards)}")
        win.grab().save(str(out / "studio_strip.png"))
        win.resize(900, 700)
        settle(600)
        win.grab().save(str(out / "studio_small.png"))
    win.close()
    settle()
log("done")
