package com.manhwatranslator.core

import android.content.Context
import org.json.JSONArray
import org.json.JSONObject
import java.io.File
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

/**
 * Словарик: слова оригинала, отмеченные на экране, с переводом и своей заметкой.
 *
 * Слово попадает сюда из «Слов на экране»: удержали палец на слове — над ним «Копировать» и
 * «В словарик». Перевод слова — из глоссария тайтла, если оно там есть, иначе от выбранного
 * переводчика (ИИ-переводчику заодно показывается фраза, где слово встретилось). Заметку
 * (транскрипцию, род, что угодно) пишут сами в окне словарика.
 *
 * Файл files/vocabulary.json; формат тот же, что в ПК-версии.
 */
object Vocabulary {
    data class Entry(
        var word: String,
        var translation: String = "",
        var note: String = "",
        var lang: String = "",
        var tgt: String = "",
        var context: String = "",
        var contextTr: String = "",
        var title: String = "",
        var added: Long = System.currentTimeMillis() / 1000,
    ) {
        val key: String get() = lang.substringBefore('-') + "|" + word.lowercase()
    }

    private val hangul = Regex("[\\uAC00-\\uD7AF]")
    private val cased = Regex("[A-Za-z\\u00C0-\\u024F\\u0400-\\u04FF]")
    private val josa = listOf("에게서", "께서는", "이랑", "에게", "에서", "한테", "께서", "이나", "이야", "은", "는", "이", "가",
        "을", "를", "의", "에", "와", "과", "도", "만", "랑", "아", "야")

    fun file(ctx: Context) = File(ctx.filesDir, "vocabulary.json")

    private fun edge(c: Char): Boolean = when (Character.getType(c).toByte()) {
        Character.CONNECTOR_PUNCTUATION, Character.DASH_PUNCTUATION, Character.START_PUNCTUATION,
        Character.END_PUNCTUATION, Character.INITIAL_QUOTE_PUNCTUATION, Character.FINAL_QUOTE_PUNCTUATION,
        Character.OTHER_PUNCTUATION, Character.MATH_SYMBOL, Character.CURRENCY_SYMBOL,
        Character.MODIFIER_SYMBOL, Character.OTHER_SYMBOL -> true
        else -> false
    }

    /** Слово без знаков препинания и кавычек по краям: «Hunter!» → Hunter. */
    fun clean(word: String): String = word.trim().dropWhile { edge(it) }.dropLastWhile { edge(it) }

    /** Слово для словарика: без знаков по краям, без корейской частицы (학교에 → 학교), латиница и
     *  кириллица — строчными (в сканлейтах часто ВСЁ ЗАГЛАВНЫМИ). */
    fun baseForm(word: String, lang: String = ""): String {
        var w = clean(word)
        if (lang.startsWith("ko") || (w.isNotEmpty() && hangul.matches(w.last().toString()))) {
            for (j in josa) if (w.endsWith(j) && w.length - j.length >= 2) { w = w.dropLast(j.length); break }
        }
        if (cased.containsMatchIn(w)) w = w.lowercase()
        return w
    }

    // ------------------------------------------------------------ файл
    @Synchronized
    fun load(f: File): MutableList<Entry> {
        if (!f.exists()) return ArrayList()
        val arr = try { JSONObject(f.readText()).optJSONArray("entries") ?: JSONArray() } catch (_: Exception) { return ArrayList() }
        val out = ArrayList<Entry>()
        for (i in 0 until arr.length()) {
            val o = arr.optJSONObject(i) ?: continue
            val w = o.optString("word").trim()
            if (w.isEmpty()) continue
            out.add(Entry(w, o.optString("translation"), o.optString("note"), o.optString("lang"), o.optString("tgt"),
                o.optString("context"), o.optString("context_tr"), o.optString("title"), o.optLong("added")))
        }
        return out
    }

    @Synchronized
    fun save(f: File, entries: List<Entry>) {
        val arr = JSONArray()
        for (e in entries) {
            if (e.word.isBlank()) continue
            arr.put(JSONObject().put("word", e.word).put("translation", e.translation).put("note", e.note)
                .put("lang", e.lang).put("tgt", e.tgt).put("context", e.context).put("context_tr", e.contextTr)
                .put("title", e.title).put("added", e.added))
        }
        // через временный файл: если приложение закроют посреди записи, словарик не испортится
        val tmp = File(f.parentFile, f.name + ".tmp")
        tmp.writeText(JSONObject().put("entries", arr).toString(1))
        if (!tmp.renameTo(f)) {
            f.writeText(tmp.readText())
            tmp.delete()
        }
    }

