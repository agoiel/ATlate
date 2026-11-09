package com.manhwatranslator

import android.Manifest
import android.app.Activity
import android.app.AlertDialog
import android.content.Intent
import android.content.pm.PackageManager
import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.graphics.drawable.GradientDrawable
import android.media.projection.MediaProjectionConfig
import android.media.projection.MediaProjectionManager
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.view.Gravity
import android.view.View
import android.widget.FrameLayout
import android.widget.ImageView
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.TextView
import android.widget.Toast
import com.manhwatranslator.core.DevLog
import com.manhwatranslator.core.Enhance
import com.manhwatranslator.core.GlossaryStore
import com.manhwatranslator.core.I18n
import com.manhwatranslator.core.tr
import com.manhwatranslator.core.trf
import com.manhwatranslator.core.Languages
import com.manhwatranslator.core.Ocr
import com.manhwatranslator.core.PageResult
import com.manhwatranslator.core.Pipeline
import com.manhwatranslator.core.Settings
import com.manhwatranslator.core.TitleStore
import com.manhwatranslator.core.Translators
import kotlin.concurrent.thread

class MainActivity : Activity() {

    private companion object {
        const val REQ_CAPTURE = 1
        const val REQ_PICK = 2
        const val REQ_NOTIF = 3
    }

    private lateinit var s: Settings
    private lateinit var ui: Ui
    private val main = Handler(Looper.getMainLooper())
    private var testPipeline: Pipeline? = null

    private lateinit var heroInner: LinearLayout
    private lateinit var statusChip: StatusChip
    private lateinit var startBtn: PillButton
    private lateinit var modeSwitch: Segmented
    private lateinit var modeHint: TextView
    private lateinit var srcPicker: PickerView
    private lateinit var tgtPicker: PickerView
    private lateinit var glossPicker: PickerView
    private lateinit var titlePicker: PickerView
    private lateinit var keysBox: LinearLayout
    private lateinit var translatorDropdown: Dropdown
    private var lastRunning: Boolean? = null

    private val poll = object : Runnable {
        override fun run() {
            updateRunning()
            main.postDelayed(this, 1000)
        }
    }

    // ================================================================ экран
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        T.init(this)
        I18n.init(this)
        s = Settings(this)
        GlossaryStore.installBundled(this, s)
        ui = Ui(this)

        val root = ui.vertical()
        root.addView(buildHero())
        root.addView(buildTiles(), lp().margins(this, 16, -34, 16, 0))
        val content = ui.vertical()
        content.addView(buildLanguages(), lp().margins(this, top = 16))
        content.addView(buildTranslator(), lp().margins(this, top = 14))
        content.addView(buildGlossary(), lp().margins(this, top = 14))
        content.addView(buildTuning(), lp().margins(this, top = 14))
        content.addView(buildDeveloper(), lp().margins(this, top = 14))
        content.addView(ui.caption(tr("ATlate 0.5 · распознавание: Google ML Kit")).apply {
            gravity = Gravity.CENTER
            alpha = 0.8f
        }, lp().margins(this, top = 22))
        root.addView(content, lp().margins(this, 16, 0, 16, 0))

