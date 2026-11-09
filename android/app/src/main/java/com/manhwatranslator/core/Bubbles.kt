package com.manhwatranslator.core

import android.graphics.Color
import kotlin.math.abs
import kotlin.math.cos
import kotlin.math.hypot
import kotlin.math.max
import kotlin.math.min
import kotlin.math.roundToInt
import kotlin.math.sin

/**
 * Сборка строк OCR в облака без нейросети — тот же алгоритм, что в ПК-версии (bubbles.py).
 *
 * 1. Соседние строки похожей высоты, стоящие друг под другом или рядом, склеиваются.
 * 2. Если между строками проходит контур облака — не склеиваем (два облака вплотную).
 * 3. По фону вокруг блока определяем тип: облако, плашка или текст поверх рисунка.
 * 4. Сортируем в порядке чтения и склеиваем строки в одну фразу.
 */
object Bubbles {

    data class Params(
        val joiner: String = " ",
        val rtl: Boolean = false,
        val gap: Float = 1.1f,
        val rowGap: Float = 1.5f,
        val borderCheck: Boolean = true,
        val skipFreeText: Boolean = false,
        val skipCut: Boolean = true,
        /** Замечать наклон текста: перевод встанет под тем же углом, что и оригинал. */
        val keepTilt: Boolean = true,
    )

    /** Наклон меньше этого считается ровным текстом — чтобы обычные реплики не «плясали». */
    private const val MIN_TILT = 4f
    /** Круче — это уже вертикальный текст, его не трогаем. */
    private const val MAX_TILT = 45f
    /** Строки, наклон которых различается сильнее, — разные надписи, даже если стоят рядом. */
    private const val TILT_DIFF = 12f

    private val letterRe = Regex("\\p{L}")
    private const val CJK = "\\u3040-\\u30ff\\u3400-\\u4dbf\\u4e00-\\u9fff\\uf900-\\ufaff\\uff66-\\uff9f"
    private val cjkSpaceRe = Regex("(?<=[$CJK])\\s+(?=[$CJK])")

    private fun xOverlap(a: Box, b: Box) = min(a.x2, b.x2) - max(a.x, b.x)
    private fun yOverlap(a: Box, b: Box) = min(a.y2, b.y2) - max(a.y, b.y)
    private fun heightOk(a: Box, b: Box): Boolean = max(a.h, b.h) / max(1f, min(a.h, b.h)) <= 1.9f

    // ---------------------------------------------------------------- наклонённый текст

    /** Точка (от центра) → в систему, где строка с наклоном deg горизонтальна. */
    private fun toFrame(x: Float, y: Float, deg: Float): Pair<Float, Float> {
        val a = Math.toRadians(deg.toDouble())
        val c = cos(a).toFloat()
        val s = sin(a).toFloat()
        return (x * c - y * s) to (x * s + y * c)
    }

    private fun sameTilt(a: TextLine, b: TextLine): Boolean {
        // размеры строки неизвестны — значит, и наклон неизвестен: склейке не мешаем
        if (a.len <= 0f || b.len <= 0f) return true
        return abs(a.angle - b.angle) <= TILT_DIFF
    }

    /** Наклон блока: медиана наклонов его строк; 0 — текст ровный (или вертикальный). */
    private fun blockTilt(members: List<TextLine>): Float {
        val known = members.filter { it.len > 0f && it.thick > 0f }
        if (known.isEmpty() || known.size < members.size) return 0f
        val angle = known.map { it.angle }.sorted()[known.size / 2]
        return if (abs(angle) < MIN_TILT || abs(angle) > MAX_TILT) 0f else angle
    }

    /**
     * Строки наклонённого блока «выпрямленными»: прямые рамки наклонённых строк налезают
     * друг на друга, и по ним не понять, какая строка выше.
     */
    private fun flatten(members: List<TextLine>, deg: Float): List<TextLine> = members.map { ln ->
        val (u, t) = toFrame(ln.box.cx, ln.box.cy, deg)
        TextLine(Box(u - ln.len / 2f, t - ln.thick / 2f, ln.len, ln.thick), ln.text)
    }

