package com.manhwatranslator

import android.annotation.SuppressLint
import android.content.Context
import android.graphics.Canvas
import android.graphics.Color
import android.graphics.Paint
import android.graphics.Rect
import android.graphics.RectF
import android.graphics.Typeface
import android.os.Handler
import android.os.Looper
import android.text.Layout
import android.text.StaticLayout
import android.text.TextPaint
import android.util.TypedValue
import android.view.MotionEvent
import android.view.View
import android.view.ViewConfiguration
import com.manhwatranslator.core.tr
import com.manhwatranslator.core.trf
import com.manhwatranslator.core.Bubble
import com.manhwatranslator.core.DebugInfo
import com.manhwatranslator.core.PassInfo
import kotlin.math.abs
import kotlin.math.max
import kotlin.math.min

// ==================================================================== плавающая кнопка

/**
 * Круглая кнопка поверх всех приложений.
 * Нажатие — перевести / скрыть перевод. Долгое нажатие — выделить новую область.
 * Перетаскивание — подвинуть кнопку.
 */
@SuppressLint("ViewConstructor")
class FloatingButton(
    ctx: Context,
    private val onTap: () -> Unit,
    private val onLongPress: () -> Unit,
    private val onMove: (dx: Int, dy: Int) -> Unit,
    private val onMoveEnd: () -> Unit,
) : View(ctx) {
    val sizePx = ctx.dpF(58f).toInt()
    var busy = false
        set(v) { field = v; invalidate() }
    var showingTranslation = false
        set(v) { field = v; invalidate() }
    /** null — режим «Скриншот»; true/false — режим Live включён/на паузе */
    var live: Boolean? = null
        set(v) { field = v; invalidate() }

    private val fill = Paint(Paint.ANTI_ALIAS_FLAG)
    private val ring = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        style = Paint.Style.STROKE
        strokeWidth = ctx.dpF(2f)
        color = Brand.CREAM
    }
    private val shadow = Paint(Paint.ANTI_ALIAS_FLAG).apply { color = Color.argb(70, 40, 25, 20) }
    private val text = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        color = Color.WHITE
        textAlign = Paint.Align.CENTER
        typeface = Fonts.medium
        textSize = ctx.dpF(17f)
    }
    private val slop = ViewConfiguration.get(ctx).scaledTouchSlop
    private val longPressMs = ViewConfiguration.getLongPressTimeout().toLong()
    private val uiHandler = Handler(Looper.getMainLooper())
    private var downX = 0f
    private var downY = 0f
    private var lastX = 0f
    private var lastY = 0f
    private var dragging = false
    private var longFired = false
    private val longPress = Runnable { longFired = true; performHapticFeedback(0); onLongPress() }

    override fun onMeasure(widthMeasureSpec: Int, heightMeasureSpec: Int) = setMeasuredDimension(sizePx, sizePx)

    override fun onDraw(c: Canvas) {
        val r = sizePx / 2f
        val body = r - context.dpF(4f)
        // мягкая тень
        c.drawCircle(r, r + context.dpF(1.5f), body + context.dpF(1f), shadow)
        fill.color = when {
            busy -> Brand.SAND
            live == false -> Brand.INK
            live == true -> Brand.COCOA
            showingTranslation -> Brand.BOW
            else -> Brand.COCOA
        }
        ring.color = if (live == true) Brand.SAND else Brand.CREAM
        ring.strokeWidth = context.dpF(if (live == true) 3f else 2f)
        c.drawCircle(r, r, body, fill)
        c.drawCircle(r, r, body, ring)
        val label = when {
            busy -> "…"
            live == true -> "LIVE"
            live == false -> "II"
            showingTranslation -> "✕"
            else -> tr("Аа")
        }
        text.textSize = context.dpF(if (label.length > 2) 12f else 17f)
        text.color = if (busy) Brand.INK else Color.WHITE
        c.drawText(label, r, r - (text.descent() + text.ascent()) / 2, text)
    }

    @SuppressLint("ClickableViewAccessibility")
    override fun onTouchEvent(e: MotionEvent): Boolean {
        when (e.actionMasked) {
            MotionEvent.ACTION_DOWN -> {
                downX = e.rawX; downY = e.rawY; lastX = e.rawX; lastY = e.rawY
                dragging = false; longFired = false
                uiHandler.postDelayed(longPress, longPressMs)
            }
            MotionEvent.ACTION_MOVE -> {
                if (!dragging && (abs(e.rawX - downX) > slop || abs(e.rawY - downY) > slop)) {
                    dragging = true
                    uiHandler.removeCallbacks(longPress)
                }
                if (dragging) {
                    onMove((e.rawX - lastX).toInt(), (e.rawY - lastY).toInt())
                    lastX = e.rawX; lastY = e.rawY
                }
            }
            MotionEvent.ACTION_UP -> {
                uiHandler.removeCallbacks(longPress)
                if (dragging) onMoveEnd() else if (!longFired) onTap()
            }
            MotionEvent.ACTION_CANCEL -> uiHandler.removeCallbacks(longPress)
        }
        return true
    }
}

