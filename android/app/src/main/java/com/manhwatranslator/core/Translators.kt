package com.manhwatranslator.core

import org.json.JSONArray
import org.json.JSONObject
import java.net.HttpURLConnection
import java.net.URL
import java.net.URLEncoder

class TranslateException(message: String, val code: Int = 0) : Exception(message)

class TranslateRequest(
    val texts: List<String>,
    val src: SourceLang,
    val tgt: TargetLang,
    val history: List<Pair<String, String>> = emptyList(),
    val glossary: List<GlossaryEntry> = emptyList(),
    /** тексты с подставленными якорями (для обычных переводчиков) */
    val anchored: List<String>? = null,
    /** обычный текст с экрана, а не облака манхвы */
    val screen: Boolean = false,
    /** ИИ-переводчик заодно называет новые имена и термины (авто-глоссарий) */
    val collectTerms: Boolean = false,
) {
    /** Язык оригинала, если его определял сам переводчик (код ISO). Заполняет переводчик. */
    var detected: String = ""
    /** Новые имена и термины от ИИ-переводчика (для авто-глоссария). Заполняет переводчик. */
    var terms: List<GlossaryEntry> = emptyList()
}

/** Код языка для сервиса; пустой — сервис этот язык не поддерживает. */
internal fun requireCode(code: String, service: String, lang: TargetLang): String {
    if (code.isBlank()) throw TranslateException(
        trf("%s не поддерживает перевод на язык «%s». Выберите другой переводчик.", service, lang.name))
    return code
}

/** Код языка оригинала для сервиса. AUTO — пусть сервис определит язык сам; пустой — не поддерживает. */
internal fun sourceCode(code: String, service: String, lang: SourceLang): String {
    if (code.isBlank()) throw TranslateException(
        trf("%s не поддерживает перевод с языка «%s». Выберите другой переводчик или язык «Латиница (язык определит переводчик)».",
            service, lang.name))
    return code
}

private const val AUTO = Languages.AUTO

interface Translator {
    val title: String
    val usesLlm: Boolean get() = false
    fun translate(req: TranslateRequest): List<String>
}

// ==================================================================== HTTP

/**
 * Ограничитель частоты: между двумя запросами к сервису перевода проходит
 * не меньше заданной паузы. Запросы идут строго по очереди.
 */
object RateLimit {
    @Volatile var intervalMs: Long = 1000
    private var last = 0L

    @Synchronized
    fun await() {
        val wait = last + intervalMs - System.currentTimeMillis()
        if (wait > 0) Thread.sleep(wait)
        last = System.currentTimeMillis()
    }
}

internal object Http {
    /** Временные сбои сервиса: перегружен, ошибка на его стороне. Такие запросы повторяются с паузой. */
    private val retryCodes = setOf(500, 502, 503, 504, 529)
    private const val ATTEMPTS = 3
    /** Код ошибки «оборвалась связь» (повторяется) и «долго нет ответа» (не повторяется). */
    const val NO_CONNECTION = -1
    const val TIMEOUT = -2

    /**
     * Запрос с повторами при временных сбоях. Раньше повторялся только ответ «слишком много
     * запросов», а перегрузка сервиса или оборванное соединение сразу давали ошибку на всю страницу.
     */
    fun call(url: String, method: String = "GET", headers: Map<String, String> = emptyMap(),
             body: String? = null, title: String): String {
        var attempt = 1
        while (true) {
            try {
                return once(url, method, headers, body, title)
            } catch (e: TranslateException) {
                val pause = when {
                    // сервис попросил притормозить: ждём и пробуем ещё раз
                    e.code == 429 && attempt == 1 -> maxOf(3000L, RateLimit.intervalMs * 2)
                    e.code == NO_CONNECTION && attempt < ATTEMPTS -> 1500L * attempt
                    e.code in retryCodes && attempt < ATTEMPTS -> 2000L * attempt
                    else -> throw e
                }
                Thread.sleep(pause)
                attempt++
            }
        }
    }

