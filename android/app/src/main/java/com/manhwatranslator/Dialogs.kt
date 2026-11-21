package com.manhwatranslator

import android.app.Dialog
import android.content.Context
import android.graphics.Bitmap
import android.graphics.Canvas
import android.util.TypedValue
import android.widget.ImageView
import com.manhwatranslator.core.tr
import com.manhwatranslator.core.trf
import com.manhwatranslator.core.Settings
import android.graphics.Color
import android.graphics.drawable.ColorDrawable
import android.view.Gravity
import android.view.ViewGroup
import android.view.Window
import android.widget.FrameLayout
import android.widget.LinearLayout
import android.widget.ProgressBar
import android.widget.TextView
import android.content.res.ColorStateList
import com.manhwatranslator.core.PageResult

/** Диалоги в стиле приложения: ожидание, результат перевода, справка. */
object Dialogs {

    private fun base(ctx: Context, content: LinearLayout, cancelable: Boolean = true): Dialog {
        val d = Dialog(ctx)
        d.requestWindowFeature(Window.FEATURE_NO_TITLE)
        d.setCancelable(cancelable)
        content.background = rounded(T.c.surface, ctx.dpF(24f))
        val wrap = FrameLayout(ctx).apply {
            setPadding(ctx.dp(16), ctx.dp(16), ctx.dp(16), ctx.dp(16))
            addView(content, FrameLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.WRAP_CONTENT, Gravity.CENTER))
        }
        d.setContentView(wrap)
        d.window?.setBackgroundDrawable(ColorDrawable(Color.TRANSPARENT))
        d.window?.setLayout(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT)
        return d
    }

    fun progress(ctx: Context, text: String): Dialog {
        val ui = Ui(ctx)
        val box = ui.horizontal().apply { setPadding(ctx.dp(22), ctx.dp(22), ctx.dp(22), ctx.dp(22)) }
        val bar = ProgressBar(ctx).apply { indeterminateTintList = ColorStateList.valueOf(T.c.primary) }
        box.addView(bar, LinearLayout.LayoutParams(ctx.dp(36), ctx.dp(36)))
        box.addView(ui.text(text, 15.5f, T.c.text, Fonts.medium), lp(0, weight = 1f).margins(ctx, start = 16))
        return base(ctx, box, cancelable = false).also { it.show() }
    }

    fun result(ctx: Context, res: PageResult, image: Bitmap? = null) {
        val ui = Ui(ctx)
        val c = T.c
        val box = ui.vertical().apply { setPadding(ctx.dp(20), ctx.dp(20), ctx.dp(20), ctx.dp(16)) }
        box.addView(ui.text(tr("Результат"), 20f, c.text, Fonts.medium))
        val n = res.bubbles.count { it.translation.isNotBlank() }
        // подробности — только в режиме разработчика
        val stats = if (Settings(ctx).devMode) res.debug?.report() ?: "" else trf("Переведено облаков: %s", n)
        if (stats.isNotEmpty()) {
            box.addView(ui.caption(stats).apply {
                if (Settings(ctx).devMode) {
                    typeface = android.graphics.Typeface.MONOSPACE
                    textSize = 11.5f
                    setTextIsSelectable(true)
                }
            }, lp().margins(ctx, top = 2))
        }

        val list = ui.vertical()
        // переведённый скриншот: плашки с переводом прямо на картинке
        if (image != null && res.bubbles.any { it.translation.isNotBlank() }) {
            val out = image.copy(Bitmap.Config.ARGB_8888, true)
            val canvas = Canvas(out)
            // шрифт подбираем под ширину картинки, как если бы она была на весь экран
            val scale = out.width.toFloat() / ctx.resources.displayMetrics.widthPixels
            val maxFont = TypedValue.applyDimension(TypedValue.COMPLEX_UNIT_SP,
                Settings(ctx).maxFontSp.toFloat(), ctx.resources.displayMetrics)
            PlateRenderer(ctx).draw(canvas, res.bubbles, 0f, 0f, maxFont, scale)
            val iv = ImageView(ctx).apply {
                setImageBitmap(out)
                adjustViewBounds = true
                scaleType = ImageView.ScaleType.FIT_CENTER
                background = rounded(c.surfaceAlt, ctx.dpF(14f))
                clipToOutline = true
                outlineProvider = android.view.ViewOutlineProvider.BACKGROUND
            }
            list.addView(iv, lp().margins(ctx, top = 12))
            list.addView(ui.caption(tr("Ниже — список реплик с оригиналом")), lp().margins(ctx, top = 8))
        }
        if (res.error.isNotEmpty()) {
            list.addView(ui.text(res.error, 14f, c.text).apply {
                background = rounded(c.selectedBg, ctx.dpF(14f))
                setPadding(ctx.dp(14), ctx.dp(12), ctx.dp(14), ctx.dp(12))
            }, lp().margins(ctx, top = 12))
        }
        if (res.bubbles.isEmpty() && res.error.isEmpty()) {
            list.addView(ui.caption(if (res.lineCount == 0)
                tr("Распознавание не нашло ни одной строки. Попробуйте режим «Тщательный» или «Максимальный» в тонкой настройке.")
            else
                tr("Строки найдены, но все отфильтрованы. Проверьте настройки «Пропускать обрезанное» и «Не переводить звуки».")),
                lp().margins(ctx, top = 12))
        }
        res.bubbles.forEachIndexed { i, b ->
            val item = ui.horizontal().apply {
                gravity = Gravity.TOP
                background = rounded(c.surfaceAlt, ctx.dpF(14f), ctx.dp(1), c.border)
                setPadding(ctx.dp(12), ctx.dp(12), ctx.dp(14), ctx.dp(12))
            }
            val badge = TextView(ctx).apply {
                text = (i + 1).toString()
                textSize = 12f
                typeface = Fonts.medium
                gravity = Gravity.CENTER
                setTextColor(c.primary)
                background = oval(c.selectedBg)
            }
            item.addView(badge, LinearLayout.LayoutParams(ctx.dp(26), ctx.dp(26)))
            val col = ui.vertical()
            if (b.translation.isNotBlank()) {
                col.addView(ui.text(b.translation, 15.5f, c.text, Fonts.medium).apply { setTextIsSelectable(true) })
            } else {
                col.addView(ui.text("— " + b.skipReason.ifBlank { tr("нет перевода") } + " —", 14f, c.text2))
            }
            val tag = if (b.lang.isNotEmpty()) "${b.lang.uppercase()} · " else ""
            col.addView(ui.text(tag + b.text, 12.5f, c.text2).apply { setTextIsSelectable(true) }, lp().margins(ctx, top = 4))
            item.addView(col, lp(0, weight = 1f).margins(ctx, start = 12))
            list.addView(item, lp().margins(ctx, top = 8))
        }
        val maxH = (ctx.resources.displayMetrics.heightPixels * 0.62f).toInt()
        box.addView(MaxHeightScrollView(ctx, maxH).apply {
            isVerticalScrollBarEnabled = false
            addView(list)
        }, lp().margins(ctx, top = 6))

        val d = base(ctx, box)
        val ok = PillButton(ctx, tr("Готово"), null, PillStyle.PRIMARY, 48)
        ok.onClick { d.dismiss() }
        val copy = res.copyText()
        if (copy.isEmpty()) {
            box.addView(ok, lp().margins(ctx, top = 16))
        } else {
            // отдельную реплику можно выделить и скопировать долгим нажатием, всю страницу — кнопкой
            val row = ui.horizontal()
            val cp = PillButton(ctx, tr("Копировать"), null, PillStyle.OUTLINE, 48)
            cp.onClick { copyToClipboard(ctx, copy, trf("Перевод скопирован: облаков %s", res.translations().size)) }
            row.addView(cp, lp(0, weight = 1f))
            row.addView(ok, lp(0, weight = 1f).margins(ctx, start = 10))
            box.addView(row, lp().margins(ctx, top = 16))
        }
        d.show()
    }

    fun help(ctx: Context) {
        val ui = Ui(ctx)
        val c = T.c
        val box = ui.vertical().apply { setPadding(ctx.dp(20), ctx.dp(20), ctx.dp(20), ctx.dp(16)) }
        box.addView(ui.text(tr("Как пользоваться"), 20f, c.text, Fonts.medium))
        val steps = listOf(
            tr("Нажмите «Запустить переводчик». Разрешите показ поверх других приложений и запись экрана — выберите «Весь экран»."),
            tr("Откройте читалку. Справа появится круглая кнопка «Аа» — её можно перетащить куда удобно."),
            tr("Нажмите «Аа»: в первый раз выделите пальцем область чтения, дальше она запомнится."),
            tr("Перевод появится прямо поверх облаков. Нажмите «Аа» ещё раз — перевод исчезнет, листайте дальше."),
            tr("Долгое нажатие на кнопку открывает меню: новая зона, показать текущую зону, смена режима, остановка."),
            tr("Режим Live: перевод появляется сам, когда страница перестаёт двигаться. Нажатие на кнопку — пауза."),
        )
        val list = ui.vertical()
        steps.forEachIndexed { i, t ->
            val row = ui.horizontal().apply { gravity = Gravity.TOP }
            row.addView(TextView(ctx).apply {
                text = (i + 1).toString()
                textSize = 13f
                typeface = Fonts.medium
                gravity = Gravity.CENTER
                setTextColor(c.onPrimary)
                background = oval(c.primary)
            }, LinearLayout.LayoutParams(ctx.dp(26), ctx.dp(26)))
            row.addView(ui.text(t, 14.5f, c.text), lp(0, weight = 1f).margins(ctx, start = 12))
            list.addView(row, lp().margins(ctx, top = 14))
        }
        list.addView(ui.caption(tr("Android спрашивает разрешение на запись экрана при каждом запуске переводчика — это ограничение системы. При блокировке экрана запись может остановиться, тогда запустите снова.")).apply {
            background = rounded(c.selectedBg, ctx.dpF(14f))
            setPadding(ctx.dp(14), ctx.dp(12), ctx.dp(14), ctx.dp(12))
        }, lp().margins(ctx, top = 18))
        val maxH = (ctx.resources.displayMetrics.heightPixels * 0.62f).toInt()
        box.addView(MaxHeightScrollView(ctx, maxH).apply { addView(list) })
        val d = base(ctx, box)
        val ok = PillButton(ctx, tr("Понятно"), null, PillStyle.PRIMARY, 48)
        ok.onClick { d.dismiss() }
        box.addView(ok, lp().margins(ctx, top = 16))
        d.show()
    }
}