// ==================================================================== выделение области

/** Полноэкранный слой: тянете пальцем прямоугольник области чтения. */
@SuppressLint("ViewConstructor")
class RegionSelectView(
    ctx: Context,
    /** выделять по всей ширине экрана: пользователь задаёт только верх и низ */
    private val fullWidth: Boolean,
    private val onSelected: (Rect) -> Unit,
    private val onCancel: () -> Unit,
) : View(ctx) {
    private val dim = Paint().apply { color = Color.argb(150, 33, 24, 22) }
    private val border = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        style = Paint.Style.STROKE
        strokeWidth = ctx.dpF(2f)
        color = Brand.CREAM
    }
    private val corner = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        style = Paint.Style.STROKE
        strokeWidth = ctx.dpF(4f)
        strokeCap = Paint.Cap.ROUND
        color = Brand.SAND
    }
    private val pill = Paint(Paint.ANTI_ALIAS_FLAG).apply { color = Brand.INK }
    private val hint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        color = Brand.CREAM
        textAlign = Paint.Align.CENTER
        textSize = ctx.dpF(15f)
        typeface = Fonts.medium
    }
    private var sx = 0f
    private var sy = 0f
    private var ex = 0f
    private var ey = 0f
    private var active = false
    private val loc = IntArray(2)

    private fun rect() = if (fullWidth) RectF(0f, min(sy, ey), width.toFloat(), max(sy, ey))
    else RectF(min(sx, ex), min(sy, ey), max(sx, ex), max(sy, ey))

    override fun onDraw(c: Canvas) {
        val w = width.toFloat()
        val h = height.toFloat()
        if (!active) {
            c.drawRect(0f, 0f, w, h, dim)
            drawHint(c, w / 2, h * 0.38f,
                if (fullWidth) tr("Проведите сверху вниз по высоте области") else tr("Выделите пальцем область чтения"))
            drawHint(c, w / 2, h * 0.38f + context.dpF(48f), tr("Короткое касание — отмена"))
            return
        }
        val r = rect()
        c.drawRect(0f, 0f, w, r.top, dim)
        c.drawRect(0f, r.bottom, w, h, dim)
        c.drawRect(0f, r.top, r.left, r.bottom, dim)
        c.drawRect(r.right, r.top, w, r.bottom, dim)
        c.drawRect(r, border)
        // уголки-«скобки»
        val k = min(context.dpF(22f), min(r.width(), r.height()) / 3)
        c.drawLine(r.left, r.top, r.left + k, r.top, corner); c.drawLine(r.left, r.top, r.left, r.top + k, corner)
        c.drawLine(r.right, r.top, r.right - k, r.top, corner); c.drawLine(r.right, r.top, r.right, r.top + k, corner)
        c.drawLine(r.left, r.bottom, r.left + k, r.bottom, corner); c.drawLine(r.left, r.bottom, r.left, r.bottom - k, corner)
        c.drawLine(r.right, r.bottom, r.right - k, r.bottom, corner); c.drawLine(r.right, r.bottom, r.right, r.bottom - k, corner)
    }

    private fun drawHint(c: Canvas, cx: Float, cy: Float, msg: String) {
        val tw = hint.measureText(msg)
        val padH = context.dpF(18f)
        val hh = context.dpF(36f)
        val rr = RectF(cx - tw / 2 - padH, cy - hh / 2, cx + tw / 2 + padH, cy + hh / 2)
        c.drawRoundRect(rr, hh / 2, hh / 2, pill)
        c.drawText(msg, cx, cy - (hint.descent() + hint.ascent()) / 2, hint)
    }

    @SuppressLint("ClickableViewAccessibility")
    override fun onTouchEvent(e: MotionEvent): Boolean {
        when (e.actionMasked) {
            MotionEvent.ACTION_DOWN -> { sx = e.x; sy = e.y; ex = e.x; ey = e.y; active = true; invalidate() }
            MotionEvent.ACTION_MOVE -> { ex = e.x; ey = e.y; invalidate() }
            MotionEvent.ACTION_UP -> {
                ex = e.x; ey = e.y
                val r = rect()
                if ((!fullWidth && r.width() < context.dpF(40f)) || r.height() < context.dpF(40f)) {
                    onCancel()
                } else {
                    // в координаты экрана (слой может начинаться не с 0,0)
                    getLocationOnScreen(loc)
                    onSelected(Rect((r.left + loc[0]).toInt(), (r.top + loc[1]).toInt(),
                        (r.right + loc[0]).toInt(), (r.bottom + loc[1]).toInt()))
                }
            }
        }
        return true
    }
}