    private fun once(url: String, method: String, headers: Map<String, String>, body: String?, title: String): String {
        RateLimit.await()
        val conn = try {
            URL(url).openConnection() as HttpURLConnection
        } catch (e: Exception) {
            throw TranslateException(trf("%s: неверный адрес", title))
        }
        try {
            conn.requestMethod = method
            conn.connectTimeout = 15000
            conn.readTimeout = 60000
            headers.forEach { (k, v) -> conn.setRequestProperty(k, v) }
            if (body != null) {
                conn.doOutput = true
                if (!headers.containsKey("Content-Type")) conn.setRequestProperty("Content-Type", "application/json; charset=utf-8")
                conn.outputStream.use { it.write(body.toByteArray(Charsets.UTF_8)) }
            }
            val code = conn.responseCode
            val stream = if (code < 400) conn.inputStream else conn.errorStream
            val text = stream?.bufferedReader(Charsets.UTF_8)?.use { it.readText() } ?: ""
            if (code >= 400) {
                val hint = when (code) {
                    401 -> tr("неверный API-ключ")
                    403 -> tr("доступ запрещён — проверьте ключ и регион")
                    // у ИИ-переводчиков так отвечают, когда модель отключили или в названии опечатка
                    404 -> tr("не найдено — проверьте адрес сервера и название модели")
                    429 -> tr("слишком много запросов или закончился лимит")
                    456 -> tr("закончился месячный лимит символов")
                    500, 502 -> tr("ошибка на стороне сервиса — попробуйте позже")
                    503, 529 -> tr("сервис перегружен — попробуйте позже")
                    504 -> tr("сервис не успел ответить — попробуйте позже")
                    else -> ""
                }
                // HTML-страницы ошибок не показываем — в них нет ничего полезного
                val details = if (text.trimStart().startsWith("<")) "" else text.take(200)
                throw TranslateException((trf("%s: ошибка %s — %s", title, code, hint) + "\n" + details).trim(), code)
            }
            return text
        } catch (e: TranslateException) {
            throw e
        } catch (e: java.net.SocketTimeoutException) {
            // долгое ожидание не повторяем: это ещё столько же секунд
            throw TranslateException(trf("%s: сервис не ответил вовремя", title), TIMEOUT)
        } catch (e: Exception) {
            throw TranslateException(trf("%s: нет связи (%s)", title, e.javaClass.simpleName), NO_CONNECTION)
        } finally {
            conn.disconnect()
        }
    }

    fun enc(s: String): String = URLEncoder.encode(s, "UTF-8")
}

// ==================================================================== обычные переводчики

/**
 * Бесплатный веб-эндпоинт Google без ключа. Неофициальный — может быть ограничен.
 * Все облака страницы уходят одним запросом (через перевод строки), а не по одному.
 */
class GoogleFreeTranslator : Translator {
    override val title = tr("Google (без ключа)")

    private fun request(text: String, sl: String, tl: String, req: TranslateRequest? = null): String {
        val url = "https://translate.googleapis.com/translate_a/single?client=gtx&dt=t" +
            "&sl=${Http.enc(sl)}&tl=${Http.enc(tl)}&q=${Http.enc(text)}"
        val raw = try {
            Http.call(url, title = title)
        } catch (e: TranslateException) {
            if (e.code == 429) {
                throw TranslateException(tr("Google временно ограничил бесплатные запросы с вашего адреса. Чаще всего так бывает при включённом VPN. Отключите VPN, подождите несколько минут или выберите другой переводчик (DeepL, Gemini)."), 429)
            }
            throw e
        }
        return try {
            val all = JSONArray(raw)
            // язык определял сам Google — он сообщает какой
            if (sl == AUTO && req != null && all.length() > 2 && all.opt(2) is String) req.detected = all.getString(2)
            val segs = all.getJSONArray(0)
            buildString {
                for (i in 0 until segs.length()) {
                    val seg = segs.optJSONArray(i) ?: continue
                    if (!seg.isNull(0)) append(seg.optString(0))
                }
            }
        } catch (e: Exception) {
            throw TranslateException(tr("Google: неожиданный ответ сервера"))
        }
    }

    override fun translate(req: TranslateRequest): List<String> {
        val texts = (req.anchored ?: req.texts).map { it.replace('\n', ' ') }
        val sl = req.src.google
        val tl = req.tgt.google
        // Делим на порции, чтобы адрес запроса не получился слишком длинным
        val chunks = ArrayList<MutableList<Int>>()
        var size = 0
        for (i in texts.indices) {
            val len = Http.enc(texts[i]).length + 3
            if (chunks.isEmpty() || size + len > 5000) {
                chunks.add(mutableListOf())
                size = 0
            }
            chunks.last().add(i)
            size += len
        }
        val out = arrayOfNulls<String>(texts.size)
        for (chunk in chunks) {
            val joined = chunk.joinToString("\n") { texts[it] }
            val parts = request(joined, sl, tl, req).split("\n").map { it.trim() }
            if (parts.size == chunk.size) {
                chunk.forEachIndexed { k, idx -> out[idx] = parts[k] }
            } else {
                // Google склеил строки — переводим эту порцию по одной
                for (idx in chunk) out[idx] = request(texts[idx], sl, tl, req).trim()
            }
        }
        return out.map { it.orEmpty() }
    }
}

/** DeepL API. План Free — ключ оканчивается на :fx. */
class DeepLTranslator(private val key: String) : Translator {
    override val title = "DeepL"

