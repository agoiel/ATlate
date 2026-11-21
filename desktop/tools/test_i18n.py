"""Язык интерфейса ПК-версии: всё переведено, перевод подставляется, язык берётся из системы.

    QT_QPA_PLATFORM=offscreen python tools/test_i18n.py out/

* у каждой строки tr("…") в коде есть английский перевод с теми же {0}, {1:.1f}…;
* в словаре нет лишних строк;
* русский текст в коде — только внутри tr() (кроме служебных строк из списка ниже);
* «как в системе»: русский для ru/uk/be/kk/uz/ky/tg, английский для остальных;
* настоящие окна (главное, Студия, редактор глоссария, компактная панель, список) в английском
  интерфейсе не показывают ни одной русской буквы.
Код возврата 1 при ошибках.
"""

from __future__ import annotations

import ast
import os
import re
import string
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ["ATLATE_UI_LANG"] = "en"                 # до импорта программы: язык выбирается при загрузке
os.environ.setdefault("ATLATE_NO_UPDATE_CHECK", "1")   # проверки не ходят в сеть за обновлениями
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

out = Path(sys.argv[1] if len(sys.argv) > 1 else "out")
out.mkdir(parents=True, exist_ok=True)
CYR = re.compile(r"[А-Яа-яЁё]")
rows: list[tuple[bool, str]] = []

# служебные строки, которые не показываются как есть (сравниваются в коде, уходят в запрос к ИИ…)
NOT_UI = {
    ("app/hotkeys.py", "йцукенгшщзхъфывапролджэячсмитьбю"),   # русские буквы на месте латинских клавиш
    ("app/__init__.py", "Pillow загрузил модуль шрифтов раньше"),
    ("app/bubbles.py", "нет букв"),                          # причины пропуска: переводятся при показе
    ("app/bubbles.py", "текст поверх рисунка"),
    ("app/bubbles.py", "обрезано краем области"),
    ("app/pipeline.py", "текст поверх рисунка"),
    ("app/studio/typeset.py", "Ду"),                         # замер высоты строки
    ("app/translators/llm.py", "You are an experienced translator"),   # запрос к ИИ
    ("app/ui/main_window.py", "Русский"),                    # язык интерфейса — на самом языке
    ("app/voice.py", "продолжение следует"),                 # выдумки Whisper на тишине — сравниваются
    ("app/voice.py", "субтитр"),
    ("app/voice.py", "Мид, топ"),                            # подсказка распознаванию: игровые слова
}
# строки, которые переводятся через переменную, а не tr("…")
DYNAMIC = {"нет букв", "текст поверх рисунка", "обрезано краем области",
           "Универсальный (корейский)", "Универсальный (английский)", "Пример (корейский)"}


def check(name: str, ok: bool, details: str = "") -> None:
    rows.append((ok, name))
    print(f"{'OK  ' if ok else 'FAIL'} {name:62} {details}", flush=True)


def app_files() -> list[Path]:
    return [p for p in sorted((ROOT / "app").rglob("*.py")) if p.name not in ("i18n.py", "i18n_en.py")]


def fields(s: str) -> list[tuple[str, str]]:
    return sorted((f or "", spec or "") for _, f, spec, _ in string.Formatter().parse(s) if f is not None)


