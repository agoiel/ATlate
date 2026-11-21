"""«Слова на экране» и словарик (ПК).

    QT_QPA_PLATFORM=offscreen python tools/test_words.py out/

* Словарик: слово без знаков препинания и корейской частицы, ЗАГЛАВНЫЕ — строчными; без дублей;
  файл переживает перезапуск; поиск; CSV для Anki; перевод слова — из глоссария без запроса,
  иначе переводчиком, и ИИ видит фразу, где слово встретилось.
* Рамки слов: у строки без рамок слова делятся по пробелам.
* Наведение в настоящем окне (курсор подменён): мышь постояла на слове — плашка облака прячется,
  слово обведено, над ним кнопки; мышь, которая просто лежит после нового перевода, ничего не
  открывает; ушла — всё вернулось; «Копировать» — слово в буфере; «В словарик» — запись с
  переводом и примером; второй раз — «Уже в словарике»; настройка выключает.
* Окно «Словарик»: правка ячейки сохраняется, удаление, CSV.
Код возврата 1 при ошибках.
"""

from __future__ import annotations

import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("ATLATE_NO_UPDATE_CHECK", "1")
os.environ.setdefault("ATLATE_UI_LANG", "ru")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

out = Path(sys.argv[1] if len(sys.argv) > 1 else "out")
out.mkdir(parents=True, exist_ok=True)

import app  # noqa: E402,F401
from app import vocab  # noqa: E402
from app.glossary import Glossary, GlossaryEntry  # noqa: E402
from app.models import Box, Bubble, PageResult, TextLine, Word, words_of  # noqa: E402

rows: list[tuple[bool, str]] = []
TMP = Path(tempfile.mkdtemp())


def check(name: str, ok: bool, details: str = "") -> None:
    rows.append((bool(ok), name))
    print(f"{'OK  ' if ok else 'FAIL'} {name:66} {details}", flush=True)


# ================================================================ словарик без окна
def store() -> None:
    cases = {("«Hunter!»", "en"): "hunter", ("HUNTER", "en"): "hunter", ("학교에", "ko"): "학교",
             ("선배님", "ko"): "선배님", ("魔王", "ja"): "魔王", ("Привет,", "ru"): "привет"}
    bad = {w: vocab.base_form(w, l) for (w, l), want in cases.items() if vocab.base_form(w, l) != want}
    check("слово для словарика: без знаков, частиц, строчными", not bad, str(bad))
    check("копируется как есть, без знаков по краям", vocab.clean("«Hunter!»") == "Hunter")

    path = TMP / "vocabulary.json"
    e1, ex1 = vocab.add(vocab.Entry("hunter", "охотник", lang="en", tgt="ru", context="I am a hunter."), path)
    e2, ex2 = vocab.add(vocab.Entry("Hunter", "", lang="en", tgt="ru", context="Hunter!"), path)
    e3, ex3 = vocab.add(vocab.Entry("gate", "", lang="en", tgt="ru"), path)
    check("без дублей: то же слово — та же запись", not ex1 and ex2 and e2.translation == "охотник" and not ex3)
    again = vocab.load(path)
    check("словарик переживает перезапуск, новые сверху", [e.word for e in again] == ["gate", "hunter"]
          and again[1].context == "I am a hunter.", str([e.word for e in again]))
    check("поиск по слову и переводу", [e.word for e in vocab.search(again, "охот")] == ["hunter"])
    csv_text = vocab.to_csv(again)
    check("CSV: первые столбцы — слово и перевод (для Anki)",
          csv_text.splitlines()[0].startswith("word,translation,note") and "hunter,охотник" in csv_text)

    g = Glossary(entries=[GlossaryEntry("강진우", "Кан Джину")])
    check("перевод слова из глоссария — без запроса", vocab.translate_word({}, "강진우", "ko", "ru", glossary=g)
          == ("Кан Джину", "glossary"))

    import app.translators as tmod
    seen = {}

    class Fake:
        title, uses_llm = "Fake", True

        def translate(self, req):
            seen["req"] = req
            return ["врата"]

    real = tmod.create_translator
    tmod.create_translator = lambda name, c: Fake()
    try:
        from app.config import Config
        cfg = Config(TMP / "cfg.json")
        tr_, by = vocab.translate_word(cfg, "gate", "en", "ru", "The gate opened.", "Врата открылись.")
    finally:
        tmod.create_translator = real
    req = seen.get("req")
    check("перевод слова переводчиком; ИИ видит фразу, где слово встретилось",
          (tr_, by) == ("врата", "Fake") and req is not None and req.texts == ["gate"]
          and req.history == [("The gate opened.", "Врата открылись.")])

    line = TextLine(Box(0, 0, 110, 20), "I am here")
    ws = words_of(line)
    check("строка без рамок слов делится по пробелам", [w.text for w in ws] == ["I", "am", "here"]
          and ws[2].box.x > ws[1].box.x > ws[0].box.x and abs(ws[2].box.x2 - 110) < 1)


# ================================================================ наведение в окне
class FakeCursor:
    p = None

    @classmethod
    def pos(cls):
        return cls.p