    override fun translate(req: TranslateRequest): List<String> {
        if (key.isBlank()) throw TranslateException(tr("DeepL: укажите API-ключ в настройках"))
        val host = if (key.trim().endsWith(":fx")) "api-free.deepl.com" else "api.deepl.com"
        val sl = sourceCode(req.src.deepl, "DeepL", req.src)
        val body = JSONObject()
            .put("text", JSONArray(req.anchored ?: req.texts))
            .put("target_lang", requireCode(req.tgt.deepl, "DeepL", req.tgt))
        if (sl != AUTO) body.put("source_lang", sl)     // без него DeepL определяет язык сам
        if (req.history.isNotEmpty()) body.put("context", req.history.takeLast(8).joinToString("\n") { it.first })
        val raw = Http.call("https://$host/v2/translate", "POST",
            mapOf("Authorization" to "DeepL-Auth-Key ${key.trim()}"), body.toString(), title)
        val arr = JSONObject(raw).getJSONArray("translations")
        if (sl == AUTO && arr.length() > 0) req.detected = arr.getJSONObject(0).optString("detected_source_language")
        return (0 until arr.length()).map { arr.getJSONObject(it).getString("text") }
    }
}

/** Azure AI Translator. Бесплатный уровень F0 — 2 млн символов в месяц. */
class MicrosoftTranslator(private val key: String, private val region: String) : Translator {
    override val title = "Microsoft"

    override fun translate(req: TranslateRequest): List<String> {
        if (key.isBlank()) throw TranslateException(tr("Microsoft: укажите API-ключ в настройках"))
        val headers = mutableMapOf("Ocp-Apim-Subscription-Key" to key.trim())
        if (region.isNotBlank() && region != "global") headers["Ocp-Apim-Subscription-Region"] = region.trim()
        val body = JSONArray()
        (req.anchored ?: req.texts).forEach { body.put(JSONObject().put("Text", it)) }
        val sl = sourceCode(req.src.microsoft, "Microsoft", req.src)
        // без from Microsoft определяет язык сам
        val url = "https://api.cognitive.microsofttranslator.com/translate?api-version=3.0" +
            (if (sl == AUTO) "" else "&from=${Http.enc(sl)}") +
            "&to=${Http.enc(requireCode(req.tgt.microsoft, "Microsoft", req.tgt))}"
        val arr = JSONArray(Http.call(url, "POST", headers, body.toString(), title))
        if (sl == AUTO && arr.length() > 0) {
            req.detected = arr.getJSONObject(0).optJSONObject("detectedLanguage")?.optString("language").orEmpty()
        }
        return (0 until arr.length()).map {
            arr.getJSONObject(it).getJSONArray("translations").getJSONObject(0).getString("text")
        }
    }
}

// ==================================================================== ИИ-переводчики

object LlmPrompt {
    fun system(req: TranslateRequest): String = if (req.screen) screenSystem(req) else manhwaSystem(req)

    private fun screenSystem(req: TranslateRequest): String = """
You translate text captured from a phone screen (apps, articles, chats, games, menus) from ${req.src.llm} into ${req.tgt.llm}.

Rules:
- The fragments are separate blocks of text in reading order. Translate each one accurately and naturally.
- The text comes from OCR and may contain recognition errors or broken line wraps. Silently fix obvious errors.
- Keep short UI labels short. Keep numbers, names, usernames, links and code as they are.
- Always use the glossary translations for names and terms.
- Do not add notes or explanations. Do not merge or split fragments.
${extra(req)}- Answer ONLY with JSON: ${answer(req)} — exactly ${req.texts.size} translations in the same order.
""".trim()

    private fun manhwaSystem(req: TranslateRequest): String = """
You are an experienced translator of webtoons, manhwa, manga and manhua.
Translate speech bubbles from ${req.src.llm} into ${req.tgt.llm}.

Rules:
- Write natural, lively spoken ${req.tgt.llm}, the way a good scanlation team would. Keep each character's tone.
- The bubbles are consecutive lines of the same scene, in reading order. Use them and the previous context to resolve who speaks, gender, and formal/informal address.
- The text comes from OCR and may contain recognition errors or broken line wraps. Silently fix obvious errors.
- Always use the glossary translations for names and terms; inflect them according to ${req.tgt.llm} grammar.
- Sound effects: translate briefly and expressively.
- Do not add notes, explanations or quotes. Do not merge or split bubbles.
${extra(req)}- Answer ONLY with JSON: ${answer(req)} — exactly ${req.texts.size} translations in the same order.
""".trim()

    private fun detect(req: TranslateRequest) = req.src.google == AUTO

