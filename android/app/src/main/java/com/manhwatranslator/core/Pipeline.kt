package com.manhwatranslator.core

import android.content.Context
import android.graphics.Bitmap

/**
 * Конвейер: картинка → строки (OCR) → облака → перевод.
 * Хранит кэш переводов и последние реплики для контекста. Если выбран тайтл,
 * контекст берётся из памяти телефона и сохраняется туда после каждого перевода.
 */
class Pipeline(private val ctx: Context) {
    private val ocr = Ocr(ctx)
    private val cache = object : LinkedHashMap<String, String>(256, 0.75f, true) {
        override fun removeEldestEntry(eldest: MutableMap.MutableEntry<String, String>?) = size > 3000
    }
    private val history = ArrayDeque<Pair<String, String>>()
    private var loadedTitle: String? = null
    private var loadedStamp = 0L

    @Synchronized
    fun reset() {
        cache.clear()
        history.clear()
        loadedTitle = null
    }

    /** Подгрузить контекст активного тайтла, если он сменился или изменился на диске. */
    private fun syncTitle(s: Settings) {
        val id = s.activeTitle
        val stamp = if (id.isBlank()) 0L else TitleStore.file(ctx, id).lastModified()
        if (id == loadedTitle && stamp == loadedStamp) return
        cache.clear()
        history.clear()
        TitleStore.load(ctx, id)?.let { t ->
            cache.putAll(t.cache)
            history.addAll(t.history)
        }
        loadedTitle = id
        loadedStamp = stamp
    }

    private fun persistTitle(s: Settings) {
        val id = s.activeTitle
        if (id.isBlank()) return
        val t = TitleStore.load(ctx, id) ?: return
        t.history.clear()
        t.history.addAll(history)
        t.cache.clear()
        t.cache.putAll(cache)
        TitleStore.save(ctx, t)
        loadedStamp = TitleStore.file(ctx, id).lastModified()
    }

    /**
     * Вызывать из фонового потока. timings — отметки, начатые раньше (например, снимок экрана);
     * live — запуск из режима Live (меньше одновременных проходов, чтобы не грелся телефон).
     */
    @Synchronized
    fun process(bitmap: Bitmap, s: Settings, translate: Boolean = true, timings: Timings = Timings(),
                live: Boolean = false): PageResult {
        val src = Languages.src(s.sourceLang)
        val tgt = Languages.tgt(s.targetLang)
        val screen = s.contentMode == "screen"
        val t0 = System.currentTimeMillis()
        syncTitle(s)

        if (isBlank(bitmap)) {
            return PageResult(blankFrame = true, error = tr("Снимок области получился однотонным (обычно чёрным). Похоже, приложение-читалка запрещает снимки экрана — так делают некоторые официальные приложения. Откройте тайтл в браузере или другой читалке."))
        }
        val ocrRes = try {
            ocr.recognize(bitmap, src, s.ocrMode, s.parallelOcr, live)
        } catch (e: OcrException) {
            timings.mark(tr("распознавание"))
            val msg = e.message ?: tr("Ошибка распознавания")
            return PageResult(error = msg, debug = DebugInfo(stages = timings.stages(), totalMs = timings.totalMs(), error = msg))
        }
        timings.mark(tr("распознавание"))
        val lines = ocrRes.lines
        val stats = ocrRes.stats.entries.joinToString(" · ") { (k, v) -> "${Ocr.scriptName(k)} $v" }
        val img = PixelImage.fromBitmap(bitmap)
        val bubbles = if (screen) {
            // обычный текст: строки собираются в абзацы, ничего не отбрасывается из-за фона или края
            Bubbles.group(lines, img, Bubbles.Params(joiner = src.joiner, rtl = false, gap = 0.9f,
                borderCheck = false, skipFreeText = false, skipCut = false, keepTilt = s.keepTilt))
        } else {
            Bubbles.group(lines, img, Bubbles.Params(
                joiner = src.joiner, rtl = src.rtl, gap = s.bubbleGap,
                skipFreeText = s.skipFreeText, skipCut = s.skipCut, keepTilt = s.keepTilt,
            ))
        }
        for (b in bubbles) {
            b.lang = Languages.detect(b.text, src).code
            if (screen) b.kind = "screen"
        }
        timings.mark(tr("облака"))
        val t1 = System.currentTimeMillis()
        fun debug(hits: Int = 0, error: String = "") = DebugInfo(ocrRes.passes, timings.stages(), timings.totalMs(),
            ocrRes.threads, ocrRes.merge, lines.size, bubbles.size, hits, error)
        if (!translate) return PageResult(bubbles, ocrMs = t1 - t0, lineCount = lines.size, ocrStats = stats, debug = debug())

        var hits = 0
        val todo = ArrayList<Bubble>()
        for (b in bubbles) {
            if (!b.translatable) continue
            val hit = cache[key(s, b.text)]
            if (hit != null) { b.translation = hit; hits++ } else todo.add(b)
        }

        var error = ""
        if (todo.isNotEmpty()) {
            try {
                // облака на разных языках переводятся отдельными запросами
                for ((code, group) in todo.groupBy { it.lang.ifEmpty { src.code } }) {
                    translateBubbles(group, s, Languages.src(code), tgt)
                }
            } catch (e: TranslateException) {
                error = e.message ?: tr("Ошибка перевода")
            } catch (e: Exception) {
                error = trf("Ошибка перевода: %s", e.message)
            }
            persistTitle(s)
        }
        timings.mark(tr("перевод"))
        return PageResult(bubbles, error, hits, t1 - t0, System.currentTimeMillis() - t1, lines.size, stats,
            debug = debug(hits, error))
    }

