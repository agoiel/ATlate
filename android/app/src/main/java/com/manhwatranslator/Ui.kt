package com.manhwatranslator

import android.app.Activity
import android.content.Context
import android.content.res.ColorStateList
import android.content.res.Configuration
import android.graphics.Canvas
import android.graphics.Color
import android.graphics.ColorFilter
import android.graphics.LinearGradient
import android.graphics.Paint
import android.graphics.Path
import android.graphics.PixelFormat
import android.graphics.RectF
import android.graphics.Shader
import android.graphics.Typeface
import android.graphics.drawable.Drawable
import android.graphics.drawable.GradientDrawable
import android.graphics.drawable.RippleDrawable
import android.graphics.drawable.StateListDrawable
import android.os.Build
import android.widget.Toast
import android.text.Editable
import android.text.InputType
import android.text.TextWatcher
import android.view.Gravity
import android.view.View
import android.view.ViewGroup
import android.view.WindowInsets
import android.view.WindowInsetsController
import android.widget.EditText
import android.widget.FrameLayout
import android.widget.ImageView
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.Switch
import android.widget.TextView
import kotlin.math.roundToInt

// ==================================================================== палитра

/** Цвета бренда — взяты с иконки приложения. */
object Brand {
    val INK = Color.parseColor("#3D2B28")     // тёмные волосы
    val COCOA = Color.parseColor("#6B4A33")   // тёплый коричневый
    val SAND = Color.parseColor("#AC8653")    // фон иконки
    val CREAM = Color.parseColor("#F3E3CC")   // кожа и страницы книги
    val BOW = Color.parseColor("#8D2A32")     // бант
}

/** Цвета интерфейса для светлой и тёмной темы, собранные из палитры бренда. */
class Palette(val dark: Boolean) {
    val bg = if (dark) Color.parseColor("#211816") else Color.parseColor("#F7EFE4")
    val surface = if (dark) Color.parseColor("#33241F") else Color.WHITE
    val surfaceAlt = if (dark) Color.parseColor("#3F2D27") else Color.parseColor("#FBF6EF")
    val border = if (dark) Color.parseColor("#574036") else Color.parseColor("#EBDCC8")
    val text = if (dark) Color.parseColor("#F5E9DA") else Brand.INK
    val text2 = if (dark) Color.parseColor("#C9B29A") else Color.parseColor("#7C6455")
    val primary = if (dark) Color.parseColor("#D9B27C") else Brand.COCOA
    val onPrimary = if (dark) Brand.INK else Color.WHITE
    val selectedBg = if (dark) Color.parseColor("#574036") else Brand.CREAM
    val heroStart = Brand.INK
    val heroEnd = Color.parseColor("#7A5638")
    val ripple = if (dark) Color.argb(50, 243, 227, 204) else Color.argb(40, 107, 74, 51)
}

object T {
    var c = Palette(false)
        private set

    fun init(ctx: Context) {
        val night = ctx.resources.configuration.uiMode and Configuration.UI_MODE_NIGHT_MASK
        c = Palette(night == Configuration.UI_MODE_NIGHT_YES)
    }
}

object Fonts {
    val regular: Typeface = Typeface.create("sans-serif", Typeface.NORMAL)
    val medium: Typeface = Typeface.create("sans-serif-medium", Typeface.NORMAL)
    val bold: Typeface = Typeface.create("sans-serif", Typeface.BOLD)
    val mono: Typeface = Typeface.MONOSPACE
}

// ==================================================================== утилиты

fun Context.dp(v: Int): Int = (v * resources.displayMetrics.density).roundToInt()
fun Context.dpF(v: Float): Float = v * resources.displayMetrics.density

fun rounded(color: Int, radius: Float, strokeWidth: Int = 0, strokeColor: Int = 0): GradientDrawable =
    GradientDrawable().apply {
        shape = GradientDrawable.RECTANGLE
        cornerRadius = radius
        setColor(color)
        if (strokeWidth > 0) setStroke(strokeWidth, strokeColor)
    }

fun oval(color: Int): GradientDrawable = GradientDrawable().apply {
    shape = GradientDrawable.OVAL
    setColor(color)
}

