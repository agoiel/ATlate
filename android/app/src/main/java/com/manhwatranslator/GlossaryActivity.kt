package com.manhwatranslator

import android.app.Activity
import android.app.AlertDialog
import android.os.Bundle
import android.view.Gravity
import android.view.View
import android.widget.FrameLayout
import android.widget.ImageView
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.TextView
import android.widget.Toast
import com.manhwatranslator.core.Glossary
import com.manhwatranslator.core.GlossaryStore
import com.manhwatranslator.core.I18n
import com.manhwatranslator.core.tr
import com.manhwatranslator.core.trf
import com.manhwatranslator.core.Settings

/**
 * Редактор глоссария. Каждая строка: «оригинал = перевод | заметка».
 * Заметка необязательна и помогает ИИ-переводчикам (пол, кто это, как обращаться).
 */
class GlossaryActivity : Activity() {

    companion object {
        const val EXTRA_FILE = "file"
    }

    private var file: String? = null
    /** Записи, которые были в файле при открытии (ключи терминов). */
    private var opened: Set<String> = emptySet()
    private lateinit var settings: Settings
    private lateinit var ui: Ui
    private lateinit var nameField: Field
    private lateinit var bodyField: Field
    private lateinit var counter: TextView

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        T.init(this)
        I18n.init(this)
        ui = Ui(this)
        settings = Settings(this)
        file = intent.getStringExtra(EXTRA_FILE)
        val g = file?.let { GlossaryStore.load(this, it) } ?: Glossary("", settings.sourceLang)
        opened = g.entries.map { Glossary.key(it.source) }.toHashSet()
        val c = T.c

        val page = ui.vertical().apply { setBackgroundColor(c.bg) }

        // ---- верхняя панель
        val bar = ui.horizontal().apply { setBackgroundColor(Brand.INK) }
        val back = FrameLayout(this).apply {
            isClickable = true
            background = pressable(null, dpF(20f), android.graphics.Color.argb(60, 255, 255, 255))
            addView(ImageView(this@GlossaryActivity).apply { setImageDrawable(tinted(R.drawable.ic_back, Brand.CREAM)) },
                FrameLayout.LayoutParams(dp(22), dp(22), Gravity.CENTER))
            setOnClickListener { finish() }
        }
        bar.addView(back, LinearLayout.LayoutParams(dp(40), dp(40)))
        val titles = ui.vertical()
        titles.addView(ui.text(if (file == null) tr("Новый глоссарий") else tr("Глоссарий"), 19f,
            android.graphics.Color.WHITE, Fonts.medium))
        titles.addView(ui.text(tr("Имена и термины тайтла"), 12.5f, Brand.CREAM))
        bar.addView(titles, lp(0, weight = 1f).margins(this, start = 8))
        page.addView(bar)

        // ---- содержимое
        val content = ui.vertical().apply { setPadding(dp(16), dp(16), dp(16), dp(16)) }
        nameField = Field(this, tr("НАЗВАНИЕ ТАЙТЛА"), g.name, tr("Например: мой любимый тайтл"))
        content.addView(nameField)

        val help = ui.vertical().apply {
            background = rounded(c.selectedBg, dpF(16f))
            setPadding(dp(14), dp(12), dp(14), dp(12))
        }
        help.addView(ui.text(tr("Одна запись на строку"), 14f, c.text, Fonts.medium))
        help.addView(ui.text(tr("헌터 = охотник\n형 = хён | обращение к старшему мужчине"), 13.5f, c.primary, Fonts.mono),
            lp().margins(this, top = 6))
        help.addView(ui.caption(tr("После | можно добавить заметку: пол, кто это, как обращаться. Она помогает ИИ-переводчикам.")), lp().margins(this, top = 6))
        if (g.entries.any { it.auto }) {
            help.addView(ui.caption(tr("[авто] — запись добавил ИИ-переводчик. Проверьте её: если всё верно, сотрите пометку; если нет — исправьте или удалите строку.")),
                lp().margins(this, top = 6))
        }
        content.addView(help, lp().margins(this, top = 14))

