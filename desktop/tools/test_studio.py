"""Проверка Студии на нарисованных страницах: найти → перевести → стереть → вписать.

    python tools/test_studio.py out/            # поддельное распознавание (где угодно)
    python tools/test_studio.py out/ --real     # настоящее распознавание Windows (только Windows)

Страницы рисуются здесь же: облака с корейским или английским текстом, тёмная плашка,
надпись поверх пёстрого рисунка и длинная вебтун-лента. Перевод подставной, чтобы не
зависеть от интернета. Итог: картинки до/после и отчёт report_studio.txt.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np  # noqa: E402
from PIL import Image, ImageDraw, ImageFont  # noqa: E402

from app.config import Config  # noqa: E402
from app.models import Box, TextLine  # noqa: E402
from app.ocr.base import OcrBackend  # noqa: E402
from app.pipeline import Pipeline  # noqa: E402
from app.studio import engine, fonts  # noqa: E402
from app.studio.project import Project  # noqa: E402
from app.translators.base import Translator  # noqa: E402

out = Path(sys.argv[1] if len(sys.argv) > 1 and not sys.argv[1].startswith("-") else "out")
REAL = "--real" in sys.argv
out.mkdir(parents=True, exist_ok=True)

KO = {
    "어디 가는 거야?": "Куда ты собрался?",
    "여기서 기다리라고 했잖아!": "Я же сказал ждать здесь!",
    "북쪽 성문": "Северные ворота",
    "그는 돌아오지 않아.": "Он не вернётся.",
    "쾅!": "БАХ!",
    "정말 괜찮겠어? 저 녀석은 위험해.": "Ты точно в порядке? Этот парень опасен.",
}
EN = {
    "Where are you going?": "Куда ты собрался?",
    "I told you to wait here!": "Я же сказал ждать здесь!",
    "THE NORTHERN GATE": "СЕВЕРНЫЕ ВОРОТА",
    "He is not coming back.": "Он не вернётся.",
    "BANG!": "БАХ!",
    "Are you sure you're okay? That guy is dangerous.": "Ты точно в порядке? Этот парень опасен.",
}


def font_for(lang: str, size: int):
    cands = (["C:/Windows/Fonts/arialbd.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"] if lang == "en"
             else ["C:/Windows/Fonts/malgunbd.ttf", "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc"])
    for c in cands:
        if Path(c).exists():
            return ImageFont.truetype(c, size)
    return ImageFont.load_default(size)


TRUTH: dict[str, list[TextLine]] = {}


def write(d: ImageDraw.ImageDraw, lines: list[str], cx: float, cy: float, font, fill, truth: list, dy: int = 0,
          stroke=None) -> None:
    lh = font.size * 1.25
    y0 = cy - len(lines) * lh / 2
    for i, t in enumerate(lines):
        y = y0 + i * lh + dy
        d.text((cx, y + lh / 2), t, font=font, fill=fill, anchor="mm", stroke_width=3 if stroke else 0,
               stroke_fill=stroke)
        x1, y1, x2, y2 = d.textbbox((cx, y + lh / 2), t, font=font, anchor="mm")
        truth.append(TextLine(Box(x1, y1, x2 - x1, y2 - y1), t))


def background(w: int, h: int, seed: int) -> Image.Image:
    """«Рисунок»: плавный градиент с пятнами."""
    rng = np.random.default_rng(seed)
    yy, xx = np.mgrid[0:h, 0:w]
    base = np.stack([150 + 60 * np.sin(xx / 90 + yy / 140), 140 + 50 * np.cos(yy / 70),
                     170 + 40 * np.sin((xx + yy) / 110)], axis=2)
    base += rng.normal(0, 6, base.shape)
    return Image.fromarray(np.clip(base, 0, 255).astype(np.uint8))


def page(lang: str, w: int = 800, h: int = 1150, seed: int = 1) -> tuple[Image.Image, list[TextLine]]:
    img = background(w, h, seed)
    d = ImageDraw.Draw(img)
    truth: list[TextLine] = []
    f = font_for(lang, 26)
    # облако-овал
    d.ellipse((60, 50, 420, 270), fill="white", outline="black", width=4)
    write(d, ["어디 가는 거야?"] if lang == "ko" else ["Where are", "you going?"], 240, 160, f, "black", truth)
    # облако с хвостиком, текст в две строки
    d.ellipse((420, 300, 760, 560), fill="white", outline="black", width=4)
    d.polygon([(500, 540), (470, 620), (560, 550)], fill="white", outline="black")
    d.line([(500, 548), (470, 620), (560, 553)], fill="black", width=4)
    write(d, ["여기서 기다리라고", "했잖아!"] if lang == "ko" else ["I told you to", "wait here!"], 590, 430, f,
          "black", truth)
    # тёмная плашка рассказчика
    d.rounded_rectangle((40, 330, 380, 420), 10, fill=(35, 30, 45), outline=(200, 190, 160), width=3)
    write(d, ["북쪽 성문"] if lang == "ko" else ["THE NORTHERN GATE"], 210, 375, f, (240, 235, 220), truth)
    # большое облако, три строки
    d.ellipse((90, 640, 560, 960), fill=(250, 250, 248), outline="black", width=4)
    write(d, ["정말 괜찮겠어?", "저 녀석은", "위험해."] if lang == "ko"
          else ["Are you sure", "you're okay? That", "guy is dangerous."], 325, 800, f, "black", truth)
    # звук поверх рисунка
    write(d, ["쾅!"] if lang == "ko" else ["BANG!"], 670, 820, font_for(lang, 64), (250, 240, 90), truth,
          stroke=(40, 20, 10))
    # плавающая подпись без облака на тёмном фоне
    d.rectangle((0, 1010, w, h), fill=(25, 25, 30))
    write(d, ["그는 돌아오지 않아."] if lang == "ko" else ["He is not coming back."], 400, 1080, f, "white", truth)
    return img, truth


# наклонённый текст: что написано и под каким углом (плюс — правый край выше)
TILTED = [
    ("bubble", (50, 40, 400, 290), ["Where are you", "going?"], 15, 30, "black"),
    ("bubble", (420, 60, 770, 320), ["I told you to", "wait here!"], -20, 32, "black"),
    ("bubble", (60, 350, 400, 560), ["He is not", "coming back."], 0, 30, "black"),
    ("bubble", (60, 620, 440, 900), ["Are you sure", "you're okay? That", "guy is dangerous."], 30, 24, "black"),
    ("box", (450, 400, 780, 540), ["THE NORTHERN GATE"], -10, 24, "white"),
    ("free", (590, 760, 0, 0), ["BANG!"], 22, 64, (250, 240, 90)),
]
TILT_WANT = {"Where": 15, "I": -20, "He": 0, "Are": 30, "THE": -10, "BANG!": 22}


def write_tilted(img: Image.Image, lines: list[str], cx: float, cy: float, deg: float, font, fill, truth: list,
                 stroke=None) -> None:
    """Строки под углом: рисуются ровно на прозрачном слое, слой поворачивается вокруг центра блока."""
    lh = font.size * 1.25
    half = 420
    for i, t in enumerate(lines):
        tmp = Image.new("RGBA", (2 * half, 2 * half), (0, 0, 0, 0))
        ImageDraw.Draw(tmp).text((half, half + (i + 0.5 - len(lines) / 2) * lh), t, font=font, fill=fill, anchor="mm",
                                 stroke_width=4 if stroke else 0, stroke_fill=stroke)
        tmp = tmp.rotate(deg, Image.BICUBIC)
        layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
        layer.paste(tmp, (int(cx - half), int(cy - half)), tmp)
        x1, y1, x2, y2 = layer.getbbox()
        # распознавание отдаёт прямую рамку наклонённой строки
        truth.append(TextLine(Box(x1, y1, x2 - x1, y2 - y1), t))
        img.paste(layer, (0, 0), layer)


def tilted_page(w: int = 800, h: int = 950) -> tuple[Image.Image, list[TextLine]]:
    """Страница, где реплики и звук стоят под разными углами."""
    img = background(w, h, 9)
    d = ImageDraw.Draw(img)
    truth: list[TextLine] = []
    for kind, box, lines, deg, size, ink in TILTED:
        f = font_for("en", size)
        if kind == "free":
            write_tilted(img, lines, box[0], box[1], deg, f, ink, truth, stroke=(40, 20, 10))
            continue
        if kind == "bubble":
            d.ellipse(box, fill=(252, 252, 250), outline="black", width=4)
        else:
            d.rectangle(box, fill=(35, 30, 45), outline=(200, 190, 160), width=3)
        write_tilted(img, lines, (box[0] + box[2]) / 2, (box[1] + box[3]) / 2, deg, f, ink, truth)
    return img, truth


class FakeOcr(OcrBackend):
    """Возвращает строки, которые мы сами нарисовали (с учётом куска картинки)."""

    name = "fake"
    supports_parallel = True

    def recognize(self, image, lang):
        key = getattr(image, "_studio_key", None)
        return [TextLine(Box(l.box.x, l.box.y, l.box.w, l.box.h), l.text) for l in TRUTH.get(key, [])]


class FakeTranslator(Translator):
    name = "fake"

    def translate(self, req):
        table = {**KO, **EN}
        joined = {k.replace(" ", ""): v for k, v in table.items()}
        return [table.get(t) or joined.get(t.replace(" ", ""), f"[{t}]") for t in req.texts]


def main() -> int:
    cfg = Config(out / "config.json")
    cfg["ocr_mode"] = "fast"
    cfg["context_size"] = 0
    cfg["request_interval"] = 0
    pipe = Pipeline(cfg)
    pipe._translator = FakeTranslator({})
    pipe._translator_key = cfg["translator"]
    chapter = out / "chapter"
    shutil.rmtree(chapter, ignore_errors=True)
    chapter.mkdir()

    pages = {"01_ko.png": page("ko", seed=1), "02_en.png": page("en", seed=2)}
    # длинная лента: три страницы друг под другом
    strip_parts = [page("en", seed=3 + i) for i in range(3)]
    strip = Image.new("RGB", (800, 1150 * 3))
    truth: list[TextLine] = []
    for i, (im, tr) in enumerate(strip_parts):
        strip.paste(im, (0, 1150 * i))
        truth += [TextLine(Box(l.box.x, l.box.y + 1150 * i, l.box.w, l.box.h), l.text) for l in tr]
    pages["03_strip.png"] = (strip, truth)
    pages["04_tilt_en.png"] = tilted_page()
    for name, (im, tr) in pages.items():
        im.save(chapter / name)

    if not REAL:
        # подделка распознавания: по куску картинки находим нарисованные в нём строки
        fake = FakeOcr(cfg)
        pipe._ocr = fake
        pipe._ocr_name = cfg["ocr_engine"]
        current = {}

        def fake_recognize(image, lang, mode, parallel, live=False, **_kw):
            arr = np.asarray(image)
            name, (full, tr) = current["page"]
            full_arr = np.asarray(full)
            # где этот кусок в странице (кусок ленты)
            y0 = 0
            if arr.shape[0] != full_arr.shape[0]:
                for y in range(0, full_arr.shape[0] - arr.shape[0] + 1, 1):
                    if np.array_equal(full_arr[y:y + 4], arr[:4]) and np.array_equal(
                            full_arr[y + arr.shape[0] - 1], arr[-1]):
                        y0 = y
                        break
            h = arr.shape[0]
            lines = [TextLine(Box(l.box.x, l.box.y - y0, l.box.w, l.box.h), l.text) for l in tr
                     if l.box.y >= y0 and l.box.y2 <= y0 + h]
            return lines, [], {}, 1

        pipe.recognize = fake_recognize
    else:
        cfg["ocr_engine"] = "windows"

    proj = Project.open(chapter)
    report = [f"font: {fonts.name_of(fonts.default_font())} ({fonts.default_font()})"]
    tilt_bad: list[str] = []
    for pg in proj.pages:
        if not REAL:
            current["page"] = (pg.file, pages[pg.file])
        cfg["source_lang"] = "ko" if "_ko" in pg.file else "en"
        try:
            r = engine.process_page(pipe, proj, pg)
        except Exception as e:  # noqa: BLE001
            report.append(f"{pg.file}: FAILED {type(e).__name__}: {e}")
            pg.status, pg.error = "error", str(e)
            continue
        engine.export_page(proj, pg, r)
        Image.fromarray(r.clean).save(out / f"clean_{pg.file}")
        side = Image.new("RGB", (r.result.width * 2 + 20, r.result.height), "white")
        side.paste(Image.fromarray(r.original), (0, 0))
        side.paste(r.result, (r.result.width + 20, 0))
        side.save(out / f"compare_{pg.file}")
        n_over = sum(1 for lay in r.layouts.values() if lay.overflow)
        report.append(f"{pg.file}: blocks {len(pg.blocks)}, translated {sum(1 for b in pg.blocks if b.translation)}, "
                      f"overflow {n_over}, error '{pg.error}'")
        for b in pg.blocks:
            lay = r.layouts.get(b.id)
            tilt = f" tilt {b.angle:+.0f}" if b.angle else ""
            report.append(f"   [{b.kind:6}] {b.source[:30]!r} -> {b.translation[:30]!r} "
                          f"size {lay.size if lay else '-'} lines {len(lay.lines) if lay else 0}{tilt}")
            if "_tilt" in pg.file and b.source.split():
                want = TILT_WANT.get(b.source.split()[0])
                if want is None or abs(b.angle - want) > 2:
                    tilt_bad.append(f"{b.source[:20]!r}: tilt {b.angle:+.0f}, expected {want}")
            elif b.angle:
                tilt_bad.append(f"{pg.file} {b.source[:20]!r}: straight text got tilt {b.angle:+.0f}")
    proj.save()
    again = Project.open(chapter)
    ok = [len(p.blocks) for p in again.pages] == [len(p.blocks) for p in proj.pages]
    report.append(f"project reload ok: {ok}")
    report.append("tilt ok: True" if not tilt_bad else "tilt ok: False — " + "; ".join(tilt_bad))
    if not REAL:
        # с настоящим распознаванием это отчёт, а не проверка: рамки строк там другие
        ok = ok and not tilt_bad
    text = "\n".join(report)
    print(text)
    (out / "report_studio.txt").write_text(text, encoding="utf-8")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