/** Фон с эффектом нажатия. */
fun pressable(content: Drawable?, radius: Float, rippleColor: Int = T.c.ripple): Drawable =
    RippleDrawable(ColorStateList.valueOf(rippleColor), content, rounded(Color.WHITE, radius))

fun Context.tinted(res: Int, color: Int): Drawable = getDrawable(res)!!.mutate().apply { setTint(color) }

fun lp(w: Int = ViewGroup.LayoutParams.MATCH_PARENT, h: Int = ViewGroup.LayoutParams.WRAP_CONTENT,
       weight: Float = 0f): LinearLayout.LayoutParams = LinearLayout.LayoutParams(w, h, weight)

fun LinearLayout.LayoutParams.margins(ctx: Context, start: Int = 0, top: Int = 0, end: Int = 0, bottom: Int = 0) =
    apply { setMargins(ctx.dp(start), ctx.dp(top), ctx.dp(end), ctx.dp(bottom)) }

fun EditText.onText(block: (String) -> Unit) = addTextChangedListener(object : TextWatcher {
    override fun beforeTextChanged(s: CharSequence?, start: Int, count: Int, after: Int) {}
    override fun onTextChanged(s: CharSequence?, start: Int, before: Int, count: Int) {}
    override fun afterTextChanged(s: Editable?) = block(s?.toString() ?: "")
})

/** Отступы под системные панели (приложение рисуется от края до края). */
fun View.onSystemBars(block: (top: Int, bottom: Int) -> Unit) {
    setOnApplyWindowInsetsListener { _, insets ->
        val (top, bottom) = if (Build.VERSION.SDK_INT >= 30) {
            val i = insets.getInsets(WindowInsets.Type.systemBars())
            i.top to i.bottom
        } else {
            legacyInsets(insets)
        }
        block(top, bottom)
        insets
    }
    requestApplyInsets()
}

@Suppress("DEPRECATION")
private fun legacyInsets(i: WindowInsets): Pair<Int, Int> = i.systemWindowInsetTop to i.systemWindowInsetBottom

/** Окно от края до края: шапка уходит под строку состояния. */
@Suppress("DEPRECATION")
fun Activity.setupEdgeToEdge() {
    val lightNav = !T.c.dark
    window.statusBarColor = Color.TRANSPARENT
    window.navigationBarColor = Color.TRANSPARENT
    if (Build.VERSION.SDK_INT >= 30) {
        window.setDecorFitsSystemWindows(false)
        window.insetsController?.setSystemBarsAppearance(
            if (lightNav) WindowInsetsController.APPEARANCE_LIGHT_NAVIGATION_BARS else 0,
            WindowInsetsController.APPEARANCE_LIGHT_NAVIGATION_BARS or WindowInsetsController.APPEARANCE_LIGHT_STATUS_BARS,
        )
    } else {
        window.decorView.systemUiVisibility = View.SYSTEM_UI_FLAG_LAYOUT_FULLSCREEN or
            View.SYSTEM_UI_FLAG_LAYOUT_STABLE or View.SYSTEM_UI_FLAG_LAYOUT_HIDE_NAVIGATION or
            (if (lightNav) View.SYSTEM_UI_FLAG_LIGHT_NAVIGATION_BAR else 0)
    }
}

// ==================================================================== фабрика простых элементов

class Ui(val ctx: Context) {
    private val c get() = T.c
    fun dp(v: Int) = ctx.dp(v)

    fun text(t: String, sizeSp: Float, color: Int = c.text, font: Typeface = Fonts.regular): TextView =
        TextView(ctx).apply {
            text = t
            textSize = sizeSp
            setTextColor(color)
            typeface = font
            setLineSpacing(0f, 1.15f)
        }

    fun caption(t: String): TextView = text(t, 13f, c.text2)

    fun icon(res: Int, color: Int, sizeDp: Int = 22): ImageView = ImageView(ctx).apply {
        setImageDrawable(ctx.tinted(res, color))
        layoutParams = LinearLayout.LayoutParams(dp(sizeDp), dp(sizeDp))
    }

    fun vertical(): LinearLayout = LinearLayout(ctx).apply { orientation = LinearLayout.VERTICAL }

    fun horizontal(): LinearLayout = LinearLayout(ctx).apply {
        orientation = LinearLayout.HORIZONTAL
        gravity = Gravity.CENTER_VERTICAL
    }