// ==================================================================== плашки с переводом

/** Рисует плашки с переводом. Используется и поверх экрана, и на переведённом скриншоте. */
class PlateRenderer(private val ctx: Context) {
    private val fill = Paint(Paint.ANTI_ALIAS_FLAG)
    private val paint = TextPaint(Paint.ANTI_ALIAS_FLAG).apply { typeface = Typeface.create(Typeface.DEFAULT, Typeface.BOLD) }
    private val minFontPx = ctx.dpF(8f)

    /** [scale] — во сколько раз пиксели картинки крупнее пикселей экрана (для скриншота). */
    fun draw(c: Canvas, bubbles: List<Bubble>, ox: Float, oy: Float, maxFontPx: Float, scale: Float = 1f) {
        for (b in bubbles) {
            if (b.translation.isBlank()) continue
            val screen = b.kind == "screen"
            val pad = if (screen) ctx.dpF(2f) * scale else max(ctx.dpF(3f) * scale, 0.35f * b.lineHeight)
            // наклонённый текст: плашка берёт настоящие размеры и центр текста (прямая рамка
            // наклонённой строки заметно больше её самой) и рисуется под тем же углом
            val tilted = b.tilted
            val bw = if (tilted) b.tiltW else b.box.w
            val bh = if (tilted) b.tiltH else b.box.h
            // в манхве русский текст длиннее корейского — даём плашке стать шире;
            // обычный текст на экране закрываем точно по месту
            val w = if (screen) max(bw + 2 * pad, ctx.dpF(24f) * scale)
            else max(max(bw + 2 * pad, bw * 1.3f), ctx.dpF(48f) * scale)
            var h = bh + 2 * pad
            val cx = ox + (if (tilted) b.tiltCx else b.box.cx)
            val cy = oy + (if (tilted) b.tiltCy else b.box.cy)
            val textW = max(10, (w - 2 * ctx.dpF(3f) * scale).toInt())
            // на экране шрифт подбираем под высоту исходной строки
            val maxSize = if (screen) min(maxFontPx * scale, max(b.lineHeight * 0.9f, ctx.dpF(9f) * scale)) else maxFontPx * scale
            var size = maxSize
            var layout = makeLayout(b.translation, textW, size)
            while (layout.height > h - ctx.dpF(4f) * scale && size > minFontPx * scale) {
                size -= scale
                layout = makeLayout(b.translation, textW, size)
            }
            h = max(h, layout.height + ctx.dpF(6f) * scale)

            val rect = RectF(cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2)
            val bg = b.bgColor
            val lum = 0.299 * Color.red(bg) + 0.587 * Color.green(bg) + 0.114 * Color.blue(bg)
            if (screen) {
                fill.color = Color.argb(250, Color.red(bg), Color.green(bg), Color.blue(bg))
                paint.color = if (lum > 140) Color.rgb(15, 15, 15) else Color.rgb(250, 250, 250)
            } else if (b.kind == "free") {
                fill.color = Color.argb(235, 20, 20, 20)
                paint.color = Color.WHITE
            } else {
                fill.color = Color.argb(255, Color.red(bg), Color.green(bg), Color.blue(bg))
                paint.color = if (lum > 140) Color.rgb(15, 15, 15) else Color.rgb(250, 250, 250)
            }
            val radius = ctx.dpF(if (screen) 3f else 8f) * scale
            c.save()
            // у Canvas плюс — по часовой стрелке, у нас — против
            if (tilted) c.rotate(-b.angle, cx, cy)
            c.drawRoundRect(rect, radius, radius, fill)
            c.translate(rect.left + (w - textW) / 2, rect.top + (h - layout.height) / 2)
            layout.draw(c)
            c.restore()
        }
    }

