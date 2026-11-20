"""Нагрузка программы и перевод во всех режимах — проверка без экрана.

    QT_QPA_PLATFORM=offscreen python tools/test_resources.py out/

Запускается настоящее главное окно; подменены только три вещи, которых нет без Windows и
интернета: снимок экрана (вместо него — нарисованные страницы), распознавание (отдаёт строки,
которые мы сами нарисовали) и переводчик. Всё остальное — сборка облаков, наклон текста,
автоопределение языка, плашки поверх экрана, Live, компактная панель — работает как у пользователя.

Что проверяется:
* простой: программа не должна тратить процессор, когда ничего не происходит;
* перевод по кнопке («Скриншот») и сам («Live») — для ровного и для наклонённого текста,
  в режимах «Манхва» и «Текст на экране»;
* Live не распознаёт заново, пока страница стоит, и убирает перевод, пока она движется;
* память не растёт от перевода к переводу, потоки не копятся.

Итог: таблица в консоли и в report_resources.txt. Код возврата 1, если что-то не так.
Время распознавания и перевода здесь не настоящее (их делают Windows и сервис перевода) —
измеряется только работа самой программы.
"""

from __future__ import annotations

import collections
import os
import sys
import threading
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("ATLATE_UI_LANG", "ru")   # проверки написаны для русского интерфейса
sys.path.insert(0, str(Path(__file__).resolve().parent))

import numpy as np  # noqa: E402
from PIL import Image  # noqa: E402
from PySide6.QtCore import QEvent, QEventLoop, QObject, QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

import test_studio as ts  # noqa: E402  (нарисованные страницы и их «распознанные» строки)
from app import pipeline as pipeline_module  # noqa: E402
from app.capture import Region  # noqa: E402
from app.config import Config  # noqa: E402
from app.models import Box, TextLine  # noqa: E402
from app.ocr.base import OcrBackend  # noqa: E402
from app.ui import main_window as mw  # noqa: E402
from app.ui import theme  # noqa: E402

out = ts.out
IDLE_LIMIT = 3.0        # % одного ядра: больше в простое быть не должно
LIVE_LIMIT = 15.0       # % одного ядра, пока Live просто следит за неподвижной страницей
LEAK_LIMIT_MB = 25.0


# ---------------------------------------------------------------- подмены

PAGES: dict[str, tuple[Image.Image, list[TextLine], str]] = {}     # имя → (картинка, строки, язык)
screen = {"page": "", "image": None, "moving": False}
calls = collections.Counter()

# наклон абзацев «статьи»: первое слово абзаца → угол
ARTICLE_WANT = {"Breaking": 0, "The": 0, "This": 12, "LIMITED": -18, "Another": 0, "Tilted": 6}
ARTICLE_RU = {"Breaking": "Главные новости дня", "The": "Быстрая бурая лиса убегает далеко в тёмный лес.",
              "This": "Эту записку прикололи к доске под углом.", "LIMITED": "ОГРАНИЧЕННОЕ ПРЕДЛОЖЕНИЕ",
              "Another": "Ещё один обычный абзац простого текста.", "Tilted": "Наклонная подпись внизу"}


def article_page(w: int = 900, h: int = 700) -> tuple[Image.Image, list[TextLine]]:
    """«Статья»: заголовок и абзацы на светлом фоне, часть — под углом (заметка, плашка, подпись)."""
    from PIL import ImageDraw
    img = Image.new("RGB", (w, h), (250, 250, 250))
    d = ImageDraw.Draw(img)
    truth: list[TextLine] = []

    def put(lines, cx, cy, deg, size, fill=(20, 20, 20)):
        ts.write_tilted(img, lines, cx, cy, deg, ts.font_for("en", size), fill, truth)

    put(["Breaking news of the day"], 450, 50, 0, 32)
    put(["The quick brown fox jumps over the lazy dog and", "runs far away into the deep dark forest where",
         "nobody has ever seen it again since that day."], 450, 150, 0, 20)
    d.rectangle((40, 250, 440, 470), fill=(255, 240, 180))
    put(["This note was pinned to", "the board at an angle,", "so read it carefully."], 240, 360, 12, 20)
    d.rectangle((500, 260, 860, 460), fill=(40, 60, 110))
    put(["LIMITED OFFER", "only this week"], 680, 360, -18, 26, fill=(255, 255, 255))
    put(["Another ordinary paragraph of plain text that", "sits straight at the bottom of the page."], 450, 560, 0, 20)
    put(["Tilted footer caption here"], 450, 650, 6, 20)
    return img, truth