    /** Белая карточка со скруглёнными углами и тонкой рамкой. */
    fun card(): LinearLayout = vertical().apply {
        background = rounded(c.surface, ctx.dpF(20f), dp(1), c.border)
        setPadding(dp(16), dp(16), dp(16), dp(16))
    }

    /** Заголовок карточки: иконка в кружке + название + подпись. */
    fun cardHeader(title: String, iconRes: Int, subtitle: String? = null): LinearLayout = horizontal().apply {
        val badge = FrameLayout(ctx).apply {
            background = rounded(c.selectedBg, ctx.dpF(12f))
            addView(icon(iconRes, c.primary, 20), FrameLayout.LayoutParams(dp(20), dp(20), Gravity.CENTER))
        }
        addView(badge, LinearLayout.LayoutParams(dp(36), dp(36)))
        val col = vertical()
        col.addView(text(title, 16.5f, c.text, Fonts.medium))
        if (subtitle != null) col.addView(text(subtitle, 12.5f, c.text2))
        addView(col, lp(0, weight = 1f).margins(ctx, start = 12))
    }

    fun spacer(hDp: Int): View = View(ctx).apply { layoutParams = lp(h = dp(hDp)) }
}

// ==================================================================== кнопки

enum class PillStyle { PRIMARY, HERO, HERO_OUTLINE, OUTLINE, TONAL }

/** Кнопка-таблетка с иконкой. */
class PillButton(ctx: Context, text: String, iconRes: Int? = null, style: PillStyle = PillStyle.PRIMARY,
                 private val heightDp: Int = 52) : LinearLayout(ctx) {
    private val label = TextView(ctx)
    private val iconView = ImageView(ctx)

    init {
        orientation = HORIZONTAL
        gravity = Gravity.CENTER
        isClickable = true
        isFocusable = true
        minimumHeight = ctx.dp(heightDp)
        setPadding(ctx.dp(18), 0, ctx.dp(18), 0)
        label.textSize = if (heightDp >= 52) 16.5f else 14.5f
        label.typeface = Fonts.medium
        addView(iconView, LayoutParams(ctx.dp(20), ctx.dp(20)).apply { marginEnd = ctx.dp(10) })
        addView(label)
        set(text, iconRes, style)
    }

    fun set(text: String, iconRes: Int?, style: PillStyle) {
        val c = T.c
        val r = context.dpF(heightDp / 2f)
        val (bg, fg) = when (style) {
            PillStyle.PRIMARY -> rounded(c.primary, r) to c.onPrimary
            PillStyle.HERO -> rounded(Brand.CREAM, r) to Brand.INK
            PillStyle.HERO_OUTLINE -> rounded(Color.argb(30, 255, 255, 255), r, context.dp(2), Brand.CREAM) to Brand.CREAM
            PillStyle.OUTLINE -> rounded(Color.TRANSPARENT, r, context.dp(2), c.primary) to c.primary
            PillStyle.TONAL -> rounded(c.selectedBg, r) to c.primary
        }
        val ripple = if (style == PillStyle.PRIMARY || style == PillStyle.HERO_OUTLINE)
            Color.argb(60, 255, 255, 255) else T.c.ripple
        background = pressable(bg, r, ripple)
        label.text = text
        label.setTextColor(fg)
        if (iconRes != null) {
            iconView.setImageDrawable(context.tinted(iconRes, fg))
            iconView.visibility = VISIBLE
        } else {
            iconView.visibility = GONE
        }
    }

    fun onClick(block: () -> Unit) = setOnClickListener { block() }
}