    /**
     * Дополнительные правила. Авто-глоссарий: модель заодно называет новые имена и термины —
     * отдельного запроса нет, поэтому пополнение глоссария почти ничего не стоит.
     */
    private fun extra(req: TranslateRequest): String = buildString {
        if (detect(req)) append("- The original language is not specified: detect it yourself and put its ISO 639-1 code into \"source_language\".\n")
        if (req.collectTerms) append("- Glossary building: in \"terms\" list proper names (characters, places, organizations, skills, items, titles, ranks) and recurring special terms that appear in these lines but are NOT in the glossary above. For each give \"source\" — exactly as written in the original lines, in base form without grammatical particles or case endings; \"target\" — the ${req.tgt.llm} translation you used; \"note\" — for people: gender and who they are, up to 5 words; otherwise what it is (place, skill, item…). Never include common words, interjections, sound effects, ordinary phrases or whole sentences. If there are none, return an empty list.\n")
    }

    private fun answer(req: TranslateRequest): String {
        val parts = mutableListOf("\"translations\": [\"...\", \"...\"]")
        if (detect(req)) parts.add("\"source_language\": \"xx\"")
        if (req.collectTerms) parts.add("\"terms\": [{\"source\": \"...\", \"target\": \"...\", \"note\": \"...\"}]")
        return "{" + parts.joinToString(", ") + "}"
    }

    fun user(req: TranslateRequest): String = buildString {
        if (req.glossary.isNotEmpty()) {
            appendLine("Glossary:")
            req.glossary.forEach { e -> appendLine("- ${e.source} → ${e.target}" + if (e.note.isNotBlank()) "  (${e.note})" else "") }
            appendLine()
        }
        if (req.history.isNotEmpty()) {
            appendLine("Previous lines (context only, do not translate):")
            req.history.forEach { (o, t) -> appendLine("- $o → $t") }
            appendLine()
        }
        appendLine("Bubbles to translate (${req.texts.size}):")
        append(JSONArray(req.texts).toString(1))
    }

    /** Ответ оборвался или в нём не все переводы — страницу стоит перевести по частям. */
    private class Incomplete(message: String) : Exception(message)

    private fun part(req: TranslateRequest, a: Int, b: Int) = TranslateRequest(
        req.texts.subList(a, b), req.src, req.tgt, req.history, req.glossary, req.anchored?.subList(a, b),
        req.screen, req.collectTerms)

    /**
     * Перевод страницы ИИ-переводчиком; длинная страница, ответ на которую оборвался, — по частям.
     * Ответ модели ограничен по длине: на странице с большим количеством текста он мог оборваться
     * посреди JSON, и пропадал перевод всей страницы. ask(часть) → (текст ответа, ответ оборван).
     */
    fun inParts(req: TranslateRequest, title: String, ask: (TranslateRequest) -> Pair<String, Boolean>): List<String> {
        req.terms = emptyList()
        return split(req, req, title, 0, ask)
    }

    private fun split(root: TranslateRequest, req: TranslateRequest, title: String, depth: Int,
                      ask: (TranslateRequest) -> Pair<String, Boolean>): List<String> {
        val n = req.texts.size
        val (raw, truncated) = ask(req)
        val list = read(raw, req)
        val problem = when {
            list == null -> if (truncated) tr("ответ оборвался — слишком длинная страница")
                            else tr("ИИ вернул ответ не в формате JSON. Попробуйте ещё раз.")
            n > 1 && truncated -> tr("ответ оборвался — слишком длинная страница")
            n > 1 && list.length() < n -> tr("в ответе не все переводы")
            else -> null
        }
        if (problem == null) {
            if (req.detected.isNotBlank()) root.detected = req.detected
            if (req !== root) root.terms = root.terms + req.terms
            val out = (0 until list!!.length()).map { list.optString(it).trim() }.toMutableList()
            while (out.size < n) out.add("")
            return out.take(n)
        }
        if (n < 2 || depth >= 3) throw TranslateException("$title: $problem")
        if (req === root) root.terms = emptyList()     // термины из неполного ответа не берём
        val mid = n / 2
        return split(root, part(req, 0, mid), title, depth + 1, ask) + split(root, part(req, mid, n), title, depth + 1, ask)
    }

    /** Ответ модели → переводы; язык оригинала и новые термины кладутся в req. */
    fun parse(raw: String, n: Int, req: TranslateRequest? = null): List<String> {
        val list = read(raw, req) ?: throw TranslateException(tr("ИИ вернул ответ не в формате JSON. Попробуйте ещё раз."))
        val out = (0 until list.length()).map { list.optString(it).trim() }.toMutableList()
        while (out.size < n) out.add("")
        return out.take(n)
    }