def show(name: str, moving: bool = False) -> None:
    """Что сейчас «на экране». moving — страницу прокручивают: каждый снимок отличается от прошлого."""
    screen["page"], screen["image"], screen["moving"] = name, PAGES[name][0], moving


def fake_grab(region):
    calls["grab"] += 1
    img = screen["image"]
    if screen["moving"]:
        img = Image.fromarray(np.roll(np.asarray(img), 45 * calls["grab"], axis=0))
    return img.copy(), 1.0


def fake_grab_raw(region):
    """То же, но как QImage — так снимок получает Live."""
    from PySide6.QtGui import QImage
    img, k = fake_grab(region)
    arr = np.ascontiguousarray(np.asarray(img.convert("RGB")))
    return QImage(arr.data, arr.shape[1], arr.shape[0], 3 * arr.shape[1], QImage.Format.Format_RGB888).copy(), k


class ScreenOcr(OcrBackend):
    """Отдаёт строки, нарисованные на текущей странице, — но только «своему» языку."""

    name = "fake"
    supports_parallel = True

    def recognize(self, image, lang):
        calls["ocr"] += 1
        _img, truth, page_lang = PAGES[screen["page"]]
        if lang != page_lang:
            return []       # чужой распознаватель этот текст не читает
        return [TextLine(Box(l.box.x, l.box.y, l.box.w, l.box.h), l.text) for l in truth]


class CountingTranslator(ts.FakeTranslator):
    def translate(self, req):
        calls["translate"] += 1
        return [ARTICLE_RU.get(t.split()[0]) or r for t, r in zip(req.texts, super().translate(req))]


# ---------------------------------------------------------------- измерения

def rss_mb() -> float:
    try:
        for line in Path("/proc/self/status").read_text().splitlines():
            if line.startswith("VmRSS:"):
                return int(line.split()[1]) / 1024
    except OSError:
        pass
    return 0.0


def os_threads() -> int:
    try:
        for line in Path("/proc/self/status").read_text().splitlines():
            if line.startswith("Threads:"):
                return int(line.split()[1])
    except OSError:
        pass
    return threading.active_count()


class Meter:
    """Время и процессор (всех потоков программы) за участок работы."""

    def __enter__(self):
        self.t, self.c = time.perf_counter(), time.process_time()
        return self

    def __exit__(self, *exc):
        self.wall = time.perf_counter() - self.t
        self.cpu = time.process_time() - self.c
        self.percent = 100 * self.cpu / max(self.wall, 1e-9)
        return False


def spin(seconds: float) -> None:
    """Дать программе поработать: обычное ожидание событий, как у пользователя."""
    loop = QEventLoop()
    QTimer.singleShot(int(seconds * 1000), loop.quit)
    loop.exec()


def wait(cond, timeout: float = 10.0) -> bool:
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        spin(0.02)
    return cond()


class LayoutSpy(QObject):
    """Считает запросы раскладки: в простое их быть не должно."""

    def __init__(self):
        super().__init__()
        self.n = 0

    def eventFilter(self, _obj, ev):
        if ev.type() == QEvent.Type.LayoutRequest:
            self.n += 1
        return False


rows: list[tuple[str, bool, str]] = []


def record(name: str, ok: bool, details: str) -> None:
    rows.append((name, ok, details))
    print(f"{'OK  ' if ok else 'FAIL'} {name:58} {details}", flush=True)


# ---------------------------------------------------------------- сценарии