/** Плитка быстрого действия: иконка в кружке и подпись. */
class Tile(ctx: Context, title: String, iconRes: Int, onClick: () -> Unit) : LinearLayout(ctx) {
    init {
        val c = T.c
        orientation = VERTICAL
        gravity = Gravity.CENTER_HORIZONTAL
        isClickable = true
        isFocusable = true
        val r = ctx.dpF(18f)
        background = pressable(rounded(c.surface, r, ctx.dp(1), c.border), r)
        elevation = ctx.dpF(if (c.dark) 0f else 6f)
        setPadding(ctx.dp(8), ctx.dp(14), ctx.dp(8), ctx.dp(12))
        val circle = FrameLayout(ctx).apply {
            background = oval(c.selectedBg)
            addView(ImageView(ctx).apply { setImageDrawable(ctx.tinted(iconRes, c.primary)) },
                FrameLayout.LayoutParams(ctx.dp(22), ctx.dp(22), Gravity.CENTER))
        }
        addView(circle, LayoutParams(ctx.dp(44), ctx.dp(44)))
        addView(TextView(ctx).apply {
            text = title
            textSize = 12.5f
            typeface = Fonts.medium
            setTextColor(c.text)
            gravity = Gravity.CENTER
            maxLines = 2
            setLineSpacing(0f, 1.1f)
        }, LayoutParams(LayoutParams.WRAP_CONTENT, LayoutParams.WRAP_CONTENT).apply { topMargin = ctx.dp(8) })
        setOnClickListener { onClick() }
    }
}

// ==================================================================== выбор значения

/** Поле выбора: подпись сверху, значение и стрелка вниз. По нажатию — список. */
class PickerView(ctx: Context, label: String, onClick: () -> Unit) : LinearLayout(ctx) {
    private val value = TextView(ctx)

    init {
        val c = T.c
        orientation = VERTICAL
        isClickable = true
        isFocusable = true
        val r = ctx.dpF(14f)
        background = pressable(rounded(c.surfaceAlt, r, ctx.dp(1), c.border), r)
        setPadding(ctx.dp(14), ctx.dp(10), ctx.dp(10), ctx.dp(10))
        addView(TextView(ctx).apply {
            text = label
            textSize = 11.5f
            setTextColor(c.text2)
            typeface = Fonts.medium
        })
        val row = LinearLayout(ctx).apply { orientation = HORIZONTAL; gravity = Gravity.CENTER_VERTICAL }
        value.textSize = 15f
        value.typeface = Fonts.medium
        value.setTextColor(c.text)
        value.maxLines = 1
        value.ellipsize = android.text.TextUtils.TruncateAt.END
        row.addView(value, LayoutParams(0, LayoutParams.WRAP_CONTENT, 1f))
        row.addView(ImageView(ctx).apply { setImageDrawable(ctx.tinted(R.drawable.ic_chevron, c.primary)) },
            LayoutParams(ctx.dp(20), ctx.dp(20)))
        addView(row, LayoutParams(LayoutParams.MATCH_PARENT, LayoutParams.WRAP_CONTENT).apply { topMargin = ctx.dp(2) })
        setOnClickListener { onClick() }
    }

    fun setValue(text: String) {
        value.text = text
    }
}

/** Пункт списка с выбором (переводчик): название, описание, галочка. */
class ChoiceItem(ctx: Context, title: String, subtitle: String, onClick: () -> Unit) : LinearLayout(ctx) {
    private val titleView = TextView(ctx)
    private val check = ImageView(ctx)

    init {
        val c = T.c
        orientation = HORIZONTAL
        gravity = Gravity.CENTER_VERTICAL
        isClickable = true
        isFocusable = true
        setPadding(ctx.dp(14), ctx.dp(11), ctx.dp(12), ctx.dp(11))
        val col = LinearLayout(ctx).apply { orientation = VERTICAL }
        titleView.text = title
        titleView.textSize = 15f
        titleView.typeface = Fonts.medium
        titleView.setTextColor(c.text)
        col.addView(titleView)
        col.addView(TextView(ctx).apply {
            text = subtitle
            textSize = 12.5f
            setTextColor(c.text2)
        })
        addView(col, LayoutParams(0, LayoutParams.WRAP_CONTENT, 1f))
        check.setImageDrawable(ctx.tinted(R.drawable.ic_check, c.onPrimary))
        check.background = oval(c.primary)
        check.setPadding(ctx.dp(4), ctx.dp(4), ctx.dp(4), ctx.dp(4))
        addView(check, LayoutParams(ctx.dp(24), ctx.dp(24)))
        setOnClickListener { onClick() }
        setChecked(false)
    }

    fun setChecked(on: Boolean) {
        val c = T.c
        val r = context.dpF(14f)
        background = pressable(
            if (on) rounded(c.selectedBg, r, context.dp(2), c.primary) else rounded(c.surfaceAlt, r, context.dp(1), c.border), r)
        check.visibility = if (on) VISIBLE else INVISIBLE
    }
}