    /** Список переводов из ответа (null — не JSON); язык оригинала и термины — в req. */
    private fun read(raw: String, req: TranslateRequest?): JSONArray? {
        var text = raw.trim().replace(Regex("^```(?:json)?|```$", RegexOption.MULTILINE), "").trim()
        fun json(t: String): Pair<JSONArray?, JSONObject?> =
            if (t.startsWith("[")) JSONArray(t) to null else JSONObject(t).let { it.optJSONArray("translations") to it }
        val (list, obj) = try {
            json(text)
        } catch (e: Exception) {
            val m = Regex("\\{.*\\}|\\[.*\\]", RegexOption.DOT_MATCHES_ALL).find(text)
            text = m?.value ?: ""
            try {
                json(text)
            } catch (e2: Exception) {
                null to null
            }
        }
        if (list == null) return null
        if (req != null && obj != null) {
            req.detected = obj.optString("source_language").trim().take(8)
            val arr = obj.optJSONArray("terms")
            if (arr != null) req.terms = (0 until arr.length()).mapNotNull { i ->
                val t = arr.optJSONObject(i) ?: return@mapNotNull null
                GlossaryEntry(t.optString("source"), t.optString("target"), t.optString("note"), auto = true)
            }
        }
        return list
    }
}

class GeminiTranslator(private val key: String, private val model: String) : Translator {
    override val title = "Gemini"
    override val usesLlm = true

    override fun translate(req: TranslateRequest): List<String> {
        if (key.isBlank()) throw TranslateException(tr("Gemini: укажите API-ключ (aistudio.google.com)"))
        return LlmPrompt.inParts(req, title) { part -> ask(part) }
    }

    private fun ask(req: TranslateRequest): Pair<String, Boolean> {
        val body = JSONObject()
            .put("systemInstruction", JSONObject().put("parts", JSONArray().put(JSONObject().put("text", LlmPrompt.system(req)))))
            .put("contents", JSONArray().put(JSONObject().put("role", "user")
                .put("parts", JSONArray().put(JSONObject().put("text", LlmPrompt.user(req))))))
            .put("generationConfig", JSONObject().put("temperature", 0.3).put("responseMimeType", "application/json"))
        val m = model.ifBlank { Settings.GEMINI_MODEL }.trim()
        val raw = try {
            Http.call("https://generativelanguage.googleapis.com/v1beta/models/$m:generateContent", "POST",
                mapOf("x-goog-api-key" to key.trim()), body.toString(), title)
        } catch (e: TranslateException) {
            // модель отключили или в названии опечатка — скажем прямо, а не «ошибка 404»
            if (e.code == 404) throw TranslateException(trf("Gemini: модель «%s» недоступна — её отключили или в названии опечатка. Укажите в настройках другую, например %s.",
                m, Settings.GEMINI_MODEL), 404)
            throw e
        }
        return try {
            val cand = JSONObject(raw).getJSONArray("candidates").getJSONObject(0)
            val parts = cand.getJSONObject("content").getJSONArray("parts")
            (0 until parts.length()).joinToString("") { parts.getJSONObject(it).optString("text") } to
                (cand.optString("finishReason") == "MAX_TOKENS")
        } catch (e: Exception) {
            throw TranslateException(tr("Gemini: пустой ответ"))
        }
    }
}

/** OpenAI, OpenRouter, DeepSeek и любые совместимые серверы. */
class OpenAICompatTranslator(private val baseUrl: String, private val key: String, private val model: String) : Translator {
    override val title = tr("OpenAI-совместимый")
    override val usesLlm = true

    override fun translate(req: TranslateRequest): List<String> {
        if (baseUrl.isBlank() || model.isBlank()) throw TranslateException(tr("Укажите адрес сервера и модель в настройках"))
        return LlmPrompt.inParts(req, title) { part -> ask(part) }
    }

    private fun ask(req: TranslateRequest): Pair<String, Boolean> {
        val headers = if (key.isNotBlank()) mapOf("Authorization" to "Bearer ${key.trim()}") else emptyMap()
        val body = JSONObject()
            .put("model", model.trim())
            .put("temperature", 0.3)
            .put("messages", JSONArray()
                .put(JSONObject().put("role", "system").put("content", LlmPrompt.system(req)))
                .put(JSONObject().put("role", "user").put("content", LlmPrompt.user(req))))
        val raw = Http.call("${baseUrl.trim().trimEnd('/')}/chat/completions", "POST", headers, body.toString(), title)
        return try {
            val choice = JSONObject(raw).getJSONArray("choices").getJSONObject(0)
            choice.getJSONObject("message").getString("content") to (choice.optString("finish_reason") == "length")
        } catch (e: Exception) {
            throw TranslateException(tr("Сервер вернул неожиданный ответ"))
        }
    }
}

// ==================================================================== общий помощник «вся страница одним запросом»

/**
 * Переводит облака порциями: тексты склеиваются через перевод строки, ответ делится обратно.
 * Если сервис склеил строки и их число не совпало, порция переводится по одной.
 */
