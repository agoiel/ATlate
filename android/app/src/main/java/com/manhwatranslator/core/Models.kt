package com.manhwatranslator.core

import android.graphics.Color

/** Прямоугольник в пикселях картинки. */
data class Box(val x: Float, val y: Float, val w: Float, val h: Float) {
    val x2: Float get() = x + w
    val y2: Float get() = y + h
    val cx: Float get() = x + w / 2f
    val cy: Float get() = y + h / 2f

    fun union(o: Box): Box {
        val nx = minOf(x, o.x)
        val ny = minOf(y, o.y)
        return Box(nx, ny, maxOf(x2, o.x2) - nx, maxOf(y2, o.y2) - ny)
    }
}

/**
 * Строка текста, найденная OCR.
 *
 * [box] — прямая рамка. Если строка стоит под углом, рамка заметно больше самой строки, поэтому
 * отдельно хранятся наклон и настоящие размеры: [angle] — в градусах, плюс — правый край выше
 * левого; [len] и [thick] — длина и высота строки (0 — неизвестны, тогда берётся рамка).
 */
data class TextLine(val box: Box, val text: String, val angle: Float = 0f, val len: Float = 0f, val thick: Float = 0f)

/** Облако: несколько строк, склеенных в одну реплику. */
class Bubble(val box: Box, val lines: List<TextLine>, val text: String) {
    /** bubble — светлое облако, box — плашка, free — текст поверх рисунка */
    var kind: String = "bubble"
    var bgColor: Int = Color.WHITE
    var translation: String = ""
    var skipReason: String = ""
    /** Язык облака, определённый по символам (ko, ja, zh, en …) */
    var lang: String = ""

    /**
     * Наклон текста в градусах (плюс — правый край выше; 0 — ровно). У наклонённого облака
     * заданы настоящие размеры текста вдоль и поперёк строк, его центр и высота строки.
     */
    var angle: Float = 0f
    var tiltW: Float = 0f
    var tiltH: Float = 0f
    var tiltCx: Float = 0f
    var tiltCy: Float = 0f
    var tiltLineH: Float = 0f

    val tilted: Boolean get() = angle != 0f && tiltW > 0f && tiltH > 0f
    val translatable: Boolean get() = skipReason.isEmpty() && text.isNotBlank()
    val lineHeight: Float get() = if (tilted && tiltLineH > 0f) tiltLineH else lines.map { it.box.h }.average().toFloat()
}

class PageResult(
    val bubbles: List<Bubble> = emptyList(),
    val error: String = "",
    val cacheHits: Int = 0,
    val ocrMs: Long = 0,
    val translateMs: Long = 0,
    /** Сколько строк нашло распознавание (до сборки в облака) */
    val lineCount: Int = 0,
    /** Сколько строк нашёл каждый распознаватель, например «латиница 0 · корейский 12» */
    val ocrStats: String = "",
    /** Кадр однотонный — скорее всего, приложение запрещает снимки экрана */
    val blankFrame: Boolean = false,
    /** Подробности для режима разработчика */
    val debug: DebugInfo? = null,
    /** Автоопределение языка: код языка, на который оно переключилось в этот раз (пусто — язык тот же) */
    val detectedLang: String = "",
)

/** Пиксели картинки для анализа фона (ARGB, построчно). */
class PixelImage(val width: Int, val height: Int, val pixels: IntArray) {
    fun gray(x: Int, y: Int): Int {
        val c = pixels[y * width + x]
        return (299 * Color.red(c) + 587 * Color.green(c) + 114 * Color.blue(c)) / 1000
    }

    fun at(x: Int, y: Int): Int = pixels[y * width + x]

    companion object {
        fun fromBitmap(bmp: android.graphics.Bitmap): PixelImage {
            val px = IntArray(bmp.width * bmp.height)
            bmp.getPixels(px, 0, bmp.width, 0, 0, bmp.width, bmp.height)
            return PixelImage(bmp.width, bmp.height, px)
        }
    }
}