def static() -> None:
    from app.i18n_en import EN
    keys: dict[str, str] = {}
    bare: list[str] = []
    for p in app_files():
        rel = p.relative_to(ROOT).as_posix()
        tree = ast.parse(p.read_text(encoding="utf-8"))
        parents = {c: n for n in ast.walk(tree) for c in ast.iter_child_nodes(n)}
        inside_tr = set()
        for n in ast.walk(tree):
            if isinstance(n, ast.Call) and getattr(n.func, "id", "") == "tr":
                if n.args and isinstance(n.args[0], ast.Constant) and isinstance(n.args[0].value, str):
                    keys.setdefault(n.args[0].value, f"{rel}:{n.lineno}")
                inside_tr.update(id(a) for a in ast.walk(n.args[0]) if n.args)
        for n in ast.walk(tree):
            if isinstance(n, ast.Constant) and isinstance(n.value, str) and CYR.search(n.value):
                par = parents.get(n)
                if id(n) in inside_tr or isinstance(par, ast.Expr) or isinstance(par, (ast.JoinedStr, ast.FormattedValue)):
                    continue
                if any(rel == f and n.value.startswith(t) for f, t in NOT_UI):
                    continue
                bare.append(f"{rel}:{n.lineno} {n.value[:50]!r}")
            if isinstance(n, ast.JoinedStr) and not isinstance(parents.get(n), ast.FormattedValue):
                text = "".join(v.value for v in n.values if isinstance(v, ast.Constant))
                if CYR.search(text) and not any(rel == f and text.startswith(t) for f, t in NOT_UI):
                    bare.append(f"{rel}:{n.lineno} f{text[:50]!r}")
    missing = [f"{w} {k[:50]!r}" for k, w in keys.items() if k not in EN]
    check("у каждой строки интерфейса есть английский перевод", not missing,
          f"строк {len(keys)}, без перевода {len(missing)}")
    for m in missing[:20]:
        print("     нет перевода:", m)
    bad_fields = [k for k, v in EN.items() if fields(k) != fields(v)]
    check("в переводах те же подстановки {0}, {1:.1f}…", not bad_fields, f"{len(bad_fields)}")
    for k in bad_fields[:10]:
        print("     подстановки:", repr(k[:50]), "→", repr(EN[k][:50]))
    extra = [k for k in EN if k not in keys and k not in DYNAMIC]
    check("в словаре нет лишних строк", not extra, f"{len(extra)}")
    for k in extra[:10]:
        print("     лишняя:", repr(k[:60]))
    check("русский текст в коде — только внутри tr()", not bare, f"{len(bare)}")
    for b in bare[:20]:
        print("     без tr():", b)
    cyr_en = [v for v in EN.values() if CYR.search(v)]
    check("в английских переводах нет русских букв", not cyr_en, f"{len(cyr_en)}")


def system_choice() -> None:
    from app import i18n
    saved = i18n.system_language
    try:
        res = {}
        for code in ("ru", "uk", "be", "kk", "en", "de", "es", "ja", "ko"):
            i18n.system_language = lambda c=code: c
            res[code] = "en" if i18n.resolve("system") else "ru"
        ok = all(res[c] == "ru" for c in ("ru", "uk", "be", "kk")) and all(
            res[c] == "en" for c in ("en", "de", "es", "ja", "ko"))
        check("«как в системе»: русский для ru/uk/be/kk, иначе английский", ok, str(res))
        check("выбор вручную важнее системы", i18n.resolve("ru") is False and i18n.resolve("en") is True)
    finally:
        i18n.system_language = saved
    print(f"     язык этой системы: {i18n.system_language()}")


def widget_texts(root) -> list[tuple[str, str]]:
    """Все надписи окна: тексты, подсказки, пункты списков, заголовки, строки в полях виджетов."""
    from PySide6.QtWidgets import QComboBox, QTableWidget, QWidget
    found: list[tuple[str, str]] = []
    ws = [root] + root.findChildren(QWidget)
    for w in ws:
        name = f"{type(w).__name__}#{w.objectName()}"
        for getter in ("text", "placeholderText", "toolTip", "windowTitle", "title", "statusTip"):
            f = getattr(w, getter, None)
            if callable(f):
                try:
                    v = f()
                except TypeError:
                    continue
                if isinstance(v, str) and v:
                    found.append((name, v))
        if isinstance(w, QComboBox):
            found += [(name, w.itemText(i)) for i in range(w.count())]
        if isinstance(w, QTableWidget):
            for i in range(w.columnCount()):
                item = w.horizontalHeaderItem(i)
                if item is not None:
                    found.append((name, item.text()))
        for k, v in vars(w).items():
            vals = v if isinstance(v, (list, tuple)) else [v]
            for x in vals:
                for y in (x if isinstance(x, tuple) else (x,)):
                    if isinstance(y, str) and y:
                        found.append((f"{name}.{k}", y))
        for a in w.actions():
            if a.text():
                found.append((f"{name}.action", a.text()))
    return found


