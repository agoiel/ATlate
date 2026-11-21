package com.manhwatranslator.core

import org.json.JSONObject
import java.net.HttpURLConnection
import java.net.URL

/**
 * Проверка обновлений: вышла ли на GitHub новая версия. Не чаще раза в день, один запрос к открытому
 * API GitHub без данных о пользователе. Ничего не скачивается само: приложение показывает плашку
 * со ссылкой на ATlate.apk — новая версия ставится поверх старой, настройки сохраняются.
 */
object Updates {
    private const val REPO = "agoiel/ATlate"
    private const val API = "https://api.github.com/repos/$REPO/releases/latest"   // без черновиков и пре-релизов
    const val PAGE = "https://github.com/$REPO/releases/latest"
    const val EVERY_MS = 24L * 3600 * 1000

    data class Release(val version: String, val url: String)

    /** «v0.6.5» → [0, 6, 5, 0]. */
    fun parse(v: String): List<Int> {
        val nums = Regex("\\d+").findAll(v).map { it.value.toIntOrNull() ?: 0 }.take(4).toList()
        return nums + List(4 - nums.size) { 0 }
    }

    fun isNewer(latest: String, current: String): Boolean {
        val a = parse(latest)
        val b = parse(current)
        if (a.all { it == 0 }) return false
        for (i in 0 until 4) if (a[i] != b[i]) return a[i] > b[i]
        return false
    }

    fun due(s: Settings, now: Long = System.currentTimeMillis()): Boolean =
        s.updateCheck && now - s.updateCheckedAt >= EVERY_MS

    /** Последний релиз; null — узнать не удалось. Вызывать не из главного потока. */
    fun fetch(): Release? = try {
        val conn = URL(API).openConnection() as HttpURLConnection
        try {
            conn.connectTimeout = 10_000
            conn.readTimeout = 10_000
            conn.setRequestProperty("Accept", "application/vnd.github+json")
            conn.setRequestProperty("User-Agent", "ATlate-update-check")
            if (conn.responseCode != 200) null else parseRelease(conn.inputStream.bufferedReader().use { it.readText() })
        } finally {
            conn.disconnect()
        }
    } catch (_: Exception) {
        null
    }

    fun parseRelease(json: String): Release? {
        val o = JSONObject(json)
        val tag = o.optString("tag_name").trim()
        if (tag.isEmpty() || o.optBoolean("draft") || o.optBoolean("prerelease")) return null
        // на телефоне удобнее сразу файл APK; нет его — страница релиза
        var url = o.optString("html_url").ifBlank { PAGE }
        val assets = o.optJSONArray("assets")
        if (assets != null) {
            for (i in 0 until assets.length()) {
                val a = assets.optJSONObject(i) ?: continue
                if (a.optString("name").endsWith(".apk", ignoreCase = true)) {
                    url = a.optString("browser_download_url").ifBlank { url }
                    break
                }
            }
        }
        if (!url.startsWith("https://github.com/")) url = PAGE
        return Release(tag.trimStart('v', 'V'), url)
    }

    /** Что показать: версия новее текущей и о ней не просили «Позже». */
    fun offer(s: Settings, current: String): Release? {
        val v = s.updateLatest
        if (v.isBlank() || !isNewer(v, current) || v == s.updateSkip) return null
        return Release(v, s.updateUrl.ifBlank { PAGE })
    }
}
