package com.manhwatranslator.core

/** Языки оригинала и перевода. Коды совпадают с ПК-версией. */
data class SourceLang(
    val code: String,
    val name: String,
    val script: String,      // какой распознаватель ML Kit нужен: latin | korean | japanese | chinese
    val google: String,
    val deepl: String,
    val microsoft: String,
    val llm: String,
    val joiner: String,      // чем склеивать строки внутри облака
    val rtl: Boolean,        // облака в одном ряду читаются справа налево (манга)
) {
    /** Пишется латиницей: читается той же моделью, что и английский. */
    val isLatin: Boolean get() = script == "latin"
}

data class TargetLang(
    val code: String,
    val name: String,
    val google: String,
    val deepl: String,
    val microsoft: String,
    val llm: String,
)

object Languages {
    val sources get() = listOf(
        SourceLang("ko", tr("Корейский (манхва)"), "korean", "ko", "KO", "ko", "Korean", " ", false),
        SourceLang("ja", tr("Японский (манга)"), "japanese", "ja", "JA", "ja", "Japanese", "", true),
        SourceLang("zh", tr("Китайский упрощённый (маньхуа)"), "chinese", "zh-CN", "ZH", "zh-Hans", "Simplified Chinese", "", false),
        SourceLang("zh-tw", tr("Китайский традиционный"), "chinese", "zh-TW", "ZH", "zh-Hant", "Traditional Chinese", "", false),
        SourceLang("en", tr("Английский (сканлейты)"), "latin", "en", "EN", "en", "English", " ", false),
        // Языки на латинице: распознаются той же моделью ML Kit, что и английский — ничего докачивать не нужно
        SourceLang("de", tr("Немецкий"), "latin", "de", "DE", "de", "German", " ", false),
        SourceLang("es", tr("Испанский"), "latin", "es", "ES", "es", "Spanish", " ", false),
        SourceLang("pt", tr("Португальский"), "latin", "pt", "PT", "pt", "Portuguese", " ", false),
        SourceLang("id", tr("Индонезийский"), "latin", "id", "ID", "id", "Indonesian", " ", false),
        // DeepL филиппинский не знает (пустой код — переводчик этот язык не поддерживает)
        SourceLang("fil", tr("Филиппинский"), "latin", "tl", "", "fil", "Filipino (Tagalog)", " ", false),
        // Любой другой текст на латинице: язык определяет сам переводчик
        SourceLang(LATIN, tr("Латиница (язык определит переводчик)"), "latin", AUTO, AUTO, AUTO,
            "the original language (detect it from the text)", " ", false),
    )

    /** Код «пусть переводчик определит язык сам». */
    const val AUTO = "auto"
    /** Язык «латиница — язык определит переводчик». */
    const val LATIN = "latin"

    /** Языки перевода. Пустой код — этот переводчик язык не поддерживает. */
    val targets get() = listOf(
        TargetLang("ru", tr("Русский"), "ru", "RU", "ru", "Russian"),
        TargetLang("uk", tr("Украинский"), "uk", "UK", "uk", "Ukrainian"),
        TargetLang("be", tr("Белорусский"), "be", "", "", "Belarusian"),
        TargetLang("kk", tr("Казахский"), "kk", "", "kk", "Kazakh"),
        TargetLang("uz", tr("Узбекский"), "uz", "", "uz", "Uzbek"),
        TargetLang("en", tr("Английский"), "en", "EN-US", "en", "English"),
        TargetLang("de", tr("Немецкий"), "de", "DE", "de", "German"),
        TargetLang("fr", tr("Французский"), "fr", "FR", "fr", "French"),
        TargetLang("es", tr("Испанский"), "es", "ES", "es", "Spanish"),
        TargetLang("pt", tr("Португальский (Бразилия)"), "pt", "PT-BR", "pt", "Brazilian Portuguese"),
        TargetLang("it", tr("Итальянский"), "it", "IT", "it", "Italian"),
        TargetLang("pl", tr("Польский"), "pl", "PL", "pl", "Polish"),
        TargetLang("cs", tr("Чешский"), "cs", "CS", "cs", "Czech"),
        TargetLang("tr", tr("Турецкий"), "tr", "TR", "tr", "Turkish"),
        TargetLang("id", tr("Индонезийский"), "id", "ID", "id", "Indonesian"),
        TargetLang("vi", tr("Вьетнамский"), "vi", "", "vi", "Vietnamese"),
        TargetLang("th", tr("Тайский"), "th", "", "th", "Thai"),
        TargetLang("ar", tr("Арабский"), "ar", "AR", "ar", "Arabic"),
    )

    fun src(code: String): SourceLang = sources.firstOrNull { it.code == code } ?: sources[0]

    private val hangul = Regex("[\\uAC00-\\uD7AF\\u1100-\\u11FF\\u3130-\\u318F]")
    private val kana = Regex("[\\u3040-\\u30FF\\u31F0-\\u31FF\\uFF66-\\uFF9F]")
    private val han = Regex("[\\u3400-\\u4DBF\\u4E00-\\u9FFF]")
    private val latin = Regex("[A-Za-z\\u00C0-\\u024F]")

    /**
     * Язык конкретного облака по символам. Если в облаке только латиница, оно переводится
     * как английское, даже когда в настройках выбран корейский (страница со сканлейтом,
     * вывески, вставки на английском). Иначе — язык из настроек.
     */
    fun detect(text: String, selected: SourceLang): SourceLang {
        val h = hangul.findAll(text).count()
        val k = kana.findAll(text).count()
        val c = han.findAll(text).count()
        val l = latin.findAll(text).count()
        return when {
            h > 0 && h >= k -> src("ko")
            k > 0 -> src("ja")
            c > 0 -> if (selected.code == "ja" || selected.code.startsWith("zh")) selected else src("zh")
            // латиница: выбранный язык, если он сам на латинице; иначе язык определит переводчик —
            // по буквам немецкий от испанского не отличить
            l >= 2 -> if (selected.isLatin) selected else src(LATIN)
            else -> selected
        }
    }

    /** Название языка по коду — нашему или ISO (его возвращают переводчики, когда определяют язык сами). */
    fun nameOf(code: String): String {
        val low = code.trim().lowercase()
        val base = low.replace('_', '-').substringBefore('-').let { if (it == "tl") "fil" else it }
        for (k in listOf(low, base)) {
            sources.firstOrNull { it.code == k }?.let { return it.name.substringBefore(" (") }
            targets.firstOrNull { it.code == k }?.let { return it.name.substringBefore(" (") }
        }
        return code
    }
    fun tgt(code: String): TargetLang = targets.firstOrNull { it.code == code } ?: targets[0]
}