    /** Однородность и цвет фона вокруг наклонённого текста — по наклонённой рамке, а не по прямой. */
    private fun ringStatsTilted(img: PixelImage, cx: Float, cy: Float, w: Float, h: Float, deg: Float,
                                pad: Float): Pair<Float, Int> {
        val reach = (hypot(w, h) / 2f + pad).toInt() + 1
        val x1 = max(0, cx.toInt() - reach)
        val y1 = max(0, cy.toInt() - reach)
        val x2 = min(img.width, cx.toInt() + reach)
        val y2 = min(img.height, cy.toInt() + reach)
        val a = Math.toRadians(deg.toDouble())
        val c = cos(a).toFloat()
        val s = sin(a).toFloat()
        val hr = IntArray(256); val hg = IntArray(256); val hb = IntArray(256)
        val ring = ArrayList<Int>()
        for (y in y1 until y2) for (x in x1 until x2) {
            val dx = x - cx
            val dy = y - cy
            val u = abs(dx * c - dy * s)
            val t = abs(dx * s + dy * c)
            if (u > w / 2f + pad || t > h / 2f + pad) continue      // снаружи кольца
            if (u <= w / 2f && t <= h / 2f) continue                // сам текст
            val px = img.at(x, y)
            ring.add(px)
            hr[Color.red(px)]++; hg[Color.green(px)]++; hb[Color.blue(px)]++
        }
        if (ring.size < 10) return 0f to Color.WHITE
        val mr = median(hr, ring.size); val mg = median(hg, ring.size); val mb = median(hb, ring.size)
        var close = 0
        for (px in ring) {
            if (abs(Color.red(px) - mr) <= 30 && abs(Color.green(px) - mg) <= 30 && abs(Color.blue(px) - mb) <= 30) close++
        }
        return close.toFloat() / ring.size to Color.rgb(mr, mg, mb)
    }

    // ---------------------------------------------------------------- проверки по картинке

    /** Медиана по гистограмме значений 0..255. */
    private fun median(hist: IntArray, total: Int): Int {
        var acc = 0
        val half = total / 2
        for (v in 0..255) {
            acc += hist[v]
            if (acc > half) return v
        }
        return 255
    }

    /** Есть ли в полосе между строками сплошная контрастная линия (контур облака). */
    private fun hasSeparator(img: PixelImage?, x1: Float, x2: Float, y1: Float, y2: Float, rows: Boolean): Boolean {
        if (img == null) return false
        val xa = max(0, x1.toInt())
        val xb = min(img.width, x2.roundToInt())
        val ya = max(0, y1.toInt())
        val yb = min(img.height, y2.roundToInt())
        if (xb - xa < 3 || yb - ya < 2) return false
        val hist = IntArray(256)
        for (y in ya until yb) for (x in xa until xb) hist[img.gray(x, y)]++
        val bg = median(hist, (xb - xa) * (yb - ya))
        if (rows) {
            for (y in ya until yb) {
                var n = 0
                for (x in xa until xb) if (abs(img.gray(x, y) - bg) > 60) n++
                if (n > 0.6f * (xb - xa)) return true
            }
        } else {
            for (x in xa until xb) {
                var n = 0
                for (y in ya until yb) if (abs(img.gray(x, y) - bg) > 60) n++
                if (n > 0.6f * (yb - ya)) return true
            }
        }
        return false
    }

