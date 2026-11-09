package com.manhwatranslator.core

import android.content.Context
import android.graphics.Bitmap
import com.google.android.gms.common.moduleinstall.ModuleInstall
import com.google.android.gms.common.moduleinstall.ModuleInstallRequest
import com.google.android.gms.tasks.Tasks
import com.google.mlkit.common.MlKitException
import com.google.mlkit.vision.common.InputImage
import com.google.mlkit.vision.text.TextRecognition
import com.google.mlkit.vision.text.TextRecognizer
import com.google.mlkit.vision.text.chinese.ChineseTextRecognizerOptions
import com.google.mlkit.vision.text.japanese.JapaneseTextRecognizerOptions
import com.google.mlkit.vision.text.korean.KoreanTextRecognizerOptions
import com.google.mlkit.vision.text.latin.TextRecognizerOptions
import com.google.android.gms.tasks.Task
import com.google.mlkit.vision.text.Text
import java.util.concurrent.ArrayBlockingQueue
import java.util.concurrent.ExecutionException
import java.util.concurrent.ExecutorService
import java.util.concurrent.Executors
import java.util.concurrent.TimeUnit
import kotlin.math.atan2
import kotlin.math.hypot

class OcrException(message: String) : Exception(message)

/** Результат распознавания: строки и подробности для диагностики. */
class OcrResult(
    val lines: List<TextLine>,
    /** Сколько строк нашёл каждый распознаватель */
    val stats: Map<String, Int>,
    val passes: List<PassInfo> = emptyList(),
    val merge: MergeStats = MergeStats(),
    /** Сколько проходов шло одновременно */
    val threads: Int = 1,
)

/** Распознавание текста через ML Kit (модели приходят из сервисов Google Play). */
class Ocr(private val ctx: Context) {
    /** Распознаватели: отдельный на каждый одновременный проход («слот»). */
    private val clients = HashMap<String, TextRecognizer>()
    private var pool: ExecutorService? = null

    companion object {
        private val scriptNames = mapOf("latin" to tr("латиница"), "korean" to tr("корейский"),
            "japanese" to tr("японский"), "chinese" to tr("китайский"))

        fun scriptName(script: String): String = tr(scriptNames[script] ?: script)

        /** Сколько проходов запускать одновременно: по числу ядер, но не больше 3 (в Live — 2). */
        fun parallelism(variants: Int, live: Boolean): Int {
            val cores = Runtime.getRuntime().availableProcessors()
            return minOf(variants, maxOf(1, cores - 1), if (live) 2 else 3).coerceAtLeast(1)
        }

        /**
         * Распознаватели для языка. Английский дополнительно читается корейским распознавателем:
         * он тоже понимает латиницу и выручает, если модуль латиницы в сервисах Google Play
         * не скачался или работает с ошибками.
         */
        fun scriptsFor(lang: SourceLang): List<String> =
            if (lang.script == "latin") listOf("latin", "korean") else listOf(lang.script)

        /** Попросить сервисы Google Play скачать модели распознавания заранее. */
        fun requestModules(ctx: Context, lang: SourceLang) {
            val ocr = Ocr(ctx)
            try {
                for (script in scriptsFor(lang)) ocr.install(script)
            } catch (_: Exception) {
            }
        }
    }

    private fun install(script: String) {
        val request = ModuleInstallRequest.newBuilder().addApi(client(script)).build()
        ModuleInstall.getClient(ctx).installModules(request)
    }

    @Synchronized
    private fun client(script: String, slot: Int = 0): TextRecognizer = clients.getOrPut("$script#$slot") {
        when (script) {
            "korean" -> TextRecognition.getClient(KoreanTextRecognizerOptions.Builder().build())
            "japanese" -> TextRecognition.getClient(JapaneseTextRecognizerOptions.Builder().build())
            "chinese" -> TextRecognition.getClient(ChineseTextRecognizerOptions.Builder().build())
            else -> TextRecognition.getClient(TextRecognizerOptions.DEFAULT_OPTIONS)
        }
    }