        bodyField = Field(this, tr("ЗАПИСИ"), g.toText(), tr("оригинал = перевод | заметка"), multiline = true) { updateCounter() }
        content.addView(bodyField, lp().margins(this, top = 14))
        counter = ui.caption("")
        content.addView(counter, lp().margins(this, top = 6))
        updateCounter()

        val scroll = ScrollView(this).apply {
            isFillViewport = true
            addView(content)
        }
        page.addView(scroll, lp(h = 0, weight = 1f))

        // ---- нижняя панель
        val bottom = ui.horizontal().apply {
            setBackgroundColor(c.surface)
            setPadding(dp(16), dp(12), dp(16), dp(12))
        }
        if (file != null) {
            val del = PillButton(this, tr("Удалить"), null, PillStyle.OUTLINE, 48)
            del.onClick { confirmDelete() }
            bottom.addView(del, lp(0, weight = 1f).margins(this, end = 10))
        }
        val save = PillButton(this, tr("Сохранить"), R.drawable.ic_check, PillStyle.PRIMARY, 48)
        save.onClick { save() }
        bottom.addView(save, lp(0, weight = 2f))
        page.addView(View(this).apply { setBackgroundColor(c.border) }, lp(h = dp(1)))
        page.addView(bottom)

        setContentView(page)
        setupEdgeToEdge()
        page.onSystemBars { top, bottomInset ->
            bar.setPadding(dp(8), top + dp(8), dp(16), dp(12))
            bottom.setPadding(dp(16), dp(12), dp(16), bottomInset + dp(12))
        }
    }

    private fun updateCounter() {
        val n = Glossary.parseText(bodyField.edit.text.toString()).size
        counter.text = trf("Записей: %s", n)
    }

    private fun save() {
        val name = nameField.edit.text.toString().trim()
        if (name.isEmpty()) {
            Toast.makeText(this, tr("Введите название тайтла"), Toast.LENGTH_SHORT).show()
            nameField.edit.requestFocus()
            return
        }
        val entries = Glossary.parseText(bodyField.edit.text.toString()).toMutableList()
        val target = file ?: GlossaryStore.fileNameFor(this, name)
        // «пополнять сам», язык оригинала и язык перевода глоссария сохраняются;
        // пометку [авто] у записи можно стереть — тогда она подтверждена
        val old = file?.let { GlossaryStore.load(this, it) }
        // пока редактор был открыт, авто-глоссарий мог пополниться (перевод шёл в фоне): эти записи
        // не теряем; удалённые здесь записи были в файле при открытии — они не возвращаются
        if (old != null) {
            val have = entries.map { Glossary.key(it.source) }.toHashSet()
            for (e in old.entries) {
                val k = Glossary.key(e.source)
                if (e.auto && k !in opened && have.add(k)) entries.add(e)
            }
        }
        GlossaryStore.save(this, target, Glossary(name, old?.sourceLang?.ifBlank { null } ?: settings.sourceLang, entries,
            old?.auto ?: false, old?.targetLang ?: ""))
        settings.glossary = target
        Toast.makeText(this, trf("Сохранено и выбрано: %s записей", entries.size), Toast.LENGTH_SHORT).show()
        finish()
    }

    private fun confirmDelete() {
        val f = file ?: return finish()
        AlertDialog.Builder(this)
            .setTitle(tr("Удалить глоссарий?"))
            .setMessage(tr("Записи этого тайтла будут удалены без возможности восстановления."))
            .setPositiveButton(tr("Удалить")) { _, _ ->
                GlossaryStore.delete(this, f)
                if (settings.glossary == f) settings.glossary = ""
                finish()
            }
            .setNegativeButton(tr("Отмена"), null)
            .show()
    }

    private fun dp(v: Int) = ui.dp(v)
}
