package com.manhwatranslator.core

import android.content.Context
import org.json.JSONArray
import org.json.JSONObject
import java.io.File
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

/**
 * История переводов (режим разработчика): оригинал и перевод каждой реплики со временем, тайтлом,
 * языками и переводчиком. Пишется, только пока включён режим разработчика, в files/history.jsonl —
 * одна строка на переведённую страницу; формат тот же, что в ПК-версии. Когда файл больше
 * MAX_BYTES, старая половина записей отбрасывается.
 */
object History {
    const val MAX_BYTES = 2_000_000L

    data class Line(val time: Long, val title: String, val src: String, val tgt: String,
                    val translator: String, val original: String, val translation: String)

    fun file(ctx: Context) = File(ctx.filesDir, "history.jsonl")

    @Synchronized
    fun add(ctx: Context, res: PageResult, s: Settings, title: String): Int = add(file(ctx), res, s.sourceLang,
        s.targetLang, s.translator, title)

    @Synchronized
    fun add(f: File, res: PageResult, srcLang: String, tgtLang: String, translator: String, title: String): Int {
        val lines = JSONArray()
        for (b in res.bubbles) {
            val t = b.translation.trim()
            if (t.isEmpty()) continue
            lines.put(JSONArray().put(b.text.trim()).put(t).put(b.lang.ifBlank { srcLang }))
        }
        if (lines.length() == 0) return 0
        val rec = JSONObject().put("t", System.currentTimeMillis() / 1000).put("title", title)
            .put("tgt", tgtLang).put("tor", translator).put("lines", lines)
        return try {
            f.appendText(rec.toString() + "\n")
            if (f.length() > MAX_BYTES) trim(f)
            lines.length()
        } catch (_: Exception) {
            0
        }
    }

    private fun trim(f: File) {
        val rows = f.readLines()
        val tmp = File(f.parentFile, f.name + ".tmp")
        tmp.writeText(rows.drop(rows.size / 2).joinToString("\n") + "\n")
        if (!tmp.renameTo(f)) {
            f.writeText(tmp.readText())
            tmp.delete()
        }
    }

    /** Все реплики, новые сверху. */
    @Synchronized
    fun load(f: File): List<Line> {
        if (!f.exists()) return emptyList()
        val out = ArrayList<Line>()
        val rows = try { f.readLines() } catch (_: Exception) { return emptyList() }
        for (row in rows.asReversed()) {
            val rec = try { JSONObject(row) } catch (_: Exception) { continue }   // оборванная запись — пропускаем
            val lines = rec.optJSONArray("lines") ?: continue
            for (i in 0 until lines.length()) {
                val item = lines.optJSONArray(i) ?: continue
                if (item.length() < 2) continue
                out.add(Line(rec.optLong("t") * 1000, rec.optString("title"), item.optString(2), rec.optString("tgt"),
                    rec.optString("tor"), item.optString(0), item.optString(1)))
            }
        }
        return out
    }

    fun load(ctx: Context) = load(file(ctx))

    fun search(lines: List<Line>, query: String): List<Line> {
        val q = query.trim().lowercase()
        if (q.isEmpty()) return lines
        return lines.filter { q in it.original.lowercase() || q in it.translation.lowercase() || q in it.title.lowercase() }
    }

    /** Для буфера обмена и «Поделиться»: оригинал и перевод парами. */
    fun toText(lines: List<Line>): String = lines.joinToString("\n\n") { "${it.original}\n${it.translation}" }

    fun toCsv(lines: List<Line>): String {
        val fmt = SimpleDateFormat("yyyy-MM-dd HH:mm:ss", Locale.US)
        fun cell(v: String) = if (v.any { it == ',' || it == '"' || it == '\n' || it == '\r' }) "\"" + v.replace("\"", "\"\"") + "\"" else v
        val sb = StringBuilder("time,title,source_lang,target_lang,translator,original,translation\n")
        for (x in lines) {
            sb.append(listOf(fmt.format(Date(x.time)), x.title, x.src, x.tgt, x.translator, x.original, x.translation)
                .joinToString(",") { cell(it) }).append('\n')
        }
        return sb.toString()
    }

    @Synchronized
    fun clear(ctx: Context) {
        file(ctx).delete()
    }
}