    /**
     * Распознавание в несколько проходов (см. [Enhance]). Координаты строк — в пикселях
     * исходной картинки. Вызывать из фонового потока.
     *
     * parallel — проходы идут одновременно. Результат от этого не меняется: проходы
     * не зависят друг от друга, а объединяются всегда в одном и том же порядке.
     */
    fun recognize(bitmap: Bitmap, lang: SourceLang, mode: Int, parallel: Boolean = true, live: Boolean = false): OcrResult {
        val k = Enhance.scaleFor(bitmap.width, bitmap.height)
        val big = Enhance.scaled(bitmap, k)
        val scripts = scriptsFor(lang)
        val keys = Enhance.variantKeys(mode)
        val source = Enhance.Source(big)
        val threads = if (parallel) parallelism(keys.size, live) else 1
        val results = arrayOfNulls<List<PassInfo>>(keys.size)
        var stuck = false
        try {
            if (threads <= 1) {
                keys.forEachIndexed { i, key -> results[i] = runVariant(key, source, scripts, 0, parallel) }
            } else {
                val exec = pool ?: Executors.newFixedThreadPool(3).also { pool = it }
                // каждый одновременный проход берёт свой «слот» — свой набор распознавателей
                val slots = ArrayBlockingQueue<Int>(threads).apply { repeat(threads) { add(it) } }
                val futures = keys.map { key ->
                    exec.submit<List<PassInfo>> {
                        val slot = slots.take()
                        try {
                            runVariant(key, source, scripts, slot, true)
                        } finally {
                            slots.put(slot)
                        }
                    }
                }
                futures.forEachIndexed { i, f ->
                    results[i] = try {
                        f.get(120, TimeUnit.SECONDS)
                    } catch (e: Exception) {
                        // зависший проход ещё может читать картинку — её нельзя освобождать
                        f.cancel(true)
                        stuck = true
                        val msg = (e.cause ?: e).message ?: e.toString()
                        scripts.map { PassInfo(keys[i], it, emptyList(), 0, 0, msg) }
                    }
                }
            }
        } finally {
            if (big !== bitmap && !stuck) big.recycle()
        }
        // порядок проходов всегда одинаковый: вариант за вариантом, распознаватель за распознавателем
        val passes = results.flatMap { it.orEmpty() }.map { p ->
            PassInfo(p.variant, p.script, p.lines.map { l -> TextLine(Box(l.box.x / k, l.box.y / k, l.box.w / k, l.box.h / k), l.text, l.angle, l.len / k, l.thick / k) },
                p.prepMs, p.ocrMs, p.error)
        }
        val stats = LinkedHashMap<String, Int>()
        for (p in passes) stats[p.script] = (stats[p.script] ?: 0) + p.lines.size
        val mergeStats = MergeStats()
        val merged = Enhance.merge(passes.map { it.lines }, mergeStats)
        // если не сработал ни один распознаватель — показываем ошибку, а не «текст не найден»
        if (merged.isEmpty()) passes.firstOrNull { it.error.isNotEmpty() }?.let { throw OcrException(it.error) }
        return OcrResult(merged, stats, passes, mergeStats, threads)
    }

    /** Один вариант картинки всеми распознавателями. */
    private fun runVariant(key: String, source: Enhance.Source, scripts: List<String>, slot: Int,
                           scriptsTogether: Boolean): List<PassInfo> {
        val t0 = System.nanoTime()
        val img = Enhance.make(key, source)
        val prepMs = (System.nanoTime() - t0) / 1_000_000
        try {
            val out = ArrayList<PassInfo>()
            if (scriptsTogether && scripts.size > 1) {
                // распознаватели разных письменностей работают одновременно
                val start = System.nanoTime()
                val tasks = scripts.map { script -> script to submit(img, script, slot) }
                for ((script, task) in tasks) {
                    out.add(collect(key, script, task, prepMs, start))
                }
            } else {
                for (script in scripts) {
                    val start = System.nanoTime()
                    val task = submit(img, script, slot)
                    out.add(collect(key, script, task, prepMs, start))
                }
            }
            return out
        } finally {
            if (img !== source.src) img.recycle()
        }
    }

    private fun submit(bitmap: Bitmap, script: String, slot: Int): Task<Text>? = try {
        client(script, slot).process(InputImage.fromBitmap(bitmap, 0))
    } catch (_: Exception) {
        null
    }

    private fun collect(key: String, script: String, task: Task<Text>?, prepMs: Long, start: Long): PassInfo {
        val lines = try {
            if (task == null) throw OcrException(trf("Ошибка распознавания (%s): %s", scriptName(script), "—"))
            toLines(await(task, script))
        } catch (e: OcrException) {
            // запасной распознаватель не должен ломать основной, и наоборот
            return PassInfo(key, script, emptyList(), prepMs, (System.nanoTime() - start) / 1_000_000, e.message ?: "")
        }
        return PassInfo(key, script, lines, prepMs, (System.nanoTime() - start) / 1_000_000)
    }

    private fun await(task: Task<Text>, script: String): Text = try {
        Tasks.await(task, 60, TimeUnit.SECONDS)
    } catch (e: ExecutionException) {
        val cause = e.cause
        if (cause is MlKitException && cause.errorCode == MlKitException.UNAVAILABLE) {
            try { install(script) } catch (_: Exception) {}
            throw OcrException(trf("Сервисы Google Play ещё скачивают модель распознавания (%s). Подождите минуту (нужен интернет) и попробуйте снова.", scriptName(script)))
        }
        throw OcrException(trf("Ошибка распознавания (%s): %s", scriptName(script), cause?.message ?: e.message))
    } catch (e: java.util.concurrent.TimeoutException) {
        throw OcrException(tr("Распознавание заняло слишком много времени"))
    }

    private fun toLines(result: Text): List<TextLine> {
        val out = ArrayList<TextLine>()
        for (block in result.textBlocks) {
            for (line in block.lines) {
                val r = line.boundingBox ?: continue
                // четыре угла строки идут по часовой стрелке от левого верхнего:
                // верхняя сторона даёт наклон и длину строки, боковая — её высоту
                var angle = 0f
                var len = 0f
                var thick = 0f
                val pts = line.cornerPoints
                if (pts != null && pts.size == 4) {
                    val dx = (pts[1].x - pts[0].x).toFloat()
                    val dy = (pts[1].y - pts[0].y).toFloat()
                    len = hypot(dx, dy)
                    thick = hypot((pts[2].x - pts[1].x).toFloat(), (pts[2].y - pts[1].y).toFloat())
                    if (len > 1f && thick > 1f) angle = -Math.toDegrees(atan2(dy, dx).toDouble()).toFloat()
                    else { len = 0f; thick = 0f }
                }
                out.add(TextLine(Box(r.left.toFloat(), r.top.toFloat(), r.width().toFloat(), r.height().toFloat()),
                    line.text, angle, len, thick))
            }
        }
        return out
    }

    fun close() {
        pool?.shutdownNow()
        pool = null
        synchronized(this) {
            clients.values.forEach { it.close() }
            clients.clear()
        }
    }
}