// ==================================================================== ввод

/** Поле ввода с подписью. Рамка подсвечивается, когда поле активно. */
class Field(ctx: Context, label: String, value: String, hint: String = "", secret: Boolean = false,
            multiline: Boolean = false, onChange: (String) -> Unit = {}) : LinearLayout(ctx) {
    val edit = EditText(ctx)

    init {
        val c = T.c
        orientation = VERTICAL
        if (label.isNotEmpty()) {
            addView(TextView(ctx).apply {
                text = label
                textSize = 12.5f
                typeface = Fonts.medium
                setTextColor(c.text2)
            }, LayoutParams(LayoutParams.MATCH_PARENT, LayoutParams.WRAP_CONTENT).apply { bottomMargin = ctx.dp(6) })
        }
        val r = ctx.dpF(14f)
        edit.background = StateListDrawable().apply {
            addState(intArrayOf(android.R.attr.state_focused), rounded(c.surface, r, ctx.dp(2), c.primary))
            addState(intArrayOf(), rounded(c.surfaceAlt, r, ctx.dp(1), c.border))
        }
        edit.setPadding(ctx.dp(14), ctx.dp(12), ctx.dp(14), ctx.dp(12))
        edit.setTextColor(c.text)
        edit.setHintTextColor(Color.argb(130, Color.red(c.text2), Color.green(c.text2), Color.blue(c.text2)))
        edit.textSize = 15f
        edit.hint = hint
        edit.setText(value)
        if (multiline) {
            edit.inputType = InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_FLAG_MULTI_LINE or
                InputType.TYPE_TEXT_FLAG_NO_SUGGESTIONS
            edit.gravity = Gravity.TOP or Gravity.START
            edit.minLines = 10
            edit.setHorizontallyScrolling(false)
            edit.setLineSpacing(0f, 1.25f)
        } else {
            edit.setSingleLine(true)
            edit.inputType = if (secret) InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_VARIATION_PASSWORD
            else InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_VARIATION_URI
        }
        addView(edit, LayoutParams(LayoutParams.MATCH_PARENT, LayoutParams.WRAP_CONTENT))
        edit.onText(onChange)
    }
}

/** Число с кнопками «−» и «+». */
class Stepper(ctx: Context, title: String, subtitle: String?, initial: Float, private val step: Float,
              private val min: Float, private val max: Float, private val format: (Float) -> String,
              private val onChange: (Float) -> Unit) : LinearLayout(ctx) {
    private val valueView = TextView(ctx)
    var value = initial
        private set

    init {
        val c = T.c
        orientation = HORIZONTAL
        gravity = Gravity.CENTER_VERTICAL
        val col = LinearLayout(ctx).apply { orientation = VERTICAL }
        col.addView(TextView(ctx).apply { text = title; textSize = 15f; setTextColor(c.text) })
        if (subtitle != null) col.addView(TextView(ctx).apply { text = subtitle; textSize = 12.5f; setTextColor(c.text2) })
        addView(col, LayoutParams(0, LayoutParams.WRAP_CONTENT, 1f))
        addView(roundButton(R.drawable.ic_minus) { change(-step) })
        valueView.textSize = 15f
        valueView.typeface = Fonts.medium
        valueView.setTextColor(c.text)
        valueView.gravity = Gravity.CENTER
        addView(valueView, LayoutParams(ctx.dp(56), LayoutParams.WRAP_CONTENT))
        addView(roundButton(R.drawable.ic_plus) { change(step) })
        render()
    }

    private fun roundButton(res: Int, onClick: () -> Unit): View = FrameLayout(context).apply {
        val c = T.c
        background = pressable(oval(c.selectedBg), context.dpF(18f))
        isClickable = true
        addView(ImageView(context).apply { setImageDrawable(context.tinted(res, c.primary)) },
            FrameLayout.LayoutParams(context.dp(18), context.dp(18), Gravity.CENTER))
        layoutParams = LayoutParams(context.dp(36), context.dp(36))
        setOnClickListener { onClick() }
    }

    private fun change(delta: Float) {
        val nv = ((value + delta) * 100).roundToInt() / 100f
        value = nv.coerceIn(min, max)
        render()
        onChange(value)
    }

    private fun render() {
        valueView.text = format(value)
    }
}

