package com.manhwatranslator.core

import android.content.Context
import org.json.JSONArray
import org.json.JSONObject
import java.io.File

/** auto — запись добавил авто-глоссарий (ИИ-переводчик), а не человек. */
data class GlossaryEntry(val source: String, val target: String, val note: String = "", val auto: Boolean = false)

/**
 * Глоссарий тайтла. Формат JSON совпадает с ПК-версией, файлы можно переносить.
 *
 * Обычные переводчики получают текст, где термин уже заменён на перевод («якорь»),
 * ИИ-переводчики получают список терминов с заметками.
 */
class Glossary(var name: String, var sourceLang: String = "", var entries: List<GlossaryEntry> = emptyList(),
               /** пополнять сам: ИИ-переводчик добавляет новые имена и термины */
               var auto: Boolean = false) {

    val autoCount: Int get() = entries.count { it.auto }

    /**
     * Авто-глоссарий: добавить новые термины от ИИ-переводчика (те же правила, что в ПК-версии).
     * Термин отбрасывается, если его нет в переведённом тексте, если это фраза, а не имя, если он
     * уже есть в глоссарии (в любом написании — с другим регистром или с частицей на конце)
     * или если лимит авто-записей исчерпан. Возвращает то, что действительно добавлено.
     */
    fun addTerms(terms: List<GlossaryEntry>, texts: List<String>): List<GlossaryEntry> {
        val page = texts.joinToString("\n").replace(Regex("\\s+"), "").lowercase()
        val known = entries.map { key(it.source) }.toHashSet()
        val room = minOf(MAX_NEW_PER_PAGE, MAX_AUTO - autoCount)
        val added = ArrayList<GlossaryEntry>()
        for (t in terms) {
            if (added.size >= room) break
            val src = t.source.replace(Regex("\\s+"), " ").trim()
            val tgt = t.target.replace(Regex("\\s+"), " ").trim()
            val note = t.note.replace(Regex("\\s+"), " ").trim().take(60)
            if (src.isEmpty() || tgt.isEmpty() || src.length > 40 || tgt.length > 60) continue
            if (sentence.containsMatchIn(src) || src.count { it.isLetter() } < 2) continue   // фраза или не слово
            val latin = latinLetter.containsMatchIn(src)
            if ((latin && src.split(" ").size > 4) || (!latin && src.replace(" ", "").length > 12)) continue
            if (!latin && tgt.equals(src, ignoreCase = true)) continue                     // модель не перевела
            if (src.replace(Regex("\\s+"), "").lowercase() !in page) continue             // в тексте такого нет
            val k = key(src)
            if (k.isEmpty() || !known.add(k)) continue
            added.add(GlossaryEntry(src, tgt, note, auto = true))
        }
        if (added.isNotEmpty()) entries = entries + added
        return added
    }

    private fun sorted() = entries.filter { it.source.isNotBlank() && it.target.isNotBlank() }
        .sortedByDescending { it.source.length }

    /**
     * Шаблон одного термина. Термин ищется только в начале слова: «인형» (кукла) не совпадёт с «형».
     * После термина могут идти частицы и окончания (선배님, 선배가, 길드에서).
     */
    private fun term(e: GlossaryEntry, inside: Boolean = false): String {
        // японский и китайский: пробелов нет, поэтому для терминов из иероглифов и каны длиной от
        // двух знаков начало слова не требуется («強くなった田中さん»); однознаковые — только
        // в начале слова, иначе «王» находился бы внутри «王国»
        val cjk = e.source.firstOrNull()?.let { cjkChar.matches(it.toString()) } == true
        var begin = if (cjk && e.source.length >= 2) "" else start
        if (inside && !cjk) begin = ""      // внутри общего шаблона начало слова уже проверено (mayStart)
        // английские термины — целым словом: «Gate» не должен совпасть с «Gatekeeper»
        val latinEnd = e.source.lastOrNull()?.let { it in 'a'..'z' || it in 'A'..'Z' } == true
        val end = if (latinEnd) "(?![\\p{L}\\p{N}])" else ""
        return begin + Regex.escape(e.source) + end
    }

    /** Глагольные окончания: «각성했다» — «пробудился», а не «пробуждение했다». */
    private val verbEnding = "(?![하했한할해합함됐된될되돼])"
    private val start = "(?<![\\p{L}\\p{N}])"
    private val cjkChars = "\\u3040-\\u30FF\\u3400-\\u4DBF\\u4E00-\\u9FFF\\uF900-\\uFAFF"
    private val cjkChar = Regex("[$cjkChars]")
    /** Быстрый отсев: термин может начаться только в начале слова или на иероглифе/кане. */
    private val mayStart = "(?:$start|(?=[$cjkChars]))"

    /**
     * Один общий шаблон на весь глоссарий — собирается заново, только когда записи изменились.
     * Раньше на каждой странице для каждой записи собирался свой шаблон: с сотнями записей
     * (авто-глоссарий) поиск терминов занимал заметное время на каждом переводе.
     */
    private class Compiled(
        val forEntries: List<GlossaryEntry>,
        val terms: List<GlossaryEntry>,
        val find: Regex?,
        val anchors: Regex?,
        val byKey: Map<String, List<GlossaryEntry>>,
        val prefixes: Map<GlossaryEntry, List<GlossaryEntry>>,
    )

    @Volatile private var compiled: Compiled? = null

    private fun compiled(): Compiled {
        val list = entries
        compiled?.let { if (it.forEntries === list) return it }
        val terms = sorted()
        val byKey = terms.groupBy { it.source.lowercase() }
        val alts = terms.joinToString("|") { term(it, inside = true) }
        // поиск: на каждой позиции (заглядыванием вперёд) — самый длинный термин, который там начинается;
        // подстановка: альтернативы от длинных к коротким — «Кан Джину» не разобьётся на «Кан»
        val find = if (terms.isEmpty()) null else Regex("(?=$mayStart($alts))", RegexOption.IGNORE_CASE)
        val anchors = if (terms.isEmpty()) null else Regex("$mayStart(?:$alts)$verbEnding", RegexOption.IGNORE_CASE)
        // термины, которые сами являются началом другого (선배 → 선배님): на той же позиции шаблон
        // найдёт только длинный, короткие проверяются отдельно
        val prefixes = HashMap<GlossaryEntry, List<GlossaryEntry>>()
        for (e in terms) {
            val low = e.source.lowercase()
            val shorter = (1 until low.length).flatMap { k -> byKey[low.substring(0, k)].orEmpty() }
            if (shorter.isNotEmpty()) prefixes[e] = shorter
        }
        return Compiled(list, terms, find, anchors, byKey, prefixes).also { compiled = it }
    }

    private fun entriesFor(matched: String, c: Compiled): List<GlossaryEntry> =
        c.byKey[matched.lowercase()]
            // редкие буквы, у которых регистр сравнивается иначе, — сверяем по одному
            ?: c.terms.filter { Regex(term(it), RegexOption.IGNORE_CASE).matches(matched) }

    /** Термины, которые встречаются в этих текстах (от длинных к коротким). */
    fun find(texts: List<String>): List<GlossaryEntry> {
        val c = compiled()
        val re = c.find ?: return emptyList()
        val joined = texts.joinToString("\n")
        val hits = HashSet<GlossaryEntry>()
        for (m in re.findAll(joined)) {
            val g = m.groups[1] ?: continue
            for (e in entriesFor(g.value, c)) {
                hits.add(e)
                for (f in c.prefixes[e].orEmpty()) {
                    if (f !in hits && Regex(term(f), RegexOption.IGNORE_CASE).matchAt(joined, g.range.first) != null) hits.add(f)
                }
            }
        }
        return c.terms.filter { it in hits }
    }

    fun applyAnchors(text: String): String {
        val c = compiled()
        val re = c.anchors ?: return text
        return re.replace(text) { m -> entriesFor(m.value, c).firstOrNull()?.target ?: m.value }
    }

    fun toJson(): String {
        val arr = JSONArray()
        for (e in entries) {
            if (e.source.isBlank()) continue
            val o = JSONObject().put("source", e.source).put("target", e.target).put("note", e.note)
            if (e.auto) o.put("auto", true)       // у записей человека пометки нет — файл не раздувается
            arr.put(o)
        }
        val root = JSONObject().put("name", name).put("source_lang", sourceLang)
        if (auto) root.put("auto", true)
        return root.put("entries", arr).toString(2)
    }

    /** Текстовый вид для редактора: «оригинал = перевод | заметка» на строку; запись ИИ — с пометкой [авто]. */
    fun toText(): String = entries.joinToString("\n") { e ->
        val line = if (e.note.isBlank()) "${e.source} = ${e.target}" else "${e.source} = ${e.target} | ${e.note}"
        if (e.auto) "$line $AUTO_MARK" else line
    }

    companion object {
        /** Пометка записи ИИ в текстовом редакторе. Убрали пометку — запись подтверждена человеком. */
        const val AUTO_MARK = "[авто]"
        const val MAX_AUTO = 1500
        const val MAX_NEW_PER_PAGE = 8
        private val sentence = Regex("[.!?。！？…]")
        private val latinLetter = Regex("[A-Za-z\\u00C0-\\u024F]")
        private val hangul = Regex("[\\uAC00-\\uD7AF]")
        // корейские частицы и окончания, которые модель могла оставить на конце имени
        private val josa = listOf("에게서", "께서는", "이랑", "에게", "에서", "한테", "께서", "이나", "이야", "은", "는", "이", "가",
            "을", "를", "의", "에", "와", "과", "도", "만", "랑", "아", "야")

        /** Запись термина для сравнения: без регистра, пробелов и корейских частиц на конце. */
        fun key(text: String): String {
            val k = text.replace(Regex("\\s+"), "").lowercase().trim('"', '\'', '«', '»', '“', '”', '(', ')', '[', ']', '「', '」')
            if (k.isNotEmpty() && hangul.matches(k.last().toString())) {
                for (j in josa) if (k.endsWith(j) && k.length - j.length >= 2) return k.dropLast(j.length)
            }
            return k
        }

        fun fromJson(json: String, fallbackName: String): Glossary {
            val o = JSONObject(json)
            val arr = o.optJSONArray("entries") ?: JSONArray()
            val list = (0 until arr.length()).mapNotNull { i ->
                val e = arr.optJSONObject(i) ?: return@mapNotNull null
                val s = e.optString("source")
                if (s.isBlank()) null else GlossaryEntry(s, e.optString("target"), e.optString("note"), e.optBoolean("auto"))
            }
            return Glossary(o.optString("name", fallbackName), o.optString("source_lang"), list, o.optBoolean("auto"))
        }

        fun parseText(text: String): List<GlossaryEntry> = text.lines().mapNotNull { line ->
            var t = line.trim()
            if (t.isEmpty() || t.startsWith("#") || !t.contains("=")) return@mapNotNull null
            val auto = t.endsWith(AUTO_MARK) || t.endsWith("[auto]")
            if (auto) t = t.substringBeforeLast('[').trim()
            val src = t.substringBefore("=").trim()
            val rest = t.substringAfter("=")
            val tgt = rest.substringBefore("|").trim()
            val note = if (rest.contains("|")) rest.substringAfter("|").trim() else ""
            if (src.isEmpty() || tgt.isEmpty()) null else GlossaryEntry(src, tgt, note, auto)
        }
    }
}

