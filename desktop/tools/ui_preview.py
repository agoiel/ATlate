"""Снимки главного окна без экрана — для проверки интерфейса в GitHub Actions.

    QT_QPA_PLATFORM=offscreen python tools/ui_preview.py out/
"""

import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")  # на Windows-сборке задаётся windows
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


from app.ui import theme  # noqa: E402
from app.ui.main_window import MainWindow, create_app  # noqa: E402
from app.ui.widgets import Card, Dropdown  # noqa: E402

out = Path(sys.argv[1] if len(sys.argv) > 1 else "out")
out.mkdir(parents=True, exist_ok=True)
import faulthandler  # noqa: E402
import traceback  # noqa: E402

_fault = open(out / "fault.txt", "w")  # noqa: SIM115
faulthandler.enable(_fault)


def _hook(t, v, tb):
    (out / "exception.txt").write_text("".join(traceback.format_exception(t, v, tb)), encoding="utf-8")
    sys.__excepthook__(t, v, tb)


sys.excepthook = _hook
app, cfg = create_app()
cfg["dev_mode"] = True  # показать и карточку «Для разработчиков» в раскрытом виде


def settle():
    for _ in range(6):
        app.processEvents()


def shot(win, name, w, h):
    win.resize(w, h)
    settle()
    win.grab().save(str(out / f"{name}.png"))
    print("saved", name, w, h)


from PySide6.QtCore import QPoint, Qt  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402

from app.ui.widgets import Clickable  # noqa: E402
from PySide6.QtWidgets import QLabel  # noqa: E402


LOG = []


def report(tag, dd):
    """Сколько места нужно заголовку и сколько он получил — если меньше, текст наезжает."""
    h = dd.header
    pop = dd.area
    log(f"[{tag}] header need={h.minimumSizeHint().height()}/{h.sizeHint().height()} got={h.height()} "
        f"title h={dd.title.height()} sub y={dd.subtitle.y()} h={dd.subtitle.height()} dropdown={dd.height()} "
        f"list area visible={pop.isVisible()} h={pop.height()} "
        f"list need={dd.list.sizeHint().height()} got={dd.list.height()}")
    chain = []
    w = dd.parentWidget()
    while w is not None and w is not win:
        lay = w.layout()
        hfw = lay.totalHeightForWidth(w.width()) if lay is not None and lay.hasHeightForWidth() else -1
        hint = lay.totalSizeHint().height() if lay is not None else w.sizeHint().height()
        chain.append(f"{type(w).__name__}#{w.objectName()} h={w.height()} hfw={hfw} hint={hint} min={w.minimumHeight()}")
        w = w.parentWidget()
    log("    chain: " + " | ".join(chain[:9]))


def squeezed(tag):
    """Найти надписи, которым досталось меньше высоты, чем нужно (тогда они наезжают друг на друга)."""
    from PySide6.QtWidgets import QLabel
    bad = []
    for lb in win.findChildren(QLabel):
        if not lb.isVisible() or not lb.text() or lb.window() is not win:
            continue
        need = lb.heightForWidth(lb.width()) if lb.wordWrap() else lb.sizeHint().height()
        if lb.height() < need - 2:
            bad.append(f"'{lb.text()[:28]}' {lb.height()}<{need}")
    log(f"[{tag}] squeezed labels: {len(bad)} " + "; ".join(bad[:6]))


def stretched(tag):
    """Карточки, которые выше своего содержимого (пустое место внутри)."""
    bad = []
    for c in win.findChildren(Card):
        if not c.isVisible():
            continue
        lay = c.layout()
        need = lay.totalHeightForWidth(c.width()) if lay.hasHeightForWidth() else lay.totalSizeHint().height()
        if c.height() > need + 6:
            bad.append(f"{c.findChildren(QLabel)[1].text()[:16]} {c.height()}>{need}")
    log(f"[{tag}] stretched cards: {len(bad)} {'; '.join(bad)}")


def log(msg):
    print(msg)
    LOG.append(msg)
    (out / "report.txt").write_text("\n".join(LOG), encoding="utf-8")


def show(w):
    """Прокрутить окно так, чтобы виджет был сверху видимой части."""
    win.scroll.verticalScrollBar().setValue(w.mapTo(win.scroll.widget(), QPoint(0, 0)).y() - 20)
    settle()