def main() -> int:
    app = QApplication.instance() or QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    cfg_path = out / "config_resources.json"
    cfg_path.unlink(missing_ok=True)
    cfg = Config(cfg_path)
    cfg.update({"ocr_mode": "thorough", "context_size": 0, "request_interval": 0, "auto_interval_ms": 300,
                "skip_cut_bubbles": False, "source_lang": "en", "mini_collapse_delay": 1})
    theme.apply(app, "light")

    img, truth = ts.page("en")
    PAGES["straight"] = (img, truth, "en")
    img, truth = ts.tilted_page()
    PAGES["tilted"] = (img, truth, "en")
    img, truth = ts.page("ko")
    PAGES["korean"] = (img, truth, "ko")
    img, truth = article_page()
    PAGES["article"] = (img, truth, "en")
    show("straight")

    mw.grab_region = fake_grab
    mw.grab_region_raw = fake_grab_raw
    pipeline_module.create_translator = lambda key, c: CountingTranslator({})
    rss_start = rss_mb()
    win = mw.MainWindow(cfg)
    win.pipeline._ocr = ScreenOcr(cfg)
    win.pipeline._ocr_name = cfg["ocr_engine"]
    win.region = Region(win.screen().name() if win.screen() else "", 0, 0, 800, 1150)
    win.overlay.place(win.region)
    win.show()
    results: list = []
    done = win._on_done

    def on_done(result, k, token):
        done(result, k, token)
        results.append(result)

    win._on_done = on_done
    spin(1.0)
    spy = LayoutSpy()
    app.installEventFilter(spy)

    # ------------------------------------------------------------ простой
    def idle(name: str, seconds: float = 3.0, limit: float = IDLE_LIMIT) -> None:
        spin(0.5)
        spy.n = 0
        with Meter() as m:
            spin(seconds)
        record(f"простой: {name}", m.percent <= limit and spy.n < 50,
               f"процессор {m.percent:5.2f}% ядра · запросов раскладки {spy.n} · память {rss_mb():.0f} МБ")

    idle("главное окно")

    # ------------------------------------------------------------ перевод по кнопке
    def translated(result) -> int:
        return sum(1 for b in result.bubbles if b.translation)

    def check_tilt(result, page: str) -> str:
        """Пусто, если наклон у всех облаков верный; иначе — что не так."""
        bad = []
        table = {"tilted": ts.TILT_WANT, "article": ARTICLE_WANT}.get(page, {})
        for b in result.bubbles:
            want = table.get(b.text.split()[0], 0) if b.text.split() else 0
            if abs(b.angle - want) > 2:
                bad.append(f"{b.text[:14]!r}: {b.angle:+.0f}° вместо {want:+}°")
        return "; ".join(bad)

    def shot(page: str, mode: str, expect: int, label: str) -> None:
        cfg["content_mode"] = mode
        cfg["translate_mode"] = "shot"
        show(page)
        n = len(results)
        with Meter() as m:
            win.translate_now()
            ok = wait(lambda: len(results) > n and not win.busy)
        if not ok:
            record(label, False, "перевод не закончился")
            return
        r = results[-1]
        tilt = check_tilt(r, page)
        shown = win.overlay.isVisible() and win.overlay.result is r
        tilted = sum(1 for b in r.bubbles if b.angle)
        good = translated(r) == expect and not tilt and shown and not r.error
        record(label, good, f"облаков {translated(r)}/{expect} · наклонённых {tilted} · {m.wall * 1000:4.0f} мс, "
                            f"процессор {m.cpu * 1000:4.0f} мс" + (f" · {tilt}" if tilt else "")
               + ("" if shown else " · перевод не показан") + (f" · {r.error}" if r.error else ""))

    shot("straight", "manhwa", 6, "Скриншот · манхва · ровный текст")
    shot("tilted", "manhwa", 6, "Скриншот · манхва · наклонённый текст")
    shot("straight", "screen", 6, "Скриншот · текст на экране · ровный текст")
    shot("article", "screen", 6, "Скриншот · текст на экране · наклонённый текст")

    # ------------------------------------------------------------ Live
    def live(mode: str, label: str, tilted: str) -> None:
        cfg["content_mode"] = mode
        show("straight")
        win._set_mode("live")
        n = len(results)
        win.set_live(True)
        ok = wait(lambda: len(results) > n and not win.busy, 6)
        r = results[-1] if ok else None
        record(f"Live · {label} · ровный текст", bool(ok and translated(r) == 6 and not check_tilt(r, "straight")),
               f"облаков {translated(r) if r else 0}/6 · перевод появился сам" if ok else "перевод не появился")

        # страница стоит: Live только следит, заново не распознаёт
        before = calls["ocr"]
        with Meter() as m:
            spin(3.0)
        record(f"Live · {label} · страница стоит", calls["ocr"] == before and m.percent <= LIVE_LIMIT,
               f"процессор {m.percent:5.2f}% ядра · лишних распознаваний {calls['ocr'] - before}")

        # страница движется: перевод убирается и не распознаётся, пока не остановится
        before, n = calls["ocr"], len(results)
        show(tilted, moving=True)
        spin(1.8)
        cleared = win.overlay.result is None
        moving_ocr = calls["ocr"] - before
        show(tilted)
        ok = wait(lambda: len(results) > n and not win.busy, 6)
        r = results[-1] if ok else None
        tilt = check_tilt(r, tilted) if r else "нет результата"
        record(f"Live · {label} · прокрутка и наклонённый текст",
               bool(ok and cleared and moving_ocr == 0 and translated(r) == 6 and not tilt),
               f"облаков {translated(r) if r else 0}/6 · при движении перевод убран: {'да' if cleared else 'НЕТ'}, "
               f"распознаваний на ходу {moving_ocr}" + (f" · {tilt}" if tilt else ""))
        win.set_live(False)
        win._set_mode("shot")

    live("manhwa", "манхва", "tilted")
    live("screen", "текст на экране", "article")
    cfg["content_mode"] = "manhwa"

    # ------------------------------------------------------------ автоопределение языка
    cfg["auto_lang"] = True
    win.pipeline.reset_auto()
    show("korean")
    n = len(results)
    win.translate_now()
    ok = wait(lambda: len(results) > n and not win.busy)
    r = results[-1] if ok else None
    record("Авто-язык · корейская страница при выбранном английском",
           bool(ok and cfg["source_lang"] == "ko" and r.detected_lang == "ko" and translated(r) >= 6),
           f"язык стал {cfg['source_lang']} · облаков {translated(r) if r else 0}")
    before, n = calls["ocr"], len(results)
    win.translate_now()
    wait(lambda: len(results) > n and not win.busy)
    passes = calls["ocr"] - before
    record("Авто-язык · тот же язык повторно: без перебора языков", passes == 3,
           f"распознаваний {passes} (три прохода «тщательного» режима)")
    cfg["auto_lang"] = False
    cfg["source_lang"] = "en"

    # ------------------------------------------------------------ компактная панель
    win.enter_mini()
    spin(0.3)
    show("tilted")
    n = len(results)
    win._mini_action("main")
    ok = wait(lambda: len(results) > n and not win.busy)
    r = results[-1] if ok else None
    record("Компактная панель · перевод с панели", bool(ok and translated(r) == 6 and not check_tilt(r, "tilted")),
           f"облаков {translated(r) if r else 0}/6 · главное окно скрыто: {'да' if not win.isVisible() else 'НЕТ'}")
    idle("компактная панель (перевод показан поверх экрана)")
    win.exit_mini()
    spin(0.3)

    # ------------------------------------------------------------ память и потоки
    threads_before = os_threads()
    marks = []
    for i in range(40):
        show("tilted" if i % 2 else "straight")
        n = len(results)
        win.translate_now()
        wait(lambda: len(results) > n and not win.busy)
        if i in (9, 39):
            marks.append(rss_mb())
    growth = marks[1] - marks[0]
    record("память: 40 переводов подряд", growth <= LEAK_LIMIT_MB,
           f"после 10-го {marks[0]:.0f} МБ, после 40-го {marks[1]:.0f} МБ (прирост {growth:+.1f} МБ)")
    record("потоки: не копятся", os_threads() <= threads_before + 1,
           f"было {threads_before}, стало {os_threads()}")

    # ------------------------------------------------------------ простой после работы
    idle("главное окно после всех переводов")
    win.toggle_overlay()
    idle("перевод скрыт")
    win.open_studio()
    spin(1.0)
    idle("открыта Студия (без главы)")
    win.studio.close()

    peak = 0.0
    try:
        import resource
        peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024
    except ImportError:
        pass
    total = (f"память: при запуске {rss_start:.0f} МБ, сейчас {rss_mb():.0f} МБ, пик {peak:.0f} МБ · "
             f"потоков {os_threads()} · снимков экрана {calls['grab']}, распознаваний {calls['ocr']}, "
             f"запросов перевода {calls['translate']}")
    print(total)
    failed = [r for r in rows if not r[1]]
    lines = [f"{'OK  ' if ok else 'FAIL'} {name:58} {details}" for name, ok, details in rows]
    (out / "report_resources.txt").write_text("\n".join(lines + [total, f"failed: {len(failed)}"]), encoding="utf-8")
    print(f"failed: {len(failed)}")
    return 1 if failed else 0


if __name__ == "__main__":
    code = main()
    sys.stdout.flush()
    os._exit(code)      # окно и его фоновые потоки закрывать незачем: всё уже проверено