/** Хранилище глоссариев: files/glossaries/<имя>.json */
object GlossaryStore {
    fun dir(ctx: Context): File = File(ctx.filesDir, "glossaries").apply { mkdirs() }

    fun list(ctx: Context): List<String> =
        dir(ctx).listFiles { f -> f.extension == "json" }?.map { it.name }?.sorted() ?: emptyList()

    fun load(ctx: Context, file: String): Glossary? = try {
        val f = File(dir(ctx), file)
        if (f.exists()) Glossary.fromJson(f.readText(), f.nameWithoutExtension) else null
    } catch (e: Exception) {
        null
    }

    fun save(ctx: Context, file: String, g: Glossary) {
        // через временный файл: если приложение закроют посреди записи, глоссарий не испортится
        val target = File(dir(ctx), file)
        val tmp = File(dir(ctx), "$file.tmp")
        tmp.writeText(g.toJson())
        if (!tmp.renameTo(target)) {
            target.writeText(g.toJson())
            tmp.delete()
        }
    }

    fun delete(ctx: Context, file: String) {
        File(dir(ctx), file).delete()
    }

    /**
     * Глоссарии, которые идут вместе с приложением (assets/glossaries).
     * Ставятся один раз при первом запуске; если пользователь удалит — не возвращаются.
     */
    fun installBundled(ctx: Context, settings: Settings) {
        val prefs = ctx.getSharedPreferences("settings", Context.MODE_PRIVATE)
        val done = HashSet(prefs.getStringSet("bundled_installed", emptySet()) ?: emptySet())
        // первая версия ставила только корейский
        if (prefs.getBoolean("bundled_glossaries_v1", false)) done.add("universal_ko.json")
        try {
            for (name in ctx.assets.list("glossaries") ?: emptyArray()) {
                if (name in done) continue
                val target = File(dir(ctx), name)
                if (!target.exists()) {
                    ctx.assets.open("glossaries/$name").use { inp -> target.outputStream().use { inp.copyTo(it) } }
                }
                done.add(name)
            }
            if (settings.glossary.isBlank()) universalFor(ctx, settings.sourceLang)?.let { settings.glossary = it }
        } catch (_: Exception) {
        }
        prefs.edit().putStringSet("bundled_installed", done).apply()
    }