def click(w):
    QTest.mouseClick(w, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier,
                     QPoint(w.width() // 2, min(20, w.height() // 2)))
    settle()


# 1) как у пользователя: окно открыто, кликаем мышкой по спискам и карточкам
theme.apply(app, "dark")
win = MainWindow(cfg)
win.resize(1000, 1100)
win.show()
settle()

translator = next(d for d in win.findChildren(Dropdown) if "deepl" in d.choices)
show(translator)
squeezed("start")
report("before", translator)


def record(name, action, ms=420, step=30):
    """Снять кадры анимации в GIF."""
    from PIL import Image
    frames = []
    action()
    for _ in range(ms // step):
        QTest.qWait(step)
        img = win.grab().toImage()
        frames.append(Image.fromqimage(img).convert("RGB").resize((img.width() // 2, img.height() // 2)))
    QTest.qWait(300)
    settle()
    frames.append(Image.fromqimage(win.grab().toImage()).convert("RGB").resize(frames[0].size))
    frames[0].save(out / f"{name}.gif", save_all=True, append_images=frames[1:], duration=step, loop=0)


record("anim_translator_open", lambda: click(translator.header))
report("opened", translator)
squeezed("list open")
win.grab().save(str(out / "click_translator_open.png"))
choice = translator.choices["deepl"]
translator.area.ensureWidgetVisible(choice)
settle()
record("anim_translator_choose", lambda: click(choice))
log(f"chosen: {translator.current} list visible after choose: {translator.area.isVisible()}")
report("chosen", translator)
squeezed("after choosing DeepL")
win.grab().save(str(out / "click_translator_chosen.png"))

# --- компактный список (язык перевода): раскрывается вниз на несколько строк, дальше прокрутка
from app.ui.widgets import Select, Toggle  # noqa: E402
tgt = win.tgt_combo
src = win.src_combo
show(tgt)
src_y = src.mapTo(win, QPoint(0, 0)).y()
record("anim_select_open", lambda: click(tgt.header))
log(f"select open: area h={tgt.area.height()} rows={len(tgt._rows)} "
    f"scroll max={tgt.area.verticalScrollBar().maximum()} src moved={src.mapTo(win, QPoint(0, 0)).y() - src_y}")
squeezed("select open")
win.grab().save(str(out / "click_select_open.png"))
before = tgt.currentIndex()
row = tgt._rows[3]
tgt.area.ensureWidgetVisible(row)
settle()
click(row)
QTest.qWait(350)
settle()
log(f"select chose: index {before} -> {tgt.currentIndex()} '{tgt.currentText()}' open={tgt.area.isVisible()}")
squeezed("select closed")

# --- переключатели не должны сдвигаться при нажатии
bad_moves = []
for sw in [w for w in win.findChildren(Toggle) if w.isVisible()][:4]:
    x0 = sw.mapTo(win, QPoint(0, 0))
    show(sw)
    x0 = sw.mapTo(win, QPoint(0, 0))
    click(sw)
    QTest.qWait(250)
    x1 = sw.mapTo(win, QPoint(0, 0))
    if x0 != x1:
        bad_moves.append(f"{x0.x()},{x0.y()}->{x1.x()},{x1.y()}")
    click(sw)
    QTest.qWait(250)
log(f"toggles moved: {len(bad_moves)} {' '.join(bad_moves)}")
cards = [c for c in win.findChildren(Card) if hasattr(c, "chevron")]
collapse_heads = [w for w in cards[0].findChildren(Clickable) if w.objectName() == "collapseHeader"]
show(cards[0])
record("anim_tuning_open", lambda: click(collapse_heads[0]))
show(cards[0])
log(f"tuning open: {cards[0].body.isVisible()}")
squeezed("tuning open")
for c in cards[1:]:
    hd = [w for w in c.findChildren(Clickable) if w.objectName() == "collapseHeader"][0]
    show(c)
    click(hd)
    QTest.qWait(400)
squeezed("all cards open")
stretched("all cards open")
# список в «Тонкой настройке» внутри раскрытой карточки — тема оформления (у движка OCR
# пункт EasyOCR есть, только если он установлен, поэтому список может быть из одного пункта)
eng = [s for s in win.findChildren(Select) if s.findData("light") >= 0][0]
show(eng)
click(eng.header)
QTest.qWait(400)
settle()
show(eng)
log(f"engine select open: area h={eng.area.height()} visible={eng.area.isVisible()}")
squeezed("engine select open")
stretched("engine select open")
win.grab().save(str(out / "click_engine_select.png"))
click(eng.header)
QTest.qWait(400)
settle()
# переключатели в раскрытых карточках
bad_moves = []
for sw in [w for w in win.findChildren(Toggle) if w.isVisible()]:
    show(sw)
    x0 = sw.mapTo(win, QPoint(0, 0))
    click(sw)
    QTest.qWait(250)
    if sw.mapTo(win, QPoint(0, 0)).x() != x0.x():
        bad_moves.append(sw.parentWidget().findChildren(QLabel)[0].text()[:20] if sw.parentWidget() else "?")
    click(sw)
    QTest.qWait(250)
log(f"toggles in open cards moved: {len(bad_moves)} {bad_moves}")
record("anim_tuning_close", lambda: click(collapse_heads[0]))
squeezed("tuning closed")
win.resize(420, 760)
QTest.qWait(400)
settle()
squeezed("narrow window")
win.grab().save(str(out / "click_narrow.png"))
win.grab().save(str(out / "click_tuning_open.png"))
win.close()
settle()

if os.environ.get("PREVIEW_ONLY_CLICKS"):
    print("done")
    sys.exit(0)

for mode in ("light", "dark"):
    theme.apply(app, mode)
    win = MainWindow(cfg)
    win.show()
    settle()
    if mode == "light":
        shot(win, "light_wide", 1100, 1500)
        shot(win, "light_medium", 760, 1700)
        shot(win, "light_narrow", 380, 2600)
        # раскрытые карточки
        for card in win.findChildren(Card):
            if hasattr(card, "chevron"):
                card.toggle()
        QTest.qWait(500)
        shot(win, "light_expanded", 1100, 2600)
    else:
        shot(win, "dark_wide", 1100, 1500)
    win.close()
    settle()
print("done")
