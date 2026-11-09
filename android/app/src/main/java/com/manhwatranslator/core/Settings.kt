package com.manhwatranslator.core

import android.content.Context
import android.content.SharedPreferences
import android.graphics.Rect

/** Настройки приложения (SharedPreferences). */
class Settings(ctx: Context) {
    private val p: SharedPreferences = ctx.getSharedPreferences("settings", Context.MODE_PRIVATE)

    var sourceLang by str("source_lang", "ko")
    var targetLang by str("target_lang", "ru")
    /** Язык интерфейса: system | ru | en */
    var uiLang by str("ui_lang", "system")
    var translator by str("translator", "google_free")
    var contextSize by int("context_size", 12)
    /** Минимальная пауза между запросами к сервису перевода, секунды */
    var requestIntervalSec by float("request_interval", 1f)
    var glossary by str("glossary", "")
    /** Активный тайтл (id) — его контекст сохраняется в памяти телефона. Пусто — без тайтла. */
    var activeTitle by str("active_title", "")
    /** manhwa — облака манхвы; screen — обычный текст на экране (статьи, игры, приложения) */
    var contentMode by str("content_mode", "manhwa")

    var deeplKey by str("deepl_key", "")
    var microsoftKey by str("ms_key", "")
    var microsoftRegion by str("ms_region", "global")
    var geminiKey by str("gemini_key", "")
    var geminiModel by str("gemini_model", "gemini-2.5-flash-lite")
    var openaiUrl by str("openai_url", "https://api.openai.com/v1")
    var openaiKey by str("openai_key", "")
    var openaiModel by str("openai_model", "gpt-4o-mini")
    var yandexKey by str("yandex_key", "")
    var yandexFolder by str("yandex_folder", "")
    var lingvaUrl by str("lingva_url", "https://lingva.ml")
    var papagoId by str("papago_id", "")
    var papagoSecret by str("papago_secret", "")
    var claudeKey by str("claude_key", "")
    var claudeModel by str("claude_model", "claude-haiku-5-5")

    var bubbleGap by float("bubble_gap", 1.1f)
    /** Сколько проходов распознавания: 1 — быстрый, 2 — тщательный, 3 — максимальный */
    var ocrMode by int("ocr_mode", Enhance.THOROUGH)
    var skipFreeText by bool("skip_free", false)
    var skipCut by bool("skip_cut", true)
    /** Перевод под тем же углом, что и наклонённый оригинал */
    var keepTilt by bool("keep_tilt", true)
    var maxFontSp by int("max_font_sp", 16)
    /** Выделять зону по всей ширине экрана (задаётся только высота) */
    var fullWidthSelect by bool("full_width_select", false)
    /** shot — перевод по нажатию; live — автоматически, когда страница перестаёт двигаться */
    var translateMode by str("translate_mode", "shot")

    /** Режим разработчика: время этапов, проходы распознавания, журнал */
    var devMode by bool("dev_mode", false)
    /** Проходы распознавания идут одновременно */
    var parallelOcr by bool("parallel_ocr", true)
    /** В режиме разработчика рисовать рамки распознанных строк */
    var debugBoxes by bool("debug_boxes", true)

    var region: Rect?
        get() {
            val s = p.getString("region", null) ?: return null
            val v = s.split(",").mapNotNull { it.toIntOrNull() }
            return if (v.size == 4) Rect(v[0], v[1], v[2], v[3]) else null
        }
        set(r) {
            p.edit().putString("region", r?.let { "${it.left},${it.top},${it.right},${it.bottom}" }).apply()
        }

    var buttonX by int("button_x", 24)
    var buttonY by int("button_y", 400)

    // ---------------------------------------------------------------- делегаты
    private fun str(key: String, def: String) = object : kotlin.properties.ReadWriteProperty<Any?, String> {
        override fun getValue(thisRef: Any?, property: kotlin.reflect.KProperty<*>) = p.getString(key, def) ?: def
        override fun setValue(thisRef: Any?, property: kotlin.reflect.KProperty<*>, value: String) {
            p.edit().putString(key, value).apply()
        }
    }

    private fun int(key: String, def: Int) = object : kotlin.properties.ReadWriteProperty<Any?, Int> {
        override fun getValue(thisRef: Any?, property: kotlin.reflect.KProperty<*>) = p.getInt(key, def)
        override fun setValue(thisRef: Any?, property: kotlin.reflect.KProperty<*>, value: Int) {
            p.edit().putInt(key, value).apply()
        }
    }

    private fun float(key: String, def: Float) = object : kotlin.properties.ReadWriteProperty<Any?, Float> {
        override fun getValue(thisRef: Any?, property: kotlin.reflect.KProperty<*>) = p.getFloat(key, def)
        override fun setValue(thisRef: Any?, property: kotlin.reflect.KProperty<*>, value: Float) {
            p.edit().putFloat(key, value).apply()
        }
    }

    private fun bool(key: String, def: Boolean) = object : kotlin.properties.ReadWriteProperty<Any?, Boolean> {
        override fun getValue(thisRef: Any?, property: kotlin.reflect.KProperty<*>) = p.getBoolean(key, def)
        override fun setValue(thisRef: Any?, property: kotlin.reflect.KProperty<*>, value: Boolean) {
            p.edit().putBoolean(key, value).apply()
        }
    }
}
