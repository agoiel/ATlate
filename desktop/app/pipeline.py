"""Конвейер: картинка → строки (OCR) → облака → перевод.

Здесь же живёт память сессии:
- кэш уже переведённых облаков (при прокрутке те же облака не переводятся повторно
  и не тратят лимиты);
- последние реплики, которые передаются переводчику как контекст.
"""

from __future__ import annotations

import os
import re
import time
from collections import OrderedDict, deque
from concurrent.futures import ThreadPoolExecutor

import numpy as np
from PIL import Image

from . import angle, languages, titles
from .bubbles import group_lines
from .config import GLOSSARY_DIR, Config
from .glossary import Glossary
from .models import DebugInfo, PageResult, PassInfo
from .ocr import OcrBackend, OcrError, OcrLanguageMissing, create_ocr, enhance
from .translators import RateLimit, TranslateError, TranslateRequest, Translator, create_translator


def _norm(text: str) -> str:
    return re.sub(r"\s+", "", text).lower()


def parallelism(variants: int, live: bool = False) -> int:
    """Сколько проходов распознавать одновременно: по числу ядер, не больше 3 (в Live — 2)."""
    cores = os.cpu_count() or 2
    return max(1, min(variants, cores - 1, 2 if live else 3))


class _Stopwatch:
    """Время этапов одного запуска."""

    def __init__(self):
        self.start = self.last = time.perf_counter()
        self.stages: list[tuple[str, float]] = []

    def mark(self, name: str) -> float:
        now = time.perf_counter()
        ms = (now - self.last) * 1000
        self.stages.append((name, ms))
        self.last = now
        return ms

    def total(self) -> float:
        return (time.perf_counter() - self.start) * 1000