internal fun translateJoined(texts: List<String>, maxChars: Int, one: (String) -> String): List<String> {
    val clean = texts.map { it.replace('\n', ' ') }
    val chunks = ArrayList<MutableList<Int>>()
    var size = 0
    for (i in clean.indices) {
        val len = clean[i].length + 1
        if (chunks.isEmpty() || size + len > maxChars) {
            chunks.add(mutableListOf())
            size = 0
        }
        chunks.last().add(i)
        size += len
    }
    val out = arrayOfNulls<String>(clean.size)
    for (chunk in chunks) {
        val parts = one(chunk.joinToString("\n") { clean[it] }).split("\n").map { it.trim() }
            .filter { it.isNotEmpty() }
        if (parts.size == chunk.size) chunk.forEachIndexed { k, idx -> out[idx] = parts[k] }
        else for (idx in chunk) out[idx] = one(clean[idx]).trim()
    }
    return out.map { it.orEmpty() }
}

private fun simpleCode(google: String) = when (google) {
    "zh-CN", "zh-TW" -> "zh"
    else -> google
}

// ==================================================================== Яндекс

/**
 * Яндекс Переводчик.
 * Без ключа — неофициальный бесплатный способ (как у мобильного приложения Яндекса).
 * С ключом — официальный Yandex Cloud Translate (нужны API-ключ и ID каталога).
 */
class YandexTranslator(private val key: String, private val folderId: String) : Translator {
    override val title = tr("Яндекс")
    private val ucid = java.util.UUID.randomUUID().toString().replace("-", "")

    override fun translate(req: TranslateRequest): List<String> {
        val texts = req.anchored ?: req.texts
        val sl = simpleCode(req.src.google)
        val tl = simpleCode(req.tgt.google)
        return if (key.isNotBlank()) cloud(texts, sl, tl, req) else free(texts, sl, tl, req)
    }

    private fun free(texts: List<String>, sl: String, tl: String, req: TranslateRequest): List<String> {
        val body = buildString {
            for (t in texts) append("text=").append(Http.enc(t.replace('\n', ' '))).append('&')
            // только язык перевода — тогда Яндекс определяет язык оригинала сам
            append("lang=").append(Http.enc(if (sl == AUTO) tl else "$sl-$tl"))
        }
        val raw = Http.call(
            "https://translate.yandex.net/api/v1/tr.json/translate?ucid=$ucid&srv=android&format=text",
            "POST", mapOf("Content-Type" to "application/x-www-form-urlencoded"), body, title)
        val arr = try {
            val o = JSONObject(raw)
            if (sl == AUTO) req.detected = o.optString("lang").substringBefore('-')
            o.getJSONArray("text")
        } catch (e: Exception) {
            throw TranslateException(tr("Яндекс: неожиданный ответ. Бесплатный способ мог перестать работать — укажите ключ Yandex Cloud или выберите другой переводчик."))
        }
        return texts.indices.map { arr.optString(it).trim() }
    }

    private fun cloud(texts: List<String>, sl: String, tl: String, req: TranslateRequest): List<String> {
        if (folderId.isBlank()) throw TranslateException(tr("Яндекс: для ключа Yandex Cloud укажите ID каталога"))
        val body = JSONObject()
            .put("folderId", folderId.trim())
            .put("targetLanguageCode", tl)
            .put("texts", JSONArray(texts))
        if (sl != AUTO) body.put("sourceLanguageCode", sl)     // без него Яндекс определяет язык сам
        val raw = Http.call("https://translate.api.cloud.yandex.net/translate/v2/translate", "POST",
            mapOf("Authorization" to "Api-Key ${key.trim()}"), body.toString(), title)
        val arr = JSONObject(raw).getJSONArray("translations")
        if (sl == AUTO && arr.length() > 0) req.detected = arr.getJSONObject(0).optString("detectedLanguageCode")
        return (0 until arr.length()).map { arr.getJSONObject(it).optString("text") }
    }
}

// ==================================================================== DuckDuckGo

/** Переводчик DuckDuckGo (внутри — Microsoft Bing). Неофициальный, без ключа. */
class DuckDuckGoTranslator : Translator {
    override val title = "DuckDuckGo"

    private fun token(): String {
        val page = Http.call("https://duckduckgo.com/?q=translate&ia=web",
            headers = mapOf("User-Agent" to UA), title = title)
        return Regex("vqd=[\"']?([\\d-]+)").find(page)?.groupValues?.get(1)
            ?: throw TranslateException(tr("DuckDuckGo: не удалось начать сессию перевода. Попробуйте позже."))
    }