    private fun makeLayout(text: String, width: Int, size: Float): StaticLayout {
        paint.textSize = size
        return StaticLayout.Builder.obtain(text, 0, text.length, paint, width)
            .setAlignment(Layout.Alignment.ALIGN_CENTER)
            .setIncludePad(false)
            .build()
    }
}

// ==================================================================== перевод поверх облаков

/** Рисует плашки с переводом поверх облаков и рамку зоны. Не перехватывает касания. */
class TranslationView(ctx: Context) : View(ctx) {
    private var bubbles: List<Bubble> = emptyList()
    /** Режим разработчика: рамки строк, найденных каждым проходом */
    private var passes: List<PassInfo> = emptyList()
    private val boxPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        style = Paint.Style.STROKE
        strokeWidth = ctx.dpF(1.5f)
    }
    private var region = Rect()
    private var maxFontPx = ctx.dpF(16f)
    private val renderer = PlateRenderer(ctx)
    private val loc = IntArray(2)
    private var frame: Rect? = null
    private val framePaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        style = Paint.Style.STROKE
        strokeWidth = ctx.dpF(3f)
        color = Brand.SAND
        pathEffect = android.graphics.DashPathEffect(floatArrayOf(ctx.dpF(12f), ctx.dpF(8f)), 0f)
    }
    private val frameFill = Paint().apply { color = Color.argb(40, 172, 134, 83) }
    private val labelBg = Paint(Paint.ANTI_ALIAS_FLAG).apply { color = Brand.INK }
    private val labelText = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        color = Brand.CREAM
        textSize = ctx.dpF(13f)
        typeface = Fonts.medium
    }
    private val hideFrame = Runnable { frame = null; invalidate() }

    val hasContent: Boolean get() = bubbles.any { it.translation.isNotBlank() }

    fun show(bubbles: List<Bubble>, region: Rect, maxFontSp: Int, debugPasses: List<PassInfo> = emptyList()) {
        this.bubbles = bubbles
        this.passes = debugPasses
        this.region = Rect(region)
        this.maxFontPx = TypedValue.applyDimension(TypedValue.COMPLEX_UNIT_SP, maxFontSp.toFloat(), context.resources.displayMetrics)
        invalidate()
    }

    fun clear() {
        bubbles = emptyList()
        passes = emptyList()
        invalidate()
    }

    /** Показать рамку текущей зоны на пару секунд. */
    fun showFrame(r: Rect, ms: Long = 2500) {
        frame = Rect(r)
        removeCallbacks(hideFrame)
        postDelayed(hideFrame, ms)
        invalidate()
    }

    override fun onDraw(c: Canvas) {
        getLocationOnScreen(loc)
        frame?.let { f ->
            val rf = RectF((f.left - loc[0]).toFloat(), (f.top - loc[1]).toFloat(),
                (f.right - loc[0]).toFloat(), (f.bottom - loc[1]).toFloat())
            c.drawRect(rf, frameFill)
            c.drawRect(rf, framePaint)
            val label = trf("Зона перевода · %s×%s", f.width(), f.height())
            val tw = labelText.measureText(label)
            val pad = context.dpF(10f)
            val top = max(rf.top + context.dpF(8f), context.dpF(8f))
            val pill = RectF(rf.left + context.dpF(8f), top, rf.left + context.dpF(8f) + tw + 2 * pad, top + context.dpF(28f))
            c.drawRoundRect(pill, context.dpF(14f), context.dpF(14f), labelBg)
            c.drawText(label, pill.left + pad, pill.centerY() - (labelText.descent() + labelText.ascent()) / 2, labelText)
        }
        val ox = (region.left - loc[0]).toFloat()
        val oy = (region.top - loc[1]).toFloat()
        if (bubbles.isNotEmpty()) renderer.draw(c, bubbles, ox, oy, maxFontPx)
        if (passes.isNotEmpty()) {
            // рамки поверх плашек: каждый вариант картинки — своим цветом
            val order = passes.map { it.variant }.distinct()
            for (p in passes) {
                val i = order.indexOf(p.variant).coerceAtLeast(0)
                boxPaint.color = DebugInfo.passColors[i % DebugInfo.passColors.size]
                val inset = i * context.dpF(1.5f)
                for (l in p.lines) {
                    c.drawRect(ox + l.box.x - inset, oy + l.box.y - inset, ox + l.box.x2 + inset, oy + l.box.y2 + inset, boxPaint)
                }
            }
        }
    }
}