/** Строка с переключателем. */
class SwitchRow(ctx: Context, title: String, subtitle: String?, checked: Boolean, onChange: (Boolean) -> Unit) :
    LinearLayout(ctx) {
    private val sw = Switch(ctx)
    private var silent = false

    /** Положение переключателя; изменение отсюда не вызывает onChange (для обновления из кода). */
    var checked: Boolean
        get() = sw.isChecked
        set(v) {
            silent = true
            sw.isChecked = v
            silent = false
        }

    init {
        val c = T.c
        orientation = HORIZONTAL
        gravity = Gravity.CENTER_VERTICAL
        setPadding(0, ctx.dp(6), 0, ctx.dp(6))
        val col = LinearLayout(ctx).apply { orientation = VERTICAL }
        col.addView(TextView(ctx).apply { text = title; textSize = 15f; setTextColor(c.text) })
        if (subtitle != null) col.addView(TextView(ctx).apply { text = subtitle; textSize = 12.5f; setTextColor(c.text2) })
        addView(col, LayoutParams(0, LayoutParams.WRAP_CONTENT, 1f))
        val states = arrayOf(intArrayOf(android.R.attr.state_checked), intArrayOf())
        sw.thumbTintList = ColorStateList(states, intArrayOf(c.primary, if (c.dark) Color.parseColor("#8FB3B9") else Color.WHITE))
        sw.trackTintList = ColorStateList(states, intArrayOf(
            Color.argb(110, Color.red(c.primary), Color.green(c.primary), Color.blue(c.primary)), c.border))
        sw.isChecked = checked
        sw.setOnCheckedChangeListener { _, on -> if (!silent) onChange(on) }
        addView(sw)
        setOnClickListener { sw.toggle() }
    }
}

/** Метка состояния с цветной точкой. */
class StatusChip(ctx: Context) : LinearLayout(ctx) {
    private val dot = View(ctx)
    private val label = TextView(ctx)

    init {
        orientation = HORIZONTAL
        gravity = Gravity.CENTER_VERTICAL
        background = rounded(Color.argb(38, 255, 255, 255), ctx.dpF(14f))
        setPadding(ctx.dp(10), ctx.dp(5), ctx.dp(12), ctx.dp(5))
        addView(dot, LayoutParams(ctx.dp(8), ctx.dp(8)).apply { marginEnd = ctx.dp(8) })
        label.textSize = 12.5f
        label.typeface = Fonts.medium
        label.setTextColor(Brand.CREAM)
        addView(label)
    }

    fun set(active: Boolean, text: String) {
        dot.background = oval(if (active) Color.parseColor("#9BE7C4") else Color.argb(140, 196, 223, 230))
        label.text = text
    }
}

/** ScrollView с ограничением высоты (для диалогов). */
class MaxHeightScrollView(ctx: Context, private val maxHeightPx: Int) : ScrollView(ctx) {
    override fun onMeasure(widthMeasureSpec: Int, heightMeasureSpec: Int) {
        super.onMeasure(widthMeasureSpec, MeasureSpec.makeMeasureSpec(maxHeightPx, MeasureSpec.AT_MOST))
    }
}

// ==================================================================== фон шапки

