package com.manhwatranslator.core

/**
 * Автоопределение языка оригинала — те же правила, что в ПК-версии (autolang.py).
 *
 * Распознавателю нужно заранее сказать письменность, поэтому «узнать язык по тексту» нельзя:
 * текста ещё нет. Вместо этого область читают все распознаватели сразу, и побеждает язык,
 * чей распознаватель нашёл больше букв своей письменности и меньше постороннего мусора.
 */
object AutoLang {
    private val hangul = Regex("[\\uAC00-\\uD7AF\\u1100-\\u11FF\\u3130-\\u318F]")
    private val hiragana = Regex("[\\u3040-\\u309F]")
    private val kana = Regex("[\\u3040-\\u30FF\\u31F0-\\u31FF\\uFF66-\\uFF9F]")
    private val han = Regex("[\\u3400-\\u4DBF\\u4E00-\\u9FFF]")
    private val latin = Regex("[A-Za-z\\u00C0-\\u024F]")
    private val letter = Regex("\\p{L}")
    private val word = Regex("[A-Za-z\\u00C0-\\u024F]+")
    private val vowel = Regex("[aeiouyAEIOUY\\u00C0-\\u00FF]")

    // Иероглифы, которые пишутся по-разному в упрощённом и традиционном китайском (частые)
    private const val SIMPLIFIED =
        "们这说时会对发开关东车门问间长见现学习书话语读写听买卖钱饭个为国来过还进远边动体点热爱亲乐万与么从儿兴军" +
        "办务区医华单变号吗员团图场坏声处头妈孙实宝导将岁师带帮应张强当总战报数无旧机杀权条样气灭灵爷独电画确离" +
        "种笔节类红级线练组经结给统继续网罗联胜脑艺药获虽观规视觉计认让记讲许论设证试该请谁调谈谢负责败货质贵" +
        "费赶转轻输达运选钟铁错镇闻队阳阴难顺须顾领题风飞马鱼鸟龙齐"
    private const val TRADITIONAL =
        "們這說時會對發開關東車門問間長見現學習書話語讀寫聽買賣錢飯個為國來過還進遠邊動體點熱愛親樂萬與麼從兒興軍" +
        "辦務區醫華單變號嗎員團圖場壞聲處頭媽孫實寶導將歲師帶幫應張強當總戰報數無舊機殺權條樣氣滅靈爺獨電畫確離" +
        "種筆節類紅級線練組經結給統繼續網羅聯勝腦藝藥獲雖觀規視覺計認讓記講許論設證試該請誰調談謝負責敗貨質貴" +
        "費趕轉輕輸達運選鐘鐵錯鎮聞隊陽陰難順須顧領題風飛馬魚鳥龍齊"
    private val simpSet = SIMPLIFIED.toSet()
    private val tradSet = TRADITIONAL.toSet()

    /** Меньше букв — судить не о чем («!?», одно короткое слово). */
    private const val MIN_LETTERS = 4

    /** Коды языков в том порядке, в каком они проверяются. */
    val codes = listOf("ko", "ja", "zh", "zh-tw", "en")

    private fun isChinese(code: String) = code == "zh" || code == "zh-tw"
    private fun count(rx: Regex, text: String) = rx.findAll(text).count()

    /**
     * Буквы латиницы в «настоящих» словах: от трёх букв и с гласной. Распознаватель латиницы
     * на корейском или японском тексте выдаёт обрывки вроде «OJ Ll I7» — они не в счёт.
     */
    private fun wordlike(text: String): Int =
        word.findAll(text).map { it.value }.filter { it.length >= 3 && vowel.containsMatchIn(it) }.sumOf { it.length }

    private fun ownLetters(text: String, code: String): Int = when {
        code == "ko" -> count(hangul, text)
        code == "ja" -> count(kana, text) + count(han, text)
        isChinese(code) -> count(han, text)
        else -> wordlike(text)
    }

    /** Насколько текст похож на правильно распознанный текст этого языка. */
    fun score(text: String, code: String): Float {
        val own = ownLetters(text, code)
        if (own == 0) return 0f
        var total = count(letter, text)
        val hira = count(hiragana, text)
        // японский текст почти всегда содержит хирагану; катакана не в счёт — её легко спутать
        // с иероглифами (ロ и 口, カ и 力), и «японский» находился бы в китайском тексте
        if (code == "ja" && (hira < 2 || hira < 0.15f * own)) return 0f
        if (isChinese(code) && hira >= 2) return 0f
        if (code == "en") total = maxOf(total, count(latin, text))
        return own.toFloat() * own / maxOf(1, total)
    }

    /** Упрощённый или традиционный — по иероглифам, которые в них пишутся по-разному. */
    private fun chineseVariant(text: String, current: String): String {
        val simp = text.count { it in simpSet }
        val trad = text.count { it in tradSet }
        return when {
            simp > trad -> "zh"
            trad > simp -> "zh-tw"
            isChinese(current) -> current
            else -> "zh"
        }
    }

    /**
     * Выбрать язык по пробному распознаванию. texts — что прочитал распознаватель для каждого
     * языка (код → весь текст). Возвращает код языка или null, если уверенно сказать нельзя.
     */
    fun choose(texts: Map<String, String>, current: String): String? {
        val scores = texts.mapValues { (code, text) -> score(text, code) }
        val en = scores["en"] ?: 0f
        val best = scores.filterKeys { it != "en" }.maxByOrNull { it.value }
        if (best != null && best.value >= MIN_LETTERS && best.value >= 0.3f * en) {
            return if (isChinese(best.key)) chineseVariant(texts[best.key].orEmpty(), current) else best.key
        }
        return if (en >= MIN_LETTERS) "en" else null
    }

    /** Оценки по языкам одной строкой — для режима разработчика. */
    fun describe(texts: Map<String, String>): String =
        texts.entries.joinToString(", ") { (code, text) -> "$code ${"%.0f".format(score(text, code))}" }

    const val GOOD = 0
    const val BAD = 1
    const val EMPTY = 2

    /**
     * Подходит ли распознанный текст выбранному языку. EMPTY — букв слишком мало, чтобы судить;
     * BAD — буквы есть, но не той письменности: похоже, язык на экране другой.
     */
    fun fit(text: String, code: String): Int {
        val total = count(letter, text)
        if (total < MIN_LETTERS) return EMPTY
        val own = ownLetters(text, code)
        val hira = count(hiragana, text)
        if (code == "ja" && own >= 8 && hira == 0) return BAD      // одни иероглифы — скорее китайский
        if (isChinese(code) && hira >= 2) return BAD
        return if (own >= MIN_LETTERS && own >= 0.5f * total) GOOD else BAD
    }
}
