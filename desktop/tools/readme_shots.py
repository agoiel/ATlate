"""Скриншоты для README: docs/screenshots/*.png.

    QT_QPA_PLATFORM=offscreen python tools/readme_shots.py

Страница — нарисованная (та же, что в tools/test_studio.py), без чужих рисунков. Перевод идёт
через настоящий конвейер программы; подменены только распознавание (отдаёт нарисованные строки)
и переводчик (берёт переводы из test_studio). Окна — настоящие, в светлой теме.
Запускать после заметных изменений интерфейса и класть картинки в репозиторий.
studio.png — страница «до и после» из tools/test_studio.py (out/compare_02_en.png), уменьшенная до 1000 px.
"""

from __future__ import annotations

import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["ATLATE_NO_UPDATE_CHECK"] = "1"
os.environ.setdefault("ATLATE_UI_LANG", "en")      # README на GitHub читают и без русского
OUT = ROOT.parent / "docs" / "screenshots"
OUT.mkdir(parents=True, exist_ok=True)
TMP = Path(tempfile.mkdtemp())

import app  # noqa: E402,F401
from PIL import Image  # noqa: E402
from PySide6.QtCore import QRect, QRectF  # noqa: E402
from PySide6.QtGui import QImage, QPainter  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

import test_studio as ts  # noqa: E402
from app import vocab  # noqa: E402
from app.config import Config  # noqa: E402
from app.ocr.base import OcrBackend  # noqa: E402
from app.pipeline import Pipeline  # noqa: E402
from app.ui import theme  # noqa: E402

qa = QApplication.instance() or QApplication(sys.argv)
theme.apply(qa, "light")


def settle(sec: float = 0.4) -> None:
    end = time.time() + sec
    while time.time() < end:
        qa.processEvents()
        time.sleep(0.01)


def save(img: QImage | Image.Image, name: str, width: int | None = None) -> None:
    """PNG поменьше: при необходимости уменьшить и сократить палитру — README грузится быстрее."""
    if isinstance(img, QImage):
        path = TMP / "q.png"
        img.save(str(path))
        img = Image.open(path).convert("RGB")
    if width and img.width > width:
        img = img.resize((width, round(img.height * width / img.width)), Image.LANCZOS)
    img.quantize(colors=256, method=Image.Quantize.MEDIANCUT).save(OUT / name, optimize=True)
    print("saved", name, img.size, f"{(OUT / name).stat().st_size // 1024} KB")


# ---------------------------------------------------------------- страница с переводом
class TruthOcr(OcrBackend):
    name = "fake"
    supports_parallel = True
    lines: list = []

    def recognize(self, image, lang):
        return list(self.lines)


def translated_page():
    page, truth = ts.page("en", seed=2)
    cfg = Config(TMP / "config.json")
    cfg.update(source_lang="en", target_lang="ru", ocr_mode="fast", context_size=0, request_interval=0)
    pipe = Pipeline(cfg)
    TruthOcr.lines = truth
    pipe._ocr = TruthOcr(cfg)
    pipe._ocr_name = cfg["ocr_engine"]
    pipe._translator = ts.FakeTranslator({})
    pipe._translator_key = cfg["translator"]
    return page, pipe.process(page), cfg


def overlay_shot(page: Image.Image, result, cfg, peek_word: str = ""):
    """Страница с переводом поверх облаков — плашки рисует та же функция, что и окно перевода
    (снимок прозрачного окна без экрана сделал бы их полупрозрачными)."""
    from PySide6.QtGui import QColor, QPen

    from app.models import words_of
    from app.ui.overlay import paint_plates
    from app.ui.words import WordChips

    word = None
    if peek_word:
        for i, b in enumerate(result.bubbles):
            for ln in b.lines:
                for w in words_of(ln):
                    if w.text.strip("?!.,") == peek_word:
                        word = (i, w)
    base = QImage(page.tobytes("raw", "RGB"), page.width, page.height, page.width * 3, QImage.Format.Format_RGB888).copy()
    p = QPainter(base)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setRenderHint(QPainter.RenderHint.TextAntialiasing)
    bubbles = [b for i, b in enumerate(result.bubbles) if word is None or i != word[0]]
    paint_plates(p, bubbles, 1.0, base.rect(), cfg)
    if word is not None:
        w = word[1]
        p.setPen(QPen(QColor(217, 178, 124), 2))
        p.setBrush(QColor(217, 178, 124, 50))
        p.drawRoundedRect(QRectF(w.box.x, w.box.y, w.box.w, w.box.h).adjusted(-3, -2, 3, 2), 5, 5)
        chips = WordChips()
        chips.show_for(QRect(int(w.box.x), int(w.box.y), int(w.box.w), int(w.box.h)))
        settle(0.2)
        c = chips.grab().toImage()
        p.drawImage(int(w.box.x + w.box.w / 2 - c.width() / 2), int(w.box.y - c.height() - 6), c)
        chips.close()
    p.end()
    return base


def side_by_side(left: QImage, right: QImage, gap: int = 16) -> QImage:
    out = QImage(left.width() + right.width() + gap, max(left.height(), right.height()), QImage.Format.Format_RGB888)
    out.fill(0xFFFFFF)
    p = QPainter(out)
    p.drawImage(0, 0, left)
    p.drawImage(left.width() + gap, 0, right)
    p.end()
    return out


def main() -> None:
    page, result, cfg = translated_page()
    original = QImage(page.tobytes("raw", "RGB"), page.width, page.height, page.width * 3,
                      QImage.Format.Format_RGB888).copy()
    over = overlay_shot(page, result, cfg)
    save(side_by_side(original, over), "screen-translator.png", 1000)
    save(overlay_shot(page, result, cfg, peek_word="dangerous").copy(QRect(0, 560, 800, 450)), "word-list-hover.png", 800)

    # главное окно: экранный переводчик и вкладка «Голос»
    from app.ui.main_window import MainWindow
    from app.ui.vocab_window import VocabDialog
    from app.ui.widgets import Card

    cfg2 = Config(TMP / "window.json")
    win = MainWindow(cfg2)
    win.resize(1100, 1000)
    win.show()
    settle(0.8)
    save(win.grab().toImage().copy(QRect(0, 0, 1100, 1000)), "main-window.png", 900)
    cfg2["voice_enabled"] = True
    win._on_mode_switch("voice")
    settle(0.6)
    save(win.grab().toImage().copy(QRect(0, 0, 1100, 900)), "voice-tab.png", 900)
    del Card

    # словарик
    path = TMP / "vocab.json"
    for w, t, n, ctx, ctx_tr in (("dangerous", "опасный", "[ˈdeɪndʒərəs]", "That guy is dangerous.", "Этот парень опасен."),
                                 ("gate", "ворота", "[ɡeɪt]", "THE NORTHERN GATE", "СЕВЕРНЫЕ ВОРОТА"),
                                 ("hunter", "охотник", "[ˈhʌntə]", "I am a hunter.", "Я охотник."),
                                 ("awakening", "пробуждение", "", "His awakening changed everything.",
                                  "Его пробуждение всё изменило.")):
        vocab.add(vocab.Entry(w, t, n, lang="en", tgt="ru", context=ctx, context_tr=ctx_tr, title="Solo Hunter"), path)
    dlg = VocabDialog(win, path)
    dlg.resize(780, 520)
    dlg.show()
    dlg.table.selectRow(3)
    settle(0.4)
    save(dlg.grab().toImage(), "word-list.png", 780)
    dlg.close()
    win.close()


if __name__ == "__main__":
    main()
    os._exit(0)