    /** Итог сравнения «по очереди» и «параллельно» на одной и той же картинке. */
    class Comparison(val sequentialMs: Long, val parallelMs: Long, val threads: Int, val same: Boolean,
                     val sequentialLines: Int, val parallelLines: Int, val differences: Int) {
        fun report(): String = buildString {
            append(trf("По очереди: %s мс", sequentialMs)).append('\n')
            append(trf("Параллельно (×%s): %s мс", threads, parallelMs)).append('\n')
            if (parallelMs > 0 && sequentialMs > 0) {
                val k = sequentialMs.toFloat() / parallelMs
                append(if (k >= 1f) trf("Быстрее в %s раза", String.format("%.1f", k))
                       else trf("Медленнее в %s раза", String.format("%.1f", 1f / k))).append('\n')
            }
            append(if (same) trf("Результат совпал: %s строк", parallelLines)
                   else trf("Результат различается: %s и %s строк, расхождений %s", sequentialLines, parallelLines, differences))
        }
    }

    /**
     * Распознать одну картинку по очереди и параллельно и сравнить время и результат.
     * Сначала один короткий прогон «на прогрев», чтобы загрузка моделей не попала в замер.
     */
    @Synchronized
    fun compare(bitmap: Bitmap, s: Settings): Comparison {
        val src = Languages.src(s.sourceLang)
        try { ocr.recognize(bitmap, src, Enhance.FAST, parallel = true) } catch (_: OcrException) {}
        fun run(parallel: Boolean): Pair<Long, OcrResult?> {
            val t = System.nanoTime()
            val r = try { ocr.recognize(bitmap, src, s.ocrMode, parallel) } catch (_: OcrException) { null }
            return (System.nanoTime() - t) / 1_000_000 to r
        }
        val (seqMs, seq) = run(false)
        val (parMs, par) = run(true)
        fun keys(r: OcrResult?) = r?.lines.orEmpty().map {
            "${it.text}|${(it.box.x / 4).toInt()}|${(it.box.y / 4).toInt()}|${(it.box.w / 4).toInt()}|${(it.box.h / 4).toInt()}"
        }.sorted()
        val a = keys(seq)
        val b = keys(par)
        val diff = (a - b.toSet()).size + (b - a.toSet()).size
        return Comparison(seqMs, parMs, par?.threads ?: 1, a == b, a.size, b.size, diff)
    }

    /** Однотонный кадр: разброс яркости почти нулевой. */
    private fun isBlank(bmp: Bitmap): Boolean {
        val small = Bitmap.createScaledBitmap(bmp, 32, maxOf(1, 32 * bmp.height / maxOf(1, bmp.width)), true)
        val px = IntArray(small.width * small.height)
        small.getPixels(px, 0, small.width, 0, 0, small.width, small.height)
        if (small !== bmp) small.recycle()
        var lo = 255
        var hi = 0
        for (c in px) {
            val v = (android.graphics.Color.red(c) + android.graphics.Color.green(c) + android.graphics.Color.blue(c)) / 3
            if (v < lo) lo = v
            if (v > hi) hi = v
        }
        return hi - lo < 10
    }

    private fun key(s: Settings, text: String) =
        "${Languages.detect(text, Languages.src(s.sourceLang)).code}>${s.targetLang}|${s.translator}|${text.replace(Regex("\\s+"), "").lowercase()}"

    private fun translateBubbles(bubbles: List<Bubble>, s: Settings, src: SourceLang, tgt: TargetLang) {
        val texts = bubbles.map { it.text }
        RateLimit.intervalMs = (s.requestIntervalSec.coerceIn(0f, 30f) * 1000).toLong()
        val tr = Translators.create(s)
        val glossary = s.glossary.takeIf { it.isNotBlank() }?.let { GlossaryStore.load(ctx, it) }
        val found = glossary?.find(texts) ?: emptyList()
        val anchored = if (glossary != null && !tr.usesLlm && found.isNotEmpty()) texts.map { glossary.applyAnchors(it) } else null
        val n = s.contextSize
        val req = TranslateRequest(
            texts = texts, src = src, tgt = tgt,
            history = if (n > 0) history.toList().takeLast(n) else emptyList(),
            glossary = found, anchored = anchored,
            screen = s.contentMode == "screen",
        )
        val out = tr.translate(req)
        bubbles.forEachIndexed { i, b ->
            val t = out.getOrNull(i).orEmpty()
            b.translation = t
            if (t.isNotBlank()) {
                cache[key(s, b.text)] = t
                history.addLast(b.text to t)
                while (history.size > Title.MAX_HISTORY) history.removeFirst()
            }
        }
    }

    fun close() = ocr.close()
}