def windows() -> None:
    import json
    import tempfile

    from PySide6.QtWidgets import QApplication

    from app import i18n
    from app.config import Config
    from app.ui import theme
    from app.ui.main_window import MainWindow

    check("программа загрузилась с английским интерфейсом", i18n.english)
    global _APP
    _APP = app = QApplication.instance() or QApplication(sys.argv)     # живёт до конца проверки
    tmp = Path(tempfile.mkdtemp())
    cfg = Config(tmp / "config.json")
    cfg["dev_mode"] = True
    theme.apply(app, "light")
    allowed = {"Русский"}           # язык интерфейса в списке называется на самом языке

    def verify(title: str, win) -> None:
        for _ in range(5):
            app.processEvents()
        bad = sorted({f"{n}: {t[:60]!r}" for n, t in widget_texts(win) if CYR.search(t) and t not in allowed})
        check(f"{title}: нет русского текста", not bad, f"{len(bad)}")
        for b in bad[:15]:
            print("     ", b)

    win = MainWindow(cfg)
    win.resize(1000, 900)
    win.show()
    verify("главное окно", win)
    win.grab().save(str(out / "main_en.png"))
    win._update = ("9.9.9", "https://github.com/agoiel/ATlate/releases/latest")
    win._rebuild()
    verify("главное окно с плашкой обновления", win)
    win.show_list = getattr(win, "show_list", None)
    verify("окно со списком", win.list_window)

    from app.studio.window import StudioWindow
    studio = StudioWindow(cfg)
    studio.resize(1200, 800)
    studio.show()
    verify("Студия", studio)
    studio.grab().save(str(out / "studio_en.png"))
    studio.close()

    from app.ui.glossary_editor import GlossaryEditor
    g = tmp / "g.json"
    g.write_text(json.dumps({"name": "t", "source_lang": "ko", "entries": [
        {"source": "민준", "target": "Minjun", "note": "", "auto": True}]}), encoding="utf-8")
    ed = GlossaryEditor(g)
    ed.show()
    verify("редактор глоссария", ed)
    ed.close()

    from app import history
    from app.models import Box, Bubble, PageResult
    from app.ui.history_window import HistoryDialog
    hp = tmp / "history.jsonl"
    history.add(PageResult(bubbles=[Bubble(Box(0, 0, 1, 1), [], text="민준", translation="Minjun")]), cfg, "t", hp)
    hd = HistoryDialog(win, hp)
    hd.show()
    verify("история переводов", hd)
    hd.close()

    from app.models import Box, Bubble, PageResult
    win.shown_result = PageResult(bubbles=[Bubble(Box(0, 0, 10, 10), [], text="a", translation="b")])
    win.enter_mini()
    for _ in range(5):
        app.processEvents()
    if win.mini is not None:
        verify("компактная панель", win.mini)
        hints = [it.hint for it in win.mini._items("translate") if it.hint]
        check("компактная панель: подсказка значка на английском", hints and not any(CYR.search(h) for h in hints),
              str(hints))
        menu = getattr(win.mini, "_menu_items", None)
        if callable(menu):
            texts = [t for t in (getattr(i, "text", "") for i in menu()) if isinstance(t, str)]
            bad = [t for t in texts if CYR.search(t)]
            check("компактная панель: пункты меню", not bad, str(bad[:5]))
    win.close()


def main() -> int:
    static()
    system_choice()
    windows()
    failed = [r for r in rows if not r[0]]
    print(f"failed: {len(failed)}")
    return 1 if failed else 0


def _close_qt() -> None:
    """Закрыть и удалить все окна Qt, пока Python жив. Иначе на Windows Qt разбирает их при выходе
    из процесса и зовёт обработчики Python, которых уже нет (падение после «failed: 0»)."""
    from PySide6.QtCore import QCoreApplication, QEvent
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance()
    if app is None:
        return
    for w in app.topLevelWidgets():
        w.close()
        w.deleteLater()
    QCoreApplication.sendPostedEvents(None, int(QEvent.Type.DeferredDelete.value))
    app.processEvents()


_APP = None

if __name__ == "__main__":
    code = main()
    _close_qt()
    sys.stdout.flush()
    os._exit(code)