    /** Однородность фона вокруг блока и его цвет. */
    private fun ringStats(img: PixelImage, box: Box, pad: Float): Pair<Float, Int> {
        val x1 = max(0, (box.x - pad).toInt())
        val y1 = max(0, (box.y - pad).toInt())
        val x2 = min(img.width, (box.x2 + pad).toInt())
        val y2 = min(img.height, (box.y2 + pad).toInt())
        val ix1 = box.x.toInt(); val iy1 = box.y.toInt()
        val ix2 = box.x2.toInt(); val iy2 = box.y2.toInt()
        val hr = IntArray(256); val hg = IntArray(256); val hb = IntArray(256)
        val ring = ArrayList<Int>()
        for (y in y1 until y2) for (x in x1 until x2) {
            if (x in ix1 until ix2 && y in iy1 until iy2) continue
            val c = img.at(x, y)
            ring.add(c)
            hr[Color.red(c)]++; hg[Color.green(c)]++; hb[Color.blue(c)]++
        }
        if (ring.size < 10) return 0f to Color.WHITE
        val mr = median(hr, ring.size); val mg = median(hg, ring.size); val mb = median(hb, ring.size)
        var close = 0
        for (c in ring) {
            if (abs(Color.red(c) - mr) <= 30 && abs(Color.green(c) - mg) <= 30 && abs(Color.blue(c) - mb) <= 30) close++
        }
        return close.toFloat() / ring.size to Color.rgb(mr, mg, mb)
    }

    // ---------------------------------------------------------------- текст

    private fun joinLines(lines: List<TextLine>, joiner: String): String {
        val sorted = lines.sortedBy { it.box.cy }
        val rows = ArrayList<MutableList<TextLine>>()
        for (ln in sorted) {
            val last = rows.lastOrNull()?.last()
            if (last != null && yOverlap(last.box, ln.box) > 0.5f * min(last.box.h, ln.box.h)) rows.last().add(ln)
            else rows.add(mutableListOf(ln))
        }
        val parts = ArrayList<String>()
        for (row in rows) {
            val text = row.sortedBy { it.box.x }.map { it.text.trim() }.filter { it.isNotEmpty() }.joinToString(joiner)
            if (text.isEmpty()) continue
            // перенос слова через дефис: "some-" + "thing"
            if (parts.isNotEmpty() && parts.last().endsWith("-") && text.first().isLowerCase()) {
                parts[parts.size - 1] = parts.last().dropLast(1) + text
            } else parts.add(text)
        }
        var out = parts.joinToString(joiner).replace(Regex("\\s+"), " ").trim()
        if (joiner.isEmpty()) out = cjkSpaceRe.replace(out, "")
        return out
    }

    // ---------------------------------------------------------------- основной алгоритм