    override fun translate(req: TranslateRequest): List<String> {
        val from = sourceCode(req.src.microsoft, "DuckDuckGo", req.src)
        val vqd = token()
        val to = req.tgt.microsoft.ifBlank { req.tgt.google }
        return translateJoined(req.anchored ?: req.texts, 1500) { text ->
            // без from язык определяется сам
            val url = "https://duckduckgo.com/translation.js?vqd=${Http.enc(vqd)}&query=translate" +
                (if (from == AUTO) "" else "&from=${Http.enc(from)}") + "&to=${Http.enc(to)}"
            val raw = Http.call(url, "POST", mapOf("Content-Type" to "text/plain", "User-Agent" to UA), text, title)
            try {
                val o = JSONObject(raw)
                if (from == AUTO && o.optString("detected_language").isNotBlank()) req.detected = o.optString("detected_language")
                o.getString("translated")
            } catch (e: Exception) {
                throw TranslateException(tr("DuckDuckGo: неожиданный ответ сервера"))
            }
        }
    }

    private companion object {
        const val UA = "Mozilla/5.0 (Linux; Android 14) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Mobile Safari/537.36"
    }
}

// ==================================================================== Lingva

/**
 * Lingva — открытый посредник к Google Переводчику. Помогает, когда Google ограничивает
 * запросы напрямую (например, при VPN). Адрес сервера можно поменять в настройках.
 */
class LingvaTranslator(private val baseUrl: String) : Translator {
    override val title = "Lingva"

    private fun code(google: String) = when (google) {
        "zh-CN" -> "zh"
        "zh-TW" -> "zh_HANT"
        else -> google
    }

    override fun translate(req: TranslateRequest): List<String> {
        val base = baseUrl.trim().trimEnd('/').ifEmpty { "https://lingva.ml" }
        val sl = code(req.src.google)
        val tl = code(req.tgt.google)
        return translateJoined(req.anchored ?: req.texts, 1500) { text ->
            val q = java.net.URLEncoder.encode(text, "UTF-8").replace("+", "%20")
            val raw = Http.call("$base/api/v1/$sl/$tl/$q", title = title)
            val o = try { JSONObject(raw) } catch (e: Exception) {
                throw TranslateException(tr("Lingva: сервер ответил не JSON. Попробуйте другой адрес сервера."))
            }
            if (o.has("error")) throw TranslateException("Lingva: ${o.optString("error")}")
            if (sl == AUTO) o.optJSONObject("info")?.optString("detectedSource")?.takeIf { it.isNotBlank() }?.let { req.detected = it }
            o.optString("translation")
        }
    }
}

// ==================================================================== Papago

/** Papago (Naver Cloud) — сильнее всех в корейском. Нужны Client ID и Client Secret. */
class PapagoTranslator(private val id: String, private val secret: String) : Translator {
    override val title = "Papago"

    override fun translate(req: TranslateRequest): List<String> {
        if (id.isBlank() || secret.isBlank()) throw TranslateException(tr("Papago: укажите Client ID и Client Secret"))
        val headers = mapOf(
            "X-NCP-APIGW-API-KEY-ID" to id.trim(),
            "X-NCP-APIGW-API-KEY" to secret.trim(),
        )
        return translateJoined(req.anchored ?: req.texts, 4500) { text ->
            val body = JSONObject().put("source", req.src.google).put("target", req.tgt.google).put("text", text)
            val raw = Http.call("https://papago.apigw.ntruss.com/nmt/v1/translation", "POST", headers, body.toString(), title)
            try {
                val res = JSONObject(raw).getJSONObject("message").getJSONObject("result")
                if (req.src.google == AUTO && res.optString("srcLangType").isNotBlank()) req.detected = res.optString("srcLangType")
                res.getString("translatedText")
            } catch (e: Exception) {
                throw TranslateException(tr("Papago: неожиданный ответ сервера"))
            }
        }
    }
}

// ==================================================================== Claude

/** Claude (Anthropic API). Учитывает контекст и глоссарий, как Gemini. */
class ClaudeTranslator(private val key: String, private val model: String) : Translator {
    override val title = "Claude"
    override val usesLlm = true

    override fun translate(req: TranslateRequest): List<String> {
        if (key.isBlank()) throw TranslateException(tr("Claude: укажите API-ключ (platform.claude.com)"))
        return LlmPrompt.inParts(req, title) { part -> ask(part) }
    }

    private fun ask(req: TranslateRequest): Pair<String, Boolean> {
        val body = JSONObject()
            .put("model", model.ifBlank { "claude-haiku-5-5" }.trim())
            .put("max_tokens", 4096)
            .put("temperature", 0.3)
            .put("system", LlmPrompt.system(req))
            .put("messages", JSONArray().put(JSONObject().put("role", "user").put("content", LlmPrompt.user(req))))
        val raw = Http.call("https://api.anthropic.com/v1/messages", "POST",
            mapOf("x-api-key" to key.trim(), "anthropic-version" to "2023-06-01"), body.toString(), title)
        return try {
            val js = JSONObject(raw)
            val content = js.getJSONArray("content")
            (0 until content.length()).map { content.getJSONObject(it) }
                .filter { it.optString("type") == "text" }.joinToString("") { it.optString("text") } to
                (js.optString("stop_reason") == "max_tokens")
        } catch (e: Exception) {
            throw TranslateException(tr("Claude: неожиданный ответ сервера"))
        }
    }
}