/** Градиент из палитры, скруглённый низ и полупрозрачные облачка для настроения. */
class HeroDrawable(private val ctx: Context, private val radius: Float) : Drawable() {
    private val paint = Paint(Paint.ANTI_ALIAS_FLAG)
    private val deco = Paint(Paint.ANTI_ALIAS_FLAG).apply { color = Color.WHITE }
    private val line = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        color = Color.WHITE
        style = Paint.Style.STROKE
        strokeCap = Paint.Cap.ROUND
    }
    private val clip = Path()
    private val bubble = Path()

    override fun draw(canvas: Canvas) {
        val b = bounds
        if (b.isEmpty) return
        val w = b.width().toFloat()
        val h = b.height().toFloat()
        paint.shader = LinearGradient(b.left.toFloat(), b.top.toFloat(), b.right.toFloat(), b.bottom.toFloat(),
            T.c.heroStart, T.c.heroEnd, Shader.TileMode.CLAMP)
        clip.reset()
        clip.addRoundRect(RectF(b), floatArrayOf(0f, 0f, 0f, 0f, radius, radius, radius, radius), Path.Direction.CW)
        canvas.save()
        canvas.clipPath(clip)
        canvas.drawRect(b, paint)

        // большие мягкие круги
        deco.alpha = 16
        canvas.drawCircle(b.left + w * 0.92f, b.top + h * 0.18f, w * 0.30f, deco)
        deco.alpha = 10
        canvas.drawCircle(b.left + w * 1.02f, b.top + h * 0.95f, w * 0.26f, deco)

        // контур облачка с хвостиком и строками текста
        val bw = w * 0.30f
        val bh = bw * 0.62f
        val left = b.left + w * 0.66f
        val top = b.top + h * 0.10f
        bubble.reset()
        bubble.addOval(RectF(left, top, left + bw, top + bh), Path.Direction.CW)
        line.alpha = 46
        line.strokeWidth = ctx.dpF(2f)
        canvas.drawPath(bubble, line)
        val tail = Path().apply {
            moveTo(left + bw * 0.28f, top + bh * 0.92f)
            lineTo(left + bw * 0.18f, top + bh * 1.22f)
            lineTo(left + bw * 0.42f, top + bh * 0.98f)
        }
        canvas.drawPath(tail, line)
        line.strokeWidth = ctx.dpF(3f)
        line.alpha = 40
        val cx = left + bw / 2
        canvas.drawLine(cx - bw * 0.24f, top + bh * 0.38f, cx + bw * 0.24f, top + bh * 0.38f, line)
        canvas.drawLine(cx - bw * 0.30f, top + bh * 0.54f, cx + bw * 0.30f, top + bh * 0.54f, line)
        canvas.drawLine(cx - bw * 0.16f, top + bh * 0.70f, cx + bw * 0.16f, top + bh * 0.70f, line)
        canvas.restore()
    }

    override fun setAlpha(alpha: Int) {}
    override fun setColorFilter(colorFilter: ColorFilter?) {}

    @Deprecated("Deprecated in Java")
    override fun getOpacity(): Int = PixelFormat.TRANSLUCENT
}

// ==================================================================== раскрывающийся список

/**
 * Поле выбора, которое по нажатию плавно раскрывается в список вариантов прямо на экране.
 * Варианты разбиты на группы (подзаголовки).
 */