    fun group(input: List<TextLine>, img: PixelImage?, p: Params): List<Bubble> {
        val lines = input.filter { it.text.isNotBlank() && it.box.w > 1 && it.box.h > 1 }
        if (lines.isEmpty()) return emptyList()
        val sepImg = if (p.borderCheck) img else null
        val n = lines.size
        val parent = IntArray(n) { it }
        fun find(i: Int): Int {
            var x = i
            while (parent[x] != x) { parent[x] = parent[parent[x]]; x = parent[x] }
            return x
        }
        fun unite(i: Int, j: Int) { val a = find(i); val b = find(j); if (a != b) parent[b] = a }

        for (i in 0 until n) {
            val a = lines[i].box
            for (j in i + 1 until n) {
                val b = lines[j].box
                if (!heightOk(a, b)) continue
                if (p.keepTilt && !sameTilt(lines[i], lines[j])) continue
                val hmin = min(a.h, b.h)

                // 1) куски одной строки
                if (yOverlap(a, b) > 0.5f * hmin) {
                    val (left, right) = if (a.x <= b.x) a to b else b to a
                    val hgap = right.x - left.x2
                    if (hgap <= p.rowGap * hmin) {
                        if (hgap > 2 && hasSeparator(sepImg, left.x2, right.x, max(a.y, b.y), min(a.y2, b.y2), rows = false)) continue
                        unite(i, j)
                    }
                    continue
                }

                // 2) строки друг под другом
                val (top, bot) = if (a.y <= b.y) a to b else b to a
                val vgap = bot.y - top.y2
                if (vgap > p.gap * hmin) continue
                val xo = xOverlap(a, b)
                val centered = abs(a.cx - b.cx) <= 0.5f * max(a.w, b.w)
                if (xo <= 0 && !centered) continue
                if (vgap > 2) {
                    val sx1 = if (xo > 0) max(a.x, b.x) else min(a.cx, b.cx)
                    val sx2 = if (xo > 0) min(a.x2, b.x2) else max(a.cx, b.cx)
                    if (hasSeparator(sepImg, sx1, sx2, top.y2, bot.y, rows = true)) continue
                }
                unite(i, j)
            }
        }

        val groups = LinkedHashMap<Int, MutableList<TextLine>>()
        for (i in 0 until n) groups.getOrPut(find(i)) { mutableListOf() }.add(lines[i])

        val bubbles = groups.values.map { members ->
            var box = members[0].box
            for (m in members.drop(1)) box = box.union(m.box)
            val tilt = if (p.keepTilt) blockTilt(members) else 0f
            val bubble = if (tilt == 0f) Bubble(box, members, joinLines(members, p.joiner)) else {
                // наклонённый текст: порядок строк, размеры и центр считаются в «выпрямленной» системе
                val flat = flatten(members, tilt)
                Bubble(box, members, joinLines(flat, p.joiner)).also { b ->
                    val u1 = flat.minOf { it.box.x }
                    val u2 = flat.maxOf { it.box.x2 }
                    val t1 = flat.minOf { it.box.y }
                    val t2 = flat.maxOf { it.box.y2 }
                    val (cx, cy) = toFrame((u1 + u2) / 2f, (t1 + t2) / 2f, -tilt)
                    b.angle = tilt
                    b.tiltW = u2 - u1
                    b.tiltH = t2 - t1
                    b.tiltCx = cx
                    b.tiltCy = cy
                    b.tiltLineH = members.map { it.thick }.sorted()[members.size / 2]
                }
            }
            if (img != null) {
                val lineH = members.map { it.box.h }.sorted()[members.size / 2]
                // прямая рамка наклонённой реплики вылезает углами за облако — фон смотрим по наклонённой
                val (uniform, color) = if (bubble.tilted)
                    ringStatsTilted(img, bubble.tiltCx, bubble.tiltCy, bubble.tiltW, bubble.tiltH, tilt,
                        max(3f, 0.4f * bubble.tiltLineH))
                else ringStats(img, box, max(3f, 0.4f * lineH))
                bubble.bgColor = color
                val lum = (Color.red(color) + Color.green(color) + Color.blue(color)) / 3
                bubble.kind = when {
                    uniform >= 0.8f && lum >= 170 -> "bubble"
                    uniform >= 0.8f -> "box"
                    else -> "free"
                }
            }
            when {
                !letterRe.containsMatchIn(bubble.text) -> bubble.skipReason = tr("нет букв")
                p.skipFreeText && bubble.kind == "free" -> bubble.skipReason = tr("текст поверх рисунка")
                p.skipCut && img != null && (box.y <= 2 || box.y2 >= img.height - 2 ||
                    box.x <= 1 || box.x2 >= img.width - 1) -> bubble.skipReason = tr("обрезано краем области")
            }
            bubble
        }
        return readingOrder(bubbles, p.rtl)
    }

    /** Сверху вниз; на одной высоте — слева направо (или справа налево для манги). */
    fun readingOrder(bubbles: List<Bubble>, rtl: Boolean): List<Bubble> {
        val rows = ArrayList<MutableList<Bubble>>()
        for (b in bubbles.sortedBy { it.box.y }) {
            val row = rows.lastOrNull()
            val ref = row?.minByOrNull { it.box.h }
            if (row != null && ref != null && yOverlap(ref.box, b.box) > 0.3f * min(ref.box.h, b.box.h)) row.add(b)
            else rows.add(mutableListOf(b))
        }
        return rows.flatMap { r -> if (rtl) r.sortedByDescending { it.box.x } else r.sortedBy { it.box.x } }
    }
}