/** Описание переводчика для экрана настроек. */
data class TranslatorInfo(val key: String, val title: String, val subtitle: String, val group: String)

object Translators {
    val all get() = listOf(
        TranslatorInfo("google_free", "Google", tr("Без ключа · среднее качество"), tr("Бесплатно, без ключа")),
        TranslatorInfo("yandex", tr("Яндекс"), tr("Без ключа · хорошо знает русский"), tr("Бесплатно, без ключа")),
        TranslatorInfo("duckduckgo", "DuckDuckGo", tr("Без ключа · переводит через Bing"), tr("Бесплатно, без ключа")),
        TranslatorInfo("lingva", "Lingva", tr("Без ключа · Google через посредника, выручает при VPN"), tr("Бесплатно, без ключа")),
        TranslatorInfo("offline", tr("Офлайн"), tr("Без интернета · на телефоне, языки скачиваются один раз"), tr("Бесплатно, без ключа")),
        TranslatorInfo("deepl", "DeepL", tr("Ключ · 500 тыс. символов в месяц бесплатно"), tr("С ключом")),
        TranslatorInfo("microsoft", "Microsoft", tr("Ключ · 2 млн символов в месяц бесплатно"), tr("С ключом")),
        TranslatorInfo("papago", "Papago", tr("Ключ Naver Cloud · лучший для корейского"), tr("С ключом")),
        TranslatorInfo("gemini", "Gemini", tr("ИИ · есть бесплатный лимит"), tr("ИИ — понимают контекст")),
        TranslatorInfo("claude", "Claude", tr("ИИ · естественный живой перевод"), tr("ИИ — понимают контекст")),
        TranslatorInfo("openai", tr("OpenAI-совместимый"), tr("ИИ · OpenAI, OpenRouter, DeepSeek и другие"), tr("ИИ — понимают контекст")),
    )

    fun info(key: String): TranslatorInfo = all.firstOrNull { it.key == key } ?: all[0]

    /**
     * Перевести запрос выбранным переводчиком → (переводы, кто перевёл). Нет интернета, а языки
     * скачаны — переводит офлайн-переводчик; выбранный бесплатный не ответил — другие бесплатные.
     * Для словарика: ему не нужны ни кэш, ни контекст тайтла (см. Pipeline для страницы).
     */
    fun withFallback(ctx: android.content.Context, s: Settings, req: TranslateRequest): Pair<List<String>, String> {
        val tor = create(s)
        if (tor !is OfflineTranslator && s.freeFallback && !OfflineModels.online(ctx) && OfflineModels.ready(req.src, req.tgt)) {
            val off = OfflineTranslator()
            return off.translate(req) to off.title
        }
        try {
            return tor.translate(req) to tor.title
        } catch (err: TranslateException) {
            if (s.freeFallback && err.code == Http.NO_CONNECTION && tor !is OfflineTranslator && OfflineModels.ready(req.src, req.tgt)) {
                try {
                    val off = OfflineTranslator()
                    return off.translate(req) to off.title
                } catch (_: TranslateException) {
                }
            }
            val cur = s.translator.ifBlank { "google_free" }
            if (!s.freeFallback || cur !in Pipeline.FREE_TRANSLATORS || (cur == "yandex" && s.yandexKey.isNotBlank())) throw err
            for (name in Pipeline.FREE_TRANSLATORS) {
                if (name == cur) continue
                val alt: Translator = when (name) {
                    "yandex" -> YandexTranslator("", "")
                    "lingva" -> LingvaTranslator(s.lingvaUrl)
                    "duckduckgo" -> DuckDuckGoTranslator()
                    else -> GoogleFreeTranslator()
                }
                try { return alt.translate(req) to alt.title } catch (_: TranslateException) { }
            }
            throw err
        }
    }

    fun create(s: Settings): Translator = when (s.translator) {
        "yandex" -> YandexTranslator(s.yandexKey, s.yandexFolder)
        "duckduckgo" -> DuckDuckGoTranslator()
        "lingva" -> LingvaTranslator(s.lingvaUrl)
        "deepl" -> DeepLTranslator(s.deeplKey)
        "microsoft" -> MicrosoftTranslator(s.microsoftKey, s.microsoftRegion)
        "papago" -> PapagoTranslator(s.papagoId, s.papagoSecret)
        "gemini" -> GeminiTranslator(s.geminiKey, s.geminiModel)
        "claude" -> ClaudeTranslator(s.claudeKey, s.claudeModel)
        "openai" -> OpenAICompatTranslator(s.openaiUrl, s.openaiKey, s.openaiModel)
        "offline" -> OfflineTranslator()
        else -> GoogleFreeTranslator()
    }
}