        val scroll = ScrollView(this).apply {
            isFillViewport = true
            isVerticalScrollBarEnabled = false
            setBackgroundColor(T.c.bg)
            addView(root)
        }
        setContentView(scroll)
        setupEdgeToEdge()
        scroll.onSystemBars { top, bottom ->
            heroInner.setPadding(dp(20), top + dp(18), dp(20), dp(56))
            root.setPadding(0, 0, 0, bottom + dp(24))
        }
        handleShare(intent)
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        handleShare(intent)
    }

    override fun onResume() {
        super.onResume()
        Ocr.requestModules(this, Languages.src(s.sourceLang))
        refreshGlossary()
        main.post(poll)
    }

    override fun onPause() {
        main.removeCallbacks(poll)
        super.onPause()
    }

    override fun onDestroy() {
        testPipeline?.close()
        super.onDestroy()
    }

    private fun dp(v: Int) = ui.dp(v)

    // ---------------------------------------------------------------- шапка
    private fun buildHero(): View {
        heroInner = ui.vertical().apply { background = HeroDrawable(this@MainActivity, dpF(28f)) }

        val brandRow = ui.horizontal()
        val logo = ImageView(this).apply {
            setImageResource(R.drawable.ic_launcher_art)
            scaleType = ImageView.ScaleType.CENTER_CROP
            // показываем середину слоя иконки (как на рабочем столе)
            scaleX = 1.45f
            scaleY = 1.45f
        }
        val logoFrame = FrameLayout(this).apply {
            background = oval(Brand.SAND)
            outlineProvider = android.view.ViewOutlineProvider.BACKGROUND
            clipToOutline = true
            addView(logo, FrameLayout.LayoutParams(dp(50), dp(50)))
            foreground = GradientDrawable().apply {
                shape = GradientDrawable.OVAL
                setStroke(dp(2), Brand.CREAM)
            }
        }
        brandRow.addView(logoFrame, LinearLayout.LayoutParams(dp(50), dp(50)))
        val titles = ui.vertical()
        titles.addView(ui.text("ATlate", 24f, android.graphics.Color.WHITE, Fonts.medium))
        titles.addView(ui.text(tr("Перевод манхвы прямо с экрана"), 13.5f, Brand.CREAM))
        brandRow.addView(titles, lp(0, weight = 1f).margins(this, start = 14))
        heroInner.addView(brandRow)

        statusChip = StatusChip(this)
        heroInner.addView(statusChip, lp(LinearLayout.LayoutParams.WRAP_CONTENT).margins(this, top = 22))

        modeSwitch = Segmented(this, listOf("shot" to tr("Скриншот"), "live" to "Live"), s.translateMode) { mode ->
            s.translateMode = mode
            updateModeHint()
            if (OverlayService.running) {
                toast(tr("Режим сменится при следующем запуске. Или переключите его в меню кнопки (долгое нажатие)."))
            }
        }
        heroInner.addView(modeSwitch, lp().margins(this, top = 16))

        startBtn = PillButton(this, "", R.drawable.ic_play, PillStyle.HERO, 56)
        startBtn.onClick { onStartClicked() }
        heroInner.addView(startBtn, lp().margins(this, top = 14))

        modeHint = ui.text("", 12.5f, Brand.CREAM).apply { alpha = 0.85f }
        heroInner.addView(modeHint, lp().margins(this, top = 12))
        updateModeHint()
        updateRunning()
        return heroInner
    }

    private fun updateModeHint() {
        modeHint.text = if (s.translateMode == "live")
            tr("Live: перевод появляется сам, когда страница перестаёт двигаться  ·  нажатие на кнопку — пауза  ·  долгое — меню")
        else
            tr("Скриншот: нажатие на «Аа» — перевести, ещё раз — убрать  ·  долгое нажатие — меню")
    }

    private fun updateRunning() {
        val running = OverlayService.running
        if (running == lastRunning) return
        lastRunning = running
        statusChip.set(running, if (running) tr("Работает — кнопка «Аа» на экране") else tr("Переводчик выключен"))
        if (running) startBtn.set(tr("Остановить переводчик"), R.drawable.ic_stop, PillStyle.HERO_OUTLINE)
        else startBtn.set(tr("Запустить переводчик"), R.drawable.ic_play, PillStyle.HERO)
    }

    // ---------------------------------------------------------------- быстрые действия
    private fun buildTiles(): View {
        val row = ui.horizontal().apply { gravity = Gravity.TOP }
        val tiles = listOf(
            Tile(this, tr("Перевести\nскриншот"), R.drawable.ic_image) { pickImage() },
            Tile(this, tr("Новый\nтайтл"), R.drawable.ic_refresh) { createTitle() },
            Tile(this, tr("Как\nпользоваться"), R.drawable.ic_help) { showHelp() },
        )
        tiles.forEachIndexed { i, t ->
            row.addView(t, lp(0, weight = 1f).margins(this, start = if (i == 0) 0 else 10))
        }
        return row
    }

    // ---------------------------------------------------------------- языки
    private fun buildLanguages(): View {
        val card = ui.card()
        card.addView(ui.cardHeader(tr("Языки"), R.drawable.ic_lang, tr("Откуда и куда переводить")))
        val row = ui.horizontal()
        srcPicker = PickerView(this, tr("С ЯЗЫКА")) { pickSource() }
        tgtPicker = PickerView(this, tr("НА ЯЗЫК")) { pickTarget() }
        row.addView(srcPicker, lp(0, weight = 1f))
        row.addView(ui.icon(R.drawable.ic_arrow, T.c.primary, 20), LinearLayout.LayoutParams(dp(20), dp(20))
            .margins(this, 8, 0, 8, 0))
        row.addView(tgtPicker, lp(0, weight = 1f))
        card.addView(row, lp().margins(this, top = 14))
        val content = Dropdown(this, tr("ЧТО ПЕРЕВОДИМ"), listOf(
            Dropdown.Item("manhwa", tr("Манхва, манга, маньхуа"), tr("Облака с репликами, звуки, плашки"), ""),
            Dropdown.Item("screen", tr("Текст на экране"), tr("Статьи, чаты, игры, приложения — перевод прямо поверх текста"), ""),
        )) { s.contentMode = it }
        content.select(s.contentMode)
        card.addView(content, lp().margins(this, top = 12))
        renderLanguages()
        return card
    }

    private fun renderLanguages() {
        srcPicker.setValue(Languages.src(s.sourceLang).name.substringBefore(" ("))
        tgtPicker.setValue(Languages.tgt(s.targetLang).name)
    }

    private fun pickSource() {
        val items = Languages.sources.map { it.name }.toTypedArray()
        val cur = Languages.sources.indexOfFirst { it.code == s.sourceLang }
        AlertDialog.Builder(this).setTitle(tr("Язык оригинала"))
            .setSingleChoiceItems(items, cur) { d, which ->
                s.sourceLang = Languages.sources[which].code
                Ocr.requestModules(this, Languages.src(s.sourceLang))
                GlossaryStore.switchUniversal(this, s)
                refreshGlossary()
                renderLanguages()
                d.dismiss()
            }.show()
    }

    private fun pickTarget() {
        val items = Languages.targets.map { it.name }.toTypedArray()
        val cur = Languages.targets.indexOfFirst { it.code == s.targetLang }
        AlertDialog.Builder(this).setTitle(tr("Переводить на"))
            .setSingleChoiceItems(items, cur) { d, which ->
                s.targetLang = Languages.targets[which].code
                renderLanguages()
                d.dismiss()
            }.show()
    }

    // ---------------------------------------------------------------- переводчик
    private fun buildTranslator(): View {
        val card = ui.card()
        card.addView(ui.cardHeader(tr("Переводчик"), R.drawable.ic_bolt, tr("Чем переводить текст облаков")))
        translatorDropdown = Dropdown(this, tr("ПЕРЕВОДЧИК"),
            Translators.all.map { Dropdown.Item(it.key, it.title, it.subtitle, it.group) }) { selectTranslator(it) }
        card.addView(translatorDropdown, lp().margins(this, top = 14))

        keysBox = ui.vertical()
        card.addView(keysBox, lp().margins(this, top = 4))

        card.addView(View(this).apply { setBackgroundColor(T.c.border) }, lp(h = dp(1)).margins(this, top = 16))
        card.addView(Stepper(this, tr("Контекст"), tr("Сколько прошлых реплик учитывать"), s.contextSize.toFloat(),
            1f, 0f, 40f, { it.toInt().toString() }) { s.contextSize = it.toInt() }, lp().margins(this, top = 12))
        card.addView(Stepper(this, tr("Пауза между запросами"), tr("Защищает от блокировки за частые запросы"),
            s.requestIntervalSec, 0.5f, 0f, 10f, { String.format(tr("%.1f с"), it) }) { s.requestIntervalSec = it },
            lp().margins(this, top = 12))
        selectTranslator(s.translator)
        return card
    }

    private fun selectTranslator(key: String) {
        s.translator = key
        translatorDropdown.select(key)
        keysBox.removeAllViews()
        fun field(label: String, value: String, hint: String, secret: Boolean = false, save: (String) -> Unit) {
            keysBox.addView(Field(this, label, value, hint, secret) { save(it.trim()) }, lp().margins(this, top = 12))
        }
        fun note(text: String) {
            keysBox.addView(ui.caption(text), lp().margins(this, top = 12))
        }
        fun link(text: String, url: String) {
            val row = ui.horizontal().apply {
                isClickable = true
                background = pressable(null, dpF(10f))
                setPadding(dp(4), dp(8), dp(4), dp(8))
                addView(ui.icon(R.drawable.ic_link, T.c.primary, 18))
                addView(ui.text(text, 14f, T.c.primary, Fonts.medium), lp(LinearLayout.LayoutParams.WRAP_CONTENT)
                    .margins(this@MainActivity, start = 8))
                setOnClickListener {
                    try { startActivity(Intent(Intent.ACTION_VIEW, Uri.parse(url))) } catch (_: Exception) {}
                }
            }
            keysBox.addView(row, lp(LinearLayout.LayoutParams.WRAP_CONTENT).margins(this, top = 6))
        }
        when (key) {
            "google_free" -> note(tr("Ключ не нужен. Это неофициальный способ: при VPN Google часто ограничивает запросы — тогда выберите Lingva или Яндекс."))
            "yandex" -> {
                note(tr("Работает без ключа. Если бесплатный способ перестанет отвечать, укажите ключ Yandex Cloud — тогда перевод пойдёт через официальный API (платно, по символам)."))
                field(tr("API-ключ Yandex Cloud (необязательно)"), s.yandexKey, "AQVN…", secret = true) { s.yandexKey = it }
                field(tr("ID каталога (нужен вместе с ключом)"), s.yandexFolder, "b1g…") { s.yandexFolder = it }
                link(tr("Как получить ключ"), "https://yandex.cloud/ru/docs/translate/quickstart")
            }
            "duckduckgo" -> note(tr("Ключ не нужен. DuckDuckGo переводит через Microsoft Bing. Способ неофициальный и может временно не отвечать."))
            "lingva" -> {
                note(tr("Ключ не нужен. Lingva — открытый посредник к Google Переводчику: Google видит запрос от сервера Lingva, а не от вас, поэтому ограничения VPN обычно не мешают."))
                field(tr("Адрес сервера Lingva"), s.lingvaUrl, "https://lingva.ml") {
                    s.lingvaUrl = it.ifEmpty { "https://lingva.ml" }
                }
                link(tr("Список других серверов"), "https://github.com/thedaviddelta/lingva-translate#instances")
            }
            "papago" -> {
                note(tr("Переводчик Naver — лучший для корейского. Нужна регистрация в Naver Cloud Platform. На русский Papago переводит только с корейского и английского."))
                field("Client ID", s.papagoId, "", secret = true) { s.papagoId = it }
                field("Client Secret", s.papagoSecret, "", secret = true) { s.papagoSecret = it }
                link(tr("Открыть Naver Cloud Platform"), "https://www.ncloud.com/product/aiService/papagoTranslation")
            }
            "claude" -> {
                field(tr("API-ключ Claude"), s.claudeKey, "sk-ant-…", secret = true) { s.claudeKey = it }
                field(tr("Модель"), s.claudeModel, "claude-haiku-5-5") { s.claudeModel = it.ifEmpty { "claude-haiku-5-5" } }
                link(tr("Получить ключ"), "https://platform.claude.com")
            }
            "deepl" -> {
                field(tr("API-ключ DeepL"), s.deeplKey, "xxxxxxxx-xxxx:fx", secret = true) { s.deeplKey = it }
                link(tr("Получить бесплатный ключ"), "https://www.deepl.com/pro-api")
            }
            "microsoft" -> {
                field(tr("Ключ Azure Translator"), s.microsoftKey, tr("ключ ресурса"), secret = true) { s.microsoftKey = it }
                field(tr("Регион"), s.microsoftRegion, "global") { s.microsoftRegion = it.ifEmpty { "global" } }
                link(tr("Открыть портал Azure"), "https://portal.azure.com")
            }
            "gemini" -> {
                field(tr("API-ключ Gemini"), s.geminiKey, "AIza…", secret = true) { s.geminiKey = it }
                field(tr("Модель"), s.geminiModel, "gemini-2.5-flash-lite") {
                    s.geminiModel = it.ifEmpty { "gemini-2.5-flash-lite" }
                }
                link(tr("Получить ключ в Google AI Studio"), "https://aistudio.google.com/apikey")
            }
            "openai" -> {
                field(tr("Адрес сервера"), s.openaiUrl, "https://api.openai.com/v1") { s.openaiUrl = it }
                field(tr("API-ключ"), s.openaiKey, "sk-…", secret = true) { s.openaiKey = it }
                field(tr("Модель"), s.openaiModel, "gpt-4o-mini") { s.openaiModel = it }
            }
        }
    }

    // ---------------------------------------------------------------- тайтл и глоссарий
    private fun buildGlossary(): View {
        val card = ui.card()
        card.addView(ui.cardHeader(tr("Тайтл"), R.drawable.ic_book, tr("Контекст и глоссарий сохраняются для каждой манхвы")))
        titlePicker = PickerView(this, tr("АКТИВНЫЙ ТАЙТЛ")) { pickTitle() }
        card.addView(titlePicker, lp().margins(this, top = 14))
        val trow = ui.horizontal()
        val manage = PillButton(this, tr("Управление"), null, PillStyle.OUTLINE, 44)
        manage.onClick { titleActions() }
        val newTitle = PillButton(this, tr("Новый"), R.drawable.ic_plus, PillStyle.TONAL, 44)
        newTitle.onClick { createTitle() }
        trow.addView(manage, lp(0, weight = 1f))
        trow.addView(newTitle, lp(0, weight = 1f).margins(this, start = 10))
        card.addView(trow, lp().margins(this, top = 10))

        glossPicker = PickerView(this, tr("ГЛОССАРИЙ")) { pickGlossary() }
        card.addView(glossPicker, lp().margins(this, top = 16))
        val row = ui.horizontal()
        val edit = PillButton(this, tr("Изменить"), null, PillStyle.OUTLINE, 44)
        edit.onClick { openGlossary(s.glossary.ifBlank { null }) }
        val create = PillButton(this, tr("Новый"), R.drawable.ic_plus, PillStyle.TONAL, 44)
        create.onClick { openGlossary(null) }
        row.addView(edit, lp(0, weight = 1f))
        row.addView(create, lp(0, weight = 1f).margins(this, start = 10))
        card.addView(row, lp().margins(this, top = 10))
        return card
    }

    private fun refreshGlossary() {
        // глоссарий активного тайтла
        TitleStore.load(this, s.activeTitle)?.let { t ->
            if (t.glossary != s.glossary) s.glossary = t.glossary
            titlePicker.setValue(trf("%s · %s реплик в памяти", t.name, t.history.size))
        } ?: run {
            if (s.activeTitle.isNotBlank()) s.activeTitle = ""
            titlePicker.setValue(tr("Без тайтла — контекст только до выхода"))
        }
        val g = s.glossary.takeIf { it.isNotBlank() }?.let { GlossaryStore.load(this, it) }
        if (g == null && s.glossary.isNotBlank()) s.glossary = ""
        glossPicker.setValue(g?.let { trf("%s · %s зап.", it.name.ifBlank { s.glossary }, it.entries.size) } ?: tr("Без глоссария"))
    }

    private fun pickGlossary() {
        val files = GlossaryStore.list(this)
        val names = listOf(tr("Без глоссария")) + files.map { GlossaryStore.load(this, it)?.name?.ifBlank { null } ?: it }
        val cur = files.indexOf(s.glossary) + 1
        AlertDialog.Builder(this).setTitle(tr("Глоссарий"))
            .setSingleChoiceItems(names.toTypedArray(), cur) { d, which ->
                s.glossary = if (which == 0) "" else files[which - 1]
                // глоссарий запоминается за активным тайтлом
                TitleStore.load(this, s.activeTitle)?.let { t -> t.glossary = s.glossary; TitleStore.save(this, t) }
                refreshGlossary()
                d.dismiss()
            }.show()
    }

    private fun pickTitle() {
        val titles = TitleStore.list(this)
        val names = listOf(tr("Без тайтла")) + titles.map { trf("%s · %s реплик", it.name, it.lines) }
        val cur = titles.indexOfFirst { it.id == s.activeTitle } + 1
        AlertDialog.Builder(this).setTitle(tr("Тайтл"))
            .setSingleChoiceItems(names.toTypedArray(), cur) { d, which ->
                selectTitle(if (which == 0) "" else titles[which - 1].id)
                d.dismiss()
            }
            .setPositiveButton(tr("Новый")) { _, _ -> createTitle() }
            .show()
    }

    private fun selectTitle(id: String) {
        s.activeTitle = id
        TitleStore.load(this, id)?.let { s.glossary = it.glossary }
        notifyContextChanged()
        refreshGlossary()
    }

    private fun askName(title: String, initial: String, onOk: (String) -> Unit) {
        val f = Field(this, "", initial, tr("Например: мой любимый тайтл"))
        val box = FrameLayout(this).apply { setPadding(dp(20), dp(8), dp(20), 0); addView(f) }
        AlertDialog.Builder(this).setTitle(title).setView(box)
            .setPositiveButton(tr("Сохранить")) { _, _ ->
                val name = f.edit.text.toString().trim()
                if (name.isEmpty()) toast(tr("Введите название тайтла")) else onOk(name)
            }
            .setNegativeButton(tr("Отмена"), null)
            .show()
    }

    private fun createTitle() {
        askName(tr("Новый тайтл"), "") { name ->
            val glossary = GlossaryStore.universalFor(this, s.sourceLang) ?: ""
            val t = TitleStore.create(this, name, glossary)
            selectTitle(t.id)
            toast(trf("Тайтл «%s» создан. Контекст будет сохраняться в памяти телефона", name))
        }
    }

    private fun titleActions() {
        val t = TitleStore.load(this, s.activeTitle)
        if (t == null) {
            AlertDialog.Builder(this).setTitle(tr("Тайтл"))
                .setMessage(tr("Сейчас тайтл не выбран: контекст хранится только до выхода из переводчика. Создайте тайтл, чтобы контекст и глоссарий сохранялись для каждой манхвы."))
                .setPositiveButton(tr("Новый")) { _, _ -> createTitle() }
                .setNeutralButton(tr("Очистить контекст")) { _, _ -> resetSession() }
                .setNegativeButton(tr("Отмена"), null)
                .show()
            return
        }
        val actions = arrayOf(tr("Переименовать"), tr("Очистить контекст"), tr("Удалить тайтл"))
        AlertDialog.Builder(this).setTitle(t.name)
            .setItems(actions) { _, which ->
                when (which) {
                    0 -> askName(tr("Переименовать"), t.name) { name ->
                        t.name = name
                        TitleStore.save(this, t)
                        refreshGlossary()
                    }
                    1 -> {
                        TitleStore.clearContext(this, t.id)
                        notifyContextChanged()
                        refreshGlossary()
                        toast(tr("Контекст тайтла очищен"))
                    }
                    2 -> AlertDialog.Builder(this).setTitle(tr("Удалить тайтл?"))
                        .setMessage(tr("Сохранённые реплики и кэш переводов этого тайтла будут удалены. Глоссарий останется."))
                        .setPositiveButton(tr("Удалить")) { _, _ ->
                            TitleStore.delete(this, t.id)
                            selectTitle("")
                        }
                        .setNegativeButton(tr("Отмена"), null)
                        .show()
                }
            }.show()
    }

    /** Сообщить переводчику, что контекст поменялся (он перечитает его из памяти). */
    private fun notifyContextChanged() {
        testPipeline?.reset()
        if (OverlayService.running) {
            startService(Intent(this, OverlayService::class.java).setAction(OverlayService.ACTION_RESET))
        }
    }

    private fun openGlossary(file: String?) {
        val i = Intent(this, GlossaryActivity::class.java)
        if (file != null) i.putExtra(GlossaryActivity.EXTRA_FILE, file)
        startActivity(i)
    }

    // ---------------------------------------------------------------- тонкая настройка
    private fun buildTuning(): View {
        val card = ui.card()
        val header = ui.horizontal().apply { isClickable = true }
        header.addView(ui.cardHeader(tr("Тонкая настройка"), R.drawable.ic_tune, tr("Распознавание и вид перевода")),
            lp(0, weight = 1f))
        val chevron = ui.icon(R.drawable.ic_chevron, T.c.primary, 22)
        header.addView(chevron)
        card.addView(header)

        val body = ui.vertical().apply { visibility = View.GONE }
        val uiLang = Dropdown(this, tr("ЯЗЫК ИНТЕРФЕЙСА"), listOf(
            Dropdown.Item("system", tr("Как в системе"), tr("Язык телефона"), ""),
            Dropdown.Item("ru", "Русский", "Russian", ""),
            Dropdown.Item("en", "English", "Английский", ""),
        )) { lang ->
            if (lang != s.uiLang) {
                s.uiLang = lang
                I18n.init(this)
                recreate()
            }
        }
        uiLang.select(s.uiLang)
        body.addView(uiLang, lp().margins(this, top = 16))
        val modes = Dropdown(this, tr("РАСПОЗНАВАНИЕ ТЕКСТА"), Enhance.modes.map {
            Dropdown.Item(it.first.toString(), it.second, it.third, "")
        }) { s.ocrMode = it.toInt() }
        modes.select(s.ocrMode.toString())
        body.addView(modes, lp().margins(this, top = 16))
        body.addView(ui.caption(tr("Если текст на тёмном или пёстром фоне не находится, выберите режим потщательнее. Каждый проход добавляет примерно 0,2–0,4 с.")), lp().margins(this, top = 6))
        body.addView(Stepper(this, tr("Склейка строк"), tr("Больше — охотнее собирает строки в одно облако"),
            s.bubbleGap, 0.1f, 0.2f, 4f, { String.format("%.1f", it) }) { s.bubbleGap = it },
            lp().margins(this, top = 16))
        body.addView(Stepper(this, tr("Шрифт перевода"), tr("Максимальный размер"), s.maxFontSp.toFloat(),
            1f, 8f, 40f, { "${it.toInt()} sp" }) { s.maxFontSp = it.toInt() }, lp().margins(this, top = 14))
        body.addView(SwitchRow(this, tr("Не переводить звуки"), tr("Текст поверх рисунка, а не в облаке"), s.skipFreeText) {
            s.skipFreeText = it
        }, lp().margins(this, top = 10))
        body.addView(SwitchRow(this, tr("Выделять по всей ширине"), tr("Проводите только сверху вниз — ширина берётся вся"),
            s.fullWidthSelect) { s.fullWidthSelect = it }, lp().margins(this, top = 2))
        body.addView(SwitchRow(this, tr("Пропускать обрезанное"), tr("Текст, который режет край области"), s.skipCut) {
            s.skipCut = it
        }, lp().margins(this, top = 2))
        body.addView(SwitchRow(this, tr("Повторять наклон текста"), tr("Перевод под тем же углом, что и оригинал"),
            s.keepTilt) { s.keepTilt = it }, lp().margins(this, top = 2))
        card.addView(body)

        header.setOnClickListener {
            val open = body.visibility != View.VISIBLE
            body.visibility = if (open) View.VISIBLE else View.GONE
            chevron.animate().rotation(if (open) 180f else 0f).setDuration(180).start()
        }
        return card
    }

    // ================================================================ для разработчиков
    private fun buildDeveloper(): View {
        val card = ui.card()
        val header = ui.horizontal().apply { isClickable = true }
        header.addView(ui.cardHeader(tr("Для разработчиков"), R.drawable.ic_code,
            tr("Отладочная информация и проверка скорости")), lp(0, weight = 1f))
        val chevron = ui.icon(R.drawable.ic_chevron, T.c.primary, 22)
        header.addView(chevron)
        card.addView(header)

        val body = ui.vertical().apply { visibility = View.GONE }
        val extra = ui.vertical()
        body.addView(SwitchRow(this, tr("Режим разработчика"),
            tr("Время каждого этапа, проходы распознавания, рамки строк и журнал"), s.devMode) {
            s.devMode = it
            extra.visibility = if (it) View.VISIBLE else View.GONE
        }, lp().margins(this, top = 12))
        body.addView(SwitchRow(this, tr("Параллельное распознавание"),
            tr("Проходы распознавания идут одновременно — быстрее на многоядерных телефонах"), s.parallelOcr) {
            s.parallelOcr = it
        }, lp().margins(this, top = 2))

        extra.visibility = if (s.devMode) View.VISIBLE else View.GONE
        extra.addView(SwitchRow(this, tr("Рамки распознанных строк"),
            tr("Поверх страницы, своим цветом для каждого прохода"), s.debugBoxes) {
            s.debugBoxes = it
        }, lp().margins(this, top = 2))
        extra.addView(ui.caption(tr("После перевода внизу экрана появится панель с подробностями. Сравнить скорость «по очереди» и «параллельно» можно в меню плавающей кнопки (долгое нажатие).")),
            lp().margins(this, top = 8))
        val row = ui.horizontal()
        val copyBtn = PillButton(this, tr("Копировать журнал"), R.drawable.ic_link, PillStyle.TONAL, 44)
        copyBtn.onClick {
            val log = DevLog.read(this)
            if (log.isBlank()) {
                toast(tr("Журнал пуст: включите режим разработчика и выполните перевод"))
            } else {
                getSystemService(android.content.ClipboardManager::class.java)
                    ?.setPrimaryClip(android.content.ClipData.newPlainText("ATlate log", log))
                toast(tr("Журнал скопирован"))
            }
        }
        row.addView(copyBtn, lp(0, weight = 1f))
        val clearBtn = PillButton(this, tr("Очистить"), R.drawable.ic_refresh, PillStyle.TONAL, 44)
        clearBtn.onClick {
            DevLog.clear(this)
            toast(tr("Журнал очищен"))
        }
        row.addView(clearBtn, lp(0, weight = 1f).margins(this, start = 8))
        extra.addView(row, lp().margins(this, top = 12))
        body.addView(extra)
        card.addView(body)

        header.setOnClickListener {
            val open = body.visibility != View.VISIBLE
            body.visibility = if (open) View.VISIBLE else View.GONE
            chevron.animate().rotation(if (open) 180f else 0f).setDuration(180).start()
        }
        return card
    }

    // ================================================================ запуск плавающей кнопки
    private fun onStartClicked() {
        if (OverlayService.running) {
            startService(Intent(this, OverlayService::class.java).setAction(OverlayService.ACTION_STOP))
            main.postDelayed({ updateRunning() }, 300)
            return
        }
        if (!android.provider.Settings.canDrawOverlays(this)) {
            AlertDialog.Builder(this)
                .setTitle(tr("Нужно разрешение"))
                .setMessage(tr("Чтобы кнопка и перевод появлялись поверх читалки, разрешите приложению «Показ поверх других приложений». Затем вернитесь сюда и снова нажмите «Запустить»."))
                .setPositiveButton(tr("Открыть настройки")) { _, _ ->
                    startActivity(Intent(android.provider.Settings.ACTION_MANAGE_OVERLAY_PERMISSION,
                        Uri.parse("package:$packageName")))
                }
                .setNegativeButton(tr("Отмена"), null)
                .show()
            return
        }
        if (Build.VERSION.SDK_INT >= 33 &&
            checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED) {
            requestPermissions(arrayOf(Manifest.permission.POST_NOTIFICATIONS), REQ_NOTIF)
            return
        }
        requestCapture()
    }

    override fun onRequestPermissionsResult(requestCode: Int, permissions: Array<out String>, grantResults: IntArray) {
        super.onRequestPermissionsResult(requestCode, permissions, grantResults)
        if (requestCode == REQ_NOTIF) requestCapture()
    }

    private fun requestCapture() {
        val mpm = getSystemService(MediaProjectionManager::class.java)
        val intent = if (Build.VERSION.SDK_INT >= 34) {
            // весь экран, а не одно приложение — иначе координаты области не совпадут
            mpm.createScreenCaptureIntent(MediaProjectionConfig.createConfigForDefaultDisplay())
        } else {
            mpm.createScreenCaptureIntent()
        }
        startActivityForResult(intent, REQ_CAPTURE)
    }

    override fun onActivityResult(requestCode: Int, resultCode: Int, data: Intent?) {
        super.onActivityResult(requestCode, resultCode, data)
        when (requestCode) {
            REQ_CAPTURE -> {
                if (resultCode != RESULT_OK || data == null) {
                    toast(tr("Без разрешения на запись экрана переводчик не сможет видеть текст"))
                    return
                }
                val i = Intent(this, OverlayService::class.java)
                    .setAction(OverlayService.ACTION_START)
                    .putExtra(OverlayService.EXTRA_CODE, resultCode)
                    .putExtra(OverlayService.EXTRA_DATA, data)
                startForegroundService(i)
                toast(tr("Готово! Откройте читалку и нажмите на кнопку «Аа»"))
                main.postDelayed({ updateRunning(); moveTaskToBack(true) }, 400)
            }
            REQ_PICK -> data?.data?.let { translateImage(it) }
        }
    }

    // ================================================================ проверка на картинке
    private fun pickImage() {
        startActivityForResult(Intent(Intent.ACTION_GET_CONTENT).setType("image/*"), REQ_PICK)
    }

    private fun handleShare(intent: Intent?) {
        if (intent?.action != Intent.ACTION_SEND || intent.type?.startsWith("image/") != true) return
        val uri: Uri? = if (Build.VERSION.SDK_INT >= 33) {
            intent.getParcelableExtra(Intent.EXTRA_STREAM, Uri::class.java)
        } else {
            @Suppress("DEPRECATION") intent.getParcelableExtra(Intent.EXTRA_STREAM)
        }
        if (uri != null) translateImage(uri)
    }

    private var shot: Bitmap? = null

    private fun translateImage(uri: Uri) {
        shot = null
        val progress = Dialogs.progress(this, tr("Распознаю и перевожу…"))
        thread {
            val res: PageResult = try {
                val bmp = contentResolver.openInputStream(uri)?.use { BitmapFactory.decodeStream(it) }
                if (bmp == null) {
                    PageResult(error = tr("Не удалось открыть картинку"))
                } else {
                    val soft = if (bmp.config == Bitmap.Config.ARGB_8888) bmp else bmp.copy(Bitmap.Config.ARGB_8888, false)
                    val p = testPipeline ?: Pipeline(this).also { testPipeline = it }
                    val img = cropStatusBar(soft)
                    shot = img
                    p.process(img, s)
                }
            } catch (e: Exception) {
                PageResult(error = trf("Ошибка: %s", e.message))
            }
            main.post {
                progress.dismiss()
                Dialogs.result(this, res, shot)
            }
        }
    }

    /** Если это скриншот всего экрана — отрезаем строку состояния (часы, значки, заряд). */
    private fun cropStatusBar(bmp: Bitmap): Bitmap {
        val screenW = resources.displayMetrics.widthPixels
        val looksLikeScreenshot = kotlin.math.abs(bmp.width - screenW) <= 4 && bmp.height > bmp.width
        if (!looksLikeScreenshot) return bmp
        val id = resources.getIdentifier("status_bar_height", "dimen", "android")
        val barH = if (id > 0) resources.getDimensionPixelSize(id) else dp(28)
        if (barH <= 0 || barH >= bmp.height / 4) return bmp
        return Bitmap.createBitmap(bmp, 0, barH, bmp.width, bmp.height - barH)
    }

    private fun resetSession() {
        if (s.activeTitle.isNotBlank()) TitleStore.clearContext(this, s.activeTitle)
        notifyContextChanged()
        refreshGlossary()
        toast(tr("Контекст и кэш очищены — можно читать новый тайтл"))
    }

    private fun showHelp() = Dialogs.help(this)

    private fun toast(msg: String) = Toast.makeText(this, msg, Toast.LENGTH_LONG).show()
}