    fun find(entries: List<Entry>, word: String, lang: String): Entry? {
        val key = Entry(word, lang = lang).key
        return entries.firstOrNull { it.key == key }
    }

    /** Добавить слово. Если оно уже есть — не дублируется: (запись из словарика, true). */
    @Synchronized
    fun add(f: File, entry: Entry): Pair<Entry, Boolean> {
        val entries = load(f)
        val old = find(entries, entry.word, entry.lang)
        if (old != null) {
            var changed = false
            if (old.translation.isBlank() && entry.translation.isNotBlank()) { old.translation = entry.translation; changed = true }
            if (old.context.isBlank() && entry.context.isNotBlank()) { old.context = entry.context; old.contextTr = entry.contextTr; changed = true }
            if (old.title.isBlank() && entry.title.isNotBlank()) { old.title = entry.title; changed = true }
            if (changed) save(f, entries)
            return old to true
        }
        entries.add(0, entry)       // новые сверху
        save(f, entries)
        return entry to false
    }

    fun load(ctx: Context) = load(file(ctx))
    fun add(ctx: Context, entry: Entry) = add(file(ctx), entry)
    fun save(ctx: Context, entries: List<Entry>) = save(file(ctx), entries)

    fun search(entries: List<Entry>, query: String): List<Entry> {
        val q = query.trim().lowercase()
        if (q.isEmpty()) return entries
        return entries.filter { e ->
            listOf(e.word, e.translation, e.note, e.context, e.title).any { q in it.lowercase() }
        }
    }

    /** Для буфера обмена: «слово — перевод (заметка)» по строке. */
    fun toText(entries: List<Entry>): String =
        entries.joinToString("\n") { "${it.word} — ${it.translation}" + if (it.note.isNotBlank()) " (${it.note})" else "" }

    /** CSV: первые два столбца — слово и перевод, так его удобно загрузить в Anki и другие карточки. */
    fun toCsv(entries: List<Entry>): String {
        fun cell(v: String) = if (v.any { it == ',' || it == '"' || it == '\n' || it == '\r' }) "\"" + v.replace("\"", "\"\"") + "\"" else v
        val fmt = SimpleDateFormat("yyyy-MM-dd", Locale.US)
        val sb = StringBuilder("word,translation,note,language,example,example_translation,title,added\n")
        for (e in entries) {
            sb.append(listOf(e.word, e.translation, e.note, e.lang, e.context, e.contextTr, e.title,
                fmt.format(Date(e.added * 1000))).joinToString(",") { cell(it) }).append('\n')
        }
        return sb.toString()
    }

    // ------------------------------------------------------------ перевод слова
    /** Перевод слова из глоссария тайтла (имя, термин), если оно там есть. */
    fun fromGlossary(g: Glossary?, word: String): String {
        if (g == null) return ""
        val key = word.lowercase()
        return g.entries.firstOrNull { it.source.trim().lowercase() == key && it.target.isNotBlank() }?.target?.trim().orEmpty()
    }

    /**
     * Перевод слова → (перевод, откуда). Сначала глоссарий тайтла, иначе выбранный переводчик:
     * ИИ-переводчик видит фразу, где слово встретилось, обычный переводит само слово.
     */
    fun translateWord(ctx: Context, s: Settings, word: String, lang: String, context: String, contextTr: String): Pair<String, String> {
        val glossary = s.glossary.takeIf { it.isNotBlank() }?.let { GlossaryStore.load(ctx, it) }
        fromGlossary(glossary, word).takeIf { it.isNotEmpty() }?.let { return it to "glossary" }
        val req = TranslateRequest(listOf(word), Languages.src(lang.ifBlank { s.sourceLang }), Languages.tgt(s.targetLang),
            history = if (context.isNotBlank() && contextTr.isNotBlank()) listOf(context to contextTr) else emptyList(),
            screen = true)
        val (out, by) = Translators.withFallback(ctx, s, req)
        return out.firstOrNull().orEmpty().trim() to by
    }
}