def hover() -> None:
    from PySide6.QtCore import QPoint
    from PySide6.QtWidgets import QApplication

    from app.capture import Region
    from app.config import Config
    from app.ui import words as words_mod
    from app.ui.main_window import MainWindow
    from app.ui.vocab_window import VocabDialog

    qa = QApplication.instance() or QApplication(sys.argv)
    vocab.PATH = TMP / "hover_vocab.json"
    cfg = Config(TMP / "hover_cfg.json")
    cfg.update(source_lang="en", target_lang="ru", display_mode="overlay")
    win = MainWindow(cfg)
    win.show()
    win.region = Region(win.screen().name(), 100, 100, 600, 400)
    win.overlay.place(win.region)
    # облако «I am a hunter.» с рамками слов (в пикселях картинки; k = 2 — экран с масштабом 200 %)
    k = 2.0
    words = [Word("I", Box(40, 40, 16, 30)), Word("am", Box(70, 40, 50, 30)), Word("a", Box(130, 40, 20, 30)),
             Word("hunter.", Box(160, 40, 120, 30))]
    line = TextLine(Box(40, 40, 240, 30), "I am a hunter.", words=words)
    bubble = Bubble(Box(30, 30, 260, 50), [line], text="I am a hunter.", translation="Я охотник.", lang="en")
    res = PageResult(bubbles=[bubble])
    words_mod.QCursor = FakeCursor
    vocab.translate_word = lambda c, w, l, t, ctx="", ctx_tr="", glossary=None: ("охотник", "Fake")

    def at(img_x: float, img_y: float) -> QPoint:
        return QPoint(int(100 + img_x / k), int(100 + img_y / k))

    def run(sec: float) -> None:
        end = time.time() + sec
        while time.time() < end:
            qa.processEvents()
            time.sleep(0.01)

    hover_ctl = win.words
    FakeCursor.p = at(200, 55)                    # мышь уже лежит на «hunter», когда появляется перевод
    win._on_done(res, k, None)
    run(1.2)
    chips = hover_ctl.chips
    check("мышь просто лежала на слове — кнопки не открылись", chips is None or not chips.isVisible())
    FakeCursor.p = at(90, 55)                     # сдвинули на «am»…
    run(0.15)
    FakeCursor.p = at(205, 52)                    # …и остановили на «hunter»
    run(1.0)
    chips = hover_ctl.chips
    opened = chips is not None and chips.isVisible()
    check("мышь постояла на слове — над ним кнопки, плашка облака спрятана, слово обведено",
          opened and win.overlay.peek_index == 0 and win.overlay.mark is not None
          and hover_ctl.hit is not None and hover_ctl.hit[2].text == "hunter."
          and chips.geometry().bottom() <= hover_ctl.word_rect.top(),
          f"кнопки {chips.geometry().getRect() if chips else None}, слово {hover_ctl.word_rect.getRect()}")
    if opened:
        hover_ctl.copy()
        check("«Копировать» — слово в буфере (без точки)", QApplication.clipboard().text() == "hunter"
              and chips.msg.text().startswith("Скопировано"), QApplication.clipboard().text())
        hover_ctl.open(hover_ctl.hit)
        hover_ctl.add()
        run(0.5)
        saved = vocab.load()
        check("«В словарик» — слово с переводом и примером",
              len(saved) == 1 and saved[0].word == "hunter" and saved[0].translation == "охотник"
              and saved[0].context == "I am a hunter." and saved[0].context_tr == "Я охотник."
              and saved[0].lang == "en" and "в словарике" in chips.msg.text(), chips.msg.text())
        hover_ctl.open(hover_ctl.hit)
        hover_ctl.add()
        check("второй раз — «Уже в словарике», без дубля", chips.msg.text().startswith("Уже в словарике")
              and len(vocab.load()) == 1)
    FakeCursor.p = at(560, 380)                   # ушли далеко
    run(0.8)
    check("мышь ушла — кнопки спрятаны, плашка вернулась", (chips is None or not chips.isVisible())
          and win.overlay.peek_index is None and win.overlay.mark is None)
    cfg["word_hover"] = False
    hover_ctl.sync()
    check("настройка выключает слежение за мышью", not hover_ctl.timer.isActive())
    cfg["word_hover"] = True
    hover_ctl.sync()
    check("и включает снова", hover_ctl.timer.isActive())

    dlg = VocabDialog(win, vocab.PATH)
    dlg.show()
    run(0.1)
    dlg.table.item(0, 2).setText("[ˈhʌntə]")
    check("окно «Словарик»: заметка сохраняется сразу", vocab.load()[0].note == "[ˈhʌntə]")
    dlg.table.selectRow(0)
    from PySide6.QtWidgets import QMessageBox
    QMessageBox.question = staticmethod(lambda *a, **kw: QMessageBox.StandardButton.Yes)
    dlg._delete()
    check("окно «Словарик»: удаление", vocab.load() == [] and dlg.table.rowCount() == 0)
    dlg.close()
    win.close()


def main() -> int:
    store()
    hover()
    failed = [r for r in rows if not r[0]]
    print(f"failed: {len(failed)}")
    return 1 if failed else 0


def _close_qt() -> None:
    """Закрыть окна Qt, пока Python жив (на Windows иначе падение при выходе — см. test_i18n)."""
    try:
        from PySide6.QtCore import QCoreApplication, QEvent
        from PySide6.QtWidgets import QApplication
    except ImportError:
        return
    qa = QApplication.instance()
    if qa is None:
        return
    for w in qa.topLevelWidgets():
        w.close()
        w.deleteLater()
    QCoreApplication.sendPostedEvents(None, int(QEvent.Type.DeferredDelete.value))
    qa.processEvents()


if __name__ == "__main__":
    code = main()
    _close_qt()
    sys.stdout.flush()
    os._exit(code)