    /**
     * Встроенный универсальный глоссарий для языка оригинала, если он есть. Для языков на латинице
     * без своего — английский: в нём корейские обращения латиницей, их пишут и в других сканлейтах.
     */
    fun universalFor(ctx: Context, lang: String): String? {
        val name = "universal_${lang.substringBefore('-')}.json"
        if (File(dir(ctx), name).exists()) return name
        val en = "universal_en.json"
        return if (Languages.src(lang).isLatin && Languages.src(lang).code == lang && File(dir(ctx), en).exists()) en else null
    }

    /** При смене языка оригинала меняем универсальный глоссарий на подходящий (свои не трогаем). */
    fun switchUniversal(ctx: Context, settings: Settings) {
        val cur = settings.glossary
        if (cur.isNotBlank() && !cur.startsWith("universal_")) return
        settings.glossary = universalFor(ctx, settings.sourceLang) ?: ""
    }

    /** Имя файла из названия тайтла. */
    fun fileNameFor(ctx: Context, title: String): String {
        val slug = title.trim().replace(Regex("[^\\p{L}\\p{N}_-]+"), "_").trim('_').ifEmpty { "glossary" }
        var name = "$slug.json"
        var i = 2
        while (File(dir(ctx), name).exists()) name = "${slug}_${i++}.json"
        return name
    }
}