// ==================================================================== меню плавающей кнопки

/** Небольшое меню рядом с плавающей кнопкой (по долгому нажатию). */
@SuppressLint("ViewConstructor")
class ButtonMenu(ctx: Context, items: List<Pair<String, () -> Unit>>, private val onDismiss: () -> Unit) :
    android.widget.LinearLayout(ctx) {
    init {
        orientation = VERTICAL
        background = android.graphics.drawable.GradientDrawable().apply {
            cornerRadius = ctx.dpF(18f)
            setColor(Brand.INK)
            setStroke(ctx.dpF(1.5f).toInt(), Brand.SAND)
        }
        val pad = ctx.dpF(6f).toInt()
        setPadding(pad, pad, pad, pad)
        for ((title, action) in items) {
            addView(android.widget.TextView(ctx).apply {
                text = title
                textSize = 15f
                typeface = Fonts.medium
                setTextColor(Brand.CREAM)
                val h = ctx.dpF(14f).toInt()
                val v = ctx.dpF(11f).toInt()
                setPadding(h, v, h, v)
                background = android.graphics.drawable.RippleDrawable(
                    android.content.res.ColorStateList.valueOf(Color.argb(60, 243, 227, 204)), null,
                    android.graphics.drawable.ColorDrawable(Color.WHITE))
                setOnClickListener { onDismiss(); action() }
            })
        }
    }

    @SuppressLint("ClickableViewAccessibility")
    override fun onTouchEvent(e: MotionEvent): Boolean {
        // касание за пределами меню закрывает его
        if (e.actionMasked == MotionEvent.ACTION_OUTSIDE) {
            onDismiss()
            return true
        }
        return super.onTouchEvent(e)
    }
}

// ==================================================================== панель режима разработчика

/**
 * Небольшая панель с отладочной информацией (только в режиме разработчика).
 * Свёрнута — одна строка с общим временем; нажатие разворачивает подробности.
 */
@SuppressLint("ViewConstructor")
class DebugPanel(ctx: Context, private val onCopy: (String) -> Unit, private val onClose: () -> Unit) :
    android.widget.LinearLayout(ctx) {
    private val body = android.widget.TextView(ctx).apply {
        textSize = 12f
        typeface = Typeface.MONOSPACE
        setTextColor(Brand.CREAM)
        setLineSpacing(0f, 1.1f)
    }
    private var full = ""
    private var expanded = false

    init {
        orientation = VERTICAL
        background = android.graphics.drawable.GradientDrawable().apply {
            cornerRadius = ctx.dpF(14f)
            setColor(Color.argb(235, 0x3D, 0x2B, 0x28))
            setStroke(ctx.dpF(1f).toInt(), Brand.SAND)
        }
        val pad = ctx.dpF(10f).toInt()
        setPadding(pad, pad, pad, ctx.dpF(4f).toInt())
        addView(body)
        body.setOnClickListener { expanded = !expanded; render() }
        val row = android.widget.LinearLayout(ctx).apply { gravity = android.view.Gravity.END }
        fun action(title: String, block: () -> Unit) = android.widget.TextView(ctx).apply {
            text = title
            textSize = 13f
            typeface = Fonts.medium
            setTextColor(Brand.SAND)
            val h = ctx.dpF(10f).toInt()
            val v = ctx.dpF(6f).toInt()
            setPadding(h, v, h, v)
            setOnClickListener { block() }
        }
        row.addView(action(tr("Подробнее")) { expanded = !expanded; render() })
        row.addView(action(tr("Копировать")) { onCopy(full) })
        row.addView(action(tr("Скрыть")) { onClose() })
        addView(row)
    }

    fun set(text: String) {
        full = text
        render()
    }

    private fun render() {
        body.text = if (expanded) full else full.lineSequence().take(2).joinToString("\n")
    }
}