class Pipeline:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self._ocr: OcrBackend | None = None
        self._ocr_name = ""
        self._translator: Translator | None = None
        self._translator_key = ""
        self.glossary: Glossary | None = None
        self._glossary_name = None
        self.cache: OrderedDict[str, str] = OrderedDict()
        self.history: deque[tuple[str, str]] = deque(maxlen=titles.MAX_HISTORY)
        self._title_id: str | None = None
        self._pool: ThreadPoolExecutor | None = None

    # ------------------------------------------------------------ сессия и тайтлы
    def reset_session(self) -> None:
        """Забыть контекст в памяти; контекст тайтла перечитается с диска."""
        self.cache.clear()
        self.history.clear()
        self._title_id = None

    def _sync_title(self) -> None:
        """Подгрузить контекст активного тайтла, если тайтл сменился."""
        tid = self.cfg.get("active_title") or ""
        if tid == self._title_id:
            return
        self.cache.clear()
        self.history.clear()
        t = titles.load(tid)
        if t:
            self.cache.update(t.cache)
            self.history.extend(t.history)
        self._title_id = tid

    def _persist_title(self) -> None:
        t = titles.load(self.cfg.get("active_title") or "")
        if not t:
            return
        t.history.clear()
        t.history.extend(self.history)
        t.cache.clear()
        t.cache.update(self.cache)
        titles.save(t)

    def reload(self) -> None:
        """Перечитать настройки (после окна настроек)."""
        self._translator = None
        self._glossary_name = None
        if self.cfg["ocr_engine"] != self._ocr_name:
            self._ocr = None

    # ------------------------------------------------------------ ленивые объекты
    @property
    def ocr(self) -> OcrBackend:
        if self._ocr is None:
            self._ocr = create_ocr(self.cfg["ocr_engine"], self.cfg)
            self._ocr_name = self.cfg["ocr_engine"]
        return self._ocr

    @property
    def translator(self) -> Translator:
        key = self.cfg["translator"]
        if self._translator is None or self._translator_key != key:
            self._translator = create_translator(key, self.cfg)
            self._translator_key = key
        return self._translator

    def _load_glossary(self) -> Glossary | None:
        name = self.cfg.get("glossary") or ""
        if name != self._glossary_name:
            self._glossary_name = name
            self.glossary = None
            path = GLOSSARY_DIR / name
            if name and path.exists():
                try:
                    self.glossary = Glossary.load(path)
                except (OSError, ValueError):
                    self.glossary = None
        return self.glossary

    # ------------------------------------------------------------ распознавание
    def recognize(self, image: Image.Image, lang: str, mode: str, parallel: bool,
                  live: bool = False, max_threads: int = 0,
                  checkpoint=None) -> tuple[list, list[PassInfo], dict, int]:
        """Все проходы распознавания → (строки, проходы, итог объединения, сколько шло одновременно).

        Параллельно или по очереди — результат одинаковый: проходы не зависят друг от друга
        и объединяются всегда в одном и том же порядке.
        max_threads — не больше стольких проходов одновременно (0 — по числу ядер);
        checkpoint — вызывается перед каждым проходом (пауза и остановка Студии).
        """
        ocr = self.ocr
        keys = enhance.variant_keys(mode)
        src = enhance.Source(image)
        threads = parallelism(len(keys), live) if parallel and ocr.supports_parallel else 1
        if max_threads > 0:
            threads = max(1, min(threads, max_threads))

        def one(key: str) -> PassInfo:
            if checkpoint is not None:
                checkpoint()
            t = time.perf_counter()
            img = enhance.make(key, src)
            prep = (time.perf_counter() - t) * 1000
            t = time.perf_counter()
            try:
                lines = ocr.recognize(img, lang)
            except OcrLanguageMissing:
                raise
            except OcrError as e:
                return PassInfo(key, [], prep, (time.perf_counter() - t) * 1000, str(e))
            return PassInfo(key, lines, prep, (time.perf_counter() - t) * 1000)

        if threads > 1:
            if self._pool is None:
                self._pool = ThreadPoolExecutor(max_workers=3, thread_name_prefix="ocr")
            futures = [self._pool.submit(one, k) for k in keys[:threads]]
            # не больше threads проходов одновременно: остальные — по мере освобождения
            passes: list[PassInfo | None] = [None] * len(keys)
            pending = list(range(threads, len(keys)))
            for i in range(len(keys)):
                passes[i] = futures[i].result()
                if pending:
                    futures.append(self._pool.submit(one, keys[pending.pop(0)]))
        else:
            passes = [one(k) for k in keys]
        stats: dict = {}
        lines = enhance.merge([p.lines for p in passes], stats)
        if not lines:
            failed = next((p for p in passes if p.error), None)
            if failed:
                raise OcrError(failed.error)
        return lines, passes, stats, threads

    # ------------------------------------------------------------ основная работа
    def process(self, image: Image.Image, translate: bool = True, live: bool = False) -> PageResult:
        cfg = self.cfg
        lang = cfg["source_lang"]
        info = languages.src(lang)
        result = PageResult(image_size=image.size)
        screen = cfg.get("content_mode") == "screen"
        t0 = time.perf_counter()
        watch = _Stopwatch()
        self._sync_title()

        try:
            mode = cfg.get("ocr_mode", "thorough")
            lines, passes, mstats, threads = self.recognize(
                image, lang, mode, bool(cfg.get("parallel_ocr", True)), live)
        except OcrLanguageMissing as e:
            result.error = str(e)
            result.missing_ocr_lang = e.lang
            return result
        except OcrError as e:
            result.error = str(e)
            watch.mark("распознавание")
            result.debug = DebugInfo(stages=watch.stages, total_ms=watch.total(), error=str(e))
            return result
        t1 = time.perf_counter()
        result.timings["ocr"] = t1 - t0
        watch.mark("распознавание")

        def debug() -> DebugInfo:
            return DebugInfo(passes=passes, stages=list(watch.stages), total_ms=watch.total(), threads=threads,
                             added=mstats.get("added", 0), replaced=mstats.get("replaced", 0), lines=len(lines),
                             bubbles=len(result.bubbles), cache_hits=result.cache_hits, error=result.error)

        arr = np.asarray(image.convert("RGB"))
        if screen:
            # обычный текст: строки собираются в абзацы, ничего не отбрасывается из-за фона или края
            result.bubbles = group_lines(lines, arr, joiner=info["joiner"], direction="ltr", gap=0.9,
                                         row_gap=float(cfg["bubble_row_gap"]), border_check=False,
                                         skip_free_text=False, skip_cut=False)
        else:
            result.bubbles = group_lines(
                lines, arr,
                joiner=info["joiner"], direction=info["direction"],
                gap=float(cfg["bubble_gap"]), row_gap=float(cfg["bubble_row_gap"]),
                border_check=bool(cfg["bubble_border_check"]),
                skip_free_text=bool(cfg["skip_free_text"]),
                skip_cut=bool(cfg["skip_cut_bubbles"]),
            )
        if cfg.get("keep_tilt", True):
            self._find_tilt(result.bubbles, arr, info["joiner"])
        for b in result.bubbles:
            b.lang = languages.detect(b.text, lang)
            if screen:
                b.kind = "screen"
        result.line_count = len(lines)
        t2 = time.perf_counter()
        result.timings["bubbles"] = t2 - t1
        watch.mark("облака")
        if not translate:
            result.debug = debug()
            return result

        # Кэш: то, что уже переводили, не отправляем повторно
        todo = []
        for b in result.bubbles:
            if not b.translatable:
                continue
            hit = self.cache.get(self._cache_key(b.text))
            if hit is not None:
                b.translation = hit
                result.cache_hits += 1
            else:
                todo.append(b)

        if todo:
            try:
                # облака на разных языках переводятся отдельными запросами
                groups: dict[str, list] = {}
                for b in todo:
                    groups.setdefault(b.lang or lang, []).append(b)
                for code, group in groups.items():
                    self._translate(group, code)
            except TranslateError as e:
                result.error = str(e)
            self._persist_title()
        result.timings["translate"] = time.perf_counter() - t2
        watch.mark("перевод")
        result.debug = debug()
        return result

    @staticmethod
    def _find_tilt(bubbles, arr: np.ndarray, joiner: str) -> None:
        """Наклон текста каждого облака: перевод встанет под тем же углом, что и оригинал."""
        for b in bubbles:
            boxes = [ln.box for ln in b.lines]
            t = angle.find(arr, b.box, boxes, b.kind, b.bg_color, [ln.angle for ln in b.lines])
            if not t.angle:
                continue
            b.angle, b.line_h = t.angle, t.line_h
            b.tilt_size, b.tilt_center = (t.length, t.thick), (t.cx, t.cy)
            if t.kind != b.kind:
                # наклонённая реплика оказалась в облаке, а не «поверх рисунка»
                b.kind, b.bg_color = t.kind, t.bg
                if b.skip_reason == "текст поверх рисунка":
                    b.skip_reason = ""
            if len(b.lines) > 1:
                # прямые рамки наклонённых строк налезают друг на друга — порядок строк собираем заново
                b.text = angle.join(b.lines, t.angle, t.line_h, joiner) or b.text

    # ------------------------------------------------------------ сравнение
    def compare(self, image: Image.Image) -> str:
        """Распознать картинку по очереди и параллельно, сравнить время и результат."""
        cfg = self.cfg
        lang = cfg["source_lang"]
        mode = cfg.get("ocr_mode", "thorough")
        try:
            self.recognize(image, lang, "fast", False)  # прогрев: загрузка распознавателя не попадёт в замер
        except OcrError:
            pass

        def run(parallel: bool):
            t = time.perf_counter()
            try:
                lines, _, _, threads = self.recognize(image, lang, mode, parallel)
            except OcrError:
                lines, threads = [], 1
            return (time.perf_counter() - t) * 1000, lines, threads

        seq_ms, seq, _ = run(False)
        par_ms, par, threads = run(True)

        def keys(ls):
            return sorted(f"{l.text}|{int(l.box.x // 4)}|{int(l.box.y // 4)}|{int(l.box.w // 4)}|{int(l.box.h // 4)}"
                          for l in ls)

        a, b = keys(seq), keys(par)
        out = ["Сравнение распознавания", f"По очереди: {seq_ms:.0f} мс"]
        if threads > 1:
            out.append(f"Параллельно (×{threads}): {par_ms:.0f} мс")
            k = seq_ms / par_ms if par_ms else 0
            out.append(f"Быстрее в {k:.1f} раза" if k >= 1 else f"Медленнее в {1 / k:.1f} раза" if k else "")
        else:
            out.append("Параллельно: недоступно (один проход, одно ядро или EasyOCR)")
        if a == b:
            out.append(f"Результат совпал: {len(a)} строк")
        else:
            diff = len(set(a) ^ set(b))
            out.append(f"Результат различается: {len(a)} и {len(b)} строк, расхождений {diff}")
        return "\n".join(x for x in out if x)

    def _cache_key(self, text: str) -> str:
        c = self.cfg
        src = languages.detect(text, c['source_lang'])
        return f"{src}>{c['target_lang']}|{c['translator']}|{_norm(text)}"

    def _translate(self, bubbles, src_code: str) -> None:
        cfg = self.cfg
        texts = [b.text for b in bubbles]
        glossary = self._load_glossary()
        RateLimit.interval = max(0.0, float(cfg.get("request_interval", 1.0)))
        tr = self.translator

        n_ctx = int(cfg["context_size"])
        req = TranslateRequest(
            texts=texts, src=src_code, tgt=cfg["target_lang"],
            history=list(self.history)[-n_ctx:] if n_ctx > 0 else [],
            screen=cfg.get("content_mode") == "screen",
        )
        if glossary:
            req.glossary = glossary.find(texts)
            if not tr.uses_llm and req.glossary:
                req.anchored = [glossary.apply_anchors(t) for t in texts]

        out = tr.translate(req)
        for b, t in zip(bubbles, out):
            b.translation = t
            if t:
                self.cache[self._cache_key(b.text)] = t
                self.history.append((b.text, t))
        while len(self.cache) > 3000:
            self.cache.popitem(last=False)