class Dropdown(ctx: Context, label: String, private val items: List<Item>, private val onSelect: (String) -> Unit) :
    LinearLayout(ctx) {

    data class Item(val key: String, val title: String, val subtitle: String, val group: String)

    private val header = LinearLayout(ctx)
    private val titleView = TextView(ctx)
    private val subtitleView = TextView(ctx)
    private val chevron = ImageView(ctx)
    private val list = LinearLayout(ctx)
    private val rows = LinkedHashMap<String, ChoiceItem>()
    private var expanded = false
    private var current = ""

    init {
        val c = T.c
        orientation = VERTICAL
        val r = ctx.dpF(14f)

        header.orientation = HORIZONTAL
        header.gravity = Gravity.CENTER_VERTICAL
        header.isClickable = true
        header.setPadding(ctx.dp(14), ctx.dp(10), ctx.dp(12), ctx.dp(10))
        val col = LinearLayout(ctx).apply { orientation = VERTICAL }
        col.addView(TextView(ctx).apply {
            text = label
            textSize = 11.5f
            typeface = Fonts.medium
            setTextColor(c.text2)
        })
        titleView.textSize = 16f
        titleView.typeface = Fonts.medium
        titleView.setTextColor(c.text)
        col.addView(titleView, LayoutParams(LayoutParams.MATCH_PARENT, LayoutParams.WRAP_CONTENT).apply { topMargin = ctx.dp(2) })
        subtitleView.textSize = 12.5f
        subtitleView.setTextColor(c.text2)
        col.addView(subtitleView)
        header.addView(col, LayoutParams(0, LayoutParams.WRAP_CONTENT, 1f))
        chevron.setImageDrawable(ctx.tinted(R.drawable.ic_chevron, c.primary))
        header.addView(chevron, LayoutParams(ctx.dp(22), ctx.dp(22)))
        header.setOnClickListener { toggle() }
        addView(header, LayoutParams(LayoutParams.MATCH_PARENT, LayoutParams.WRAP_CONTENT))

        list.orientation = VERTICAL
        list.visibility = GONE
        list.setPadding(ctx.dp(8), ctx.dp(2), ctx.dp(8), ctx.dp(10))
        var group = ""
        for (it in items) {
            if (it.group != group) {
                group = it.group
                list.addView(TextView(ctx).apply {
                    text = group
                    textSize = 12f
                    typeface = Fonts.medium
                    setTextColor(c.primary)
                    setPadding(ctx.dp(6), ctx.dp(12), 0, ctx.dp(2))
                })
            }
            val row = ChoiceItem(ctx, it.title, it.subtitle) { choose(it.key) }
            rows[it.key] = row
            list.addView(row, LayoutParams(LayoutParams.MATCH_PARENT, LayoutParams.WRAP_CONTENT).apply { topMargin = ctx.dp(6) })
        }
        addView(list, LayoutParams(LayoutParams.MATCH_PARENT, LayoutParams.WRAP_CONTENT))
        render()
        // плавное раскрытие и сворачивание
        layoutTransition = android.animation.LayoutTransition().apply {
            enableTransitionType(android.animation.LayoutTransition.CHANGING)
            setDuration(180)
        }
        background = rounded(c.surfaceAlt, r, ctx.dp(1), c.border)
        header.background = pressable(null, r)
    }

    fun select(key: String) {
        current = key
        render()
    }

    private fun choose(key: String) {
        current = key
        render()
        setExpanded(false)
        onSelect(key)
    }

    private fun toggle() = setExpanded(!expanded)

    private fun setExpanded(on: Boolean) {
        expanded = on
        list.visibility = if (on) VISIBLE else GONE
        chevron.animate().rotation(if (on) 180f else 0f).setDuration(180).start()
        val c = T.c
        background = if (on) rounded(c.surface, context.dpF(14f), context.dp(2), c.primary)
        else rounded(c.surfaceAlt, context.dpF(14f), context.dp(1), c.border)
    }

    private fun render() {
        val item = items.firstOrNull { it.key == current } ?: items.first()
        titleView.text = item.title
        subtitleView.text = item.subtitle
        rows.forEach { (k, v) -> v.setChecked(k == item.key) }
    }
}

// ==================================================================== переключатель из двух-трёх вариантов

/** Сегментированный переключатель для тёмной шапки (например, «Скриншот | Live»). */
class Segmented(ctx: Context, private val options: List<Pair<String, String>>, selected: String,
                private val onSelect: (String) -> Unit) : LinearLayout(ctx) {
    private val views = LinkedHashMap<String, TextView>()
    private var current = selected

    init {
        orientation = HORIZONTAL
        val r = ctx.dpF(22f)
        background = rounded(Color.argb(34, 255, 255, 255), r)
        val pad = ctx.dp(4)
        setPadding(pad, pad, pad, pad)
        for ((key, label) in options) {
            val tv = TextView(ctx).apply {
                text = label
                textSize = 14.5f
                typeface = Fonts.medium
                gravity = Gravity.CENTER
                setPadding(0, ctx.dp(9), 0, ctx.dp(9))
                isClickable = true
                setOnClickListener { select(key, notify = true) }
            }
            views[key] = tv
            addView(tv, LayoutParams(0, LayoutParams.WRAP_CONTENT, 1f))
        }
        render()
    }

    fun select(key: String, notify: Boolean = false) {
        if (key == current && !notify) return
        val changed = key != current
        current = key
        render()
        if (notify && changed) onSelect(key)
    }

    private fun render() {
        val r = context.dpF(18f)
        for ((k, v) in views) {
            val on = k == current
            v.background = if (on) rounded(Brand.CREAM, r) else null
            v.setTextColor(if (on) Brand.INK else Brand.CREAM)
        }
    }
}


/** Скопировать текст в буфер обмена. На Android 13+ система сама показывает, что скопировано. */
fun copyToClipboard(ctx: Context, text: String, done: String) {
    val cm = ctx.getSystemService(Context.CLIPBOARD_SERVICE) as android.content.ClipboardManager
    cm.setPrimaryClip(android.content.ClipData.newPlainText("ATlate", text))
    if (Build.VERSION.SDK_INT < 33) Toast.makeText(ctx, done, Toast.LENGTH_SHORT).show()
}
