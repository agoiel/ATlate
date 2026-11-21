package com.manhwatranslator.core

import android.content.Context
import android.net.ConnectivityManager
import android.net.NetworkCapabilities
import com.google.android.gms.tasks.Task
import com.google.android.gms.tasks.Tasks
import com.google.mlkit.common.model.DownloadConditions
import com.google.mlkit.common.model.RemoteModelManager
import com.google.mlkit.nl.languageid.LanguageIdentification
import com.google.mlkit.nl.translate.TranslateLanguage
import com.google.mlkit.nl.translate.TranslateRemoteModel
import com.google.mlkit.nl.translate.Translation
import com.google.mlkit.nl.translate.TranslatorOptions
import java.util.concurrent.TimeUnit

/**
 * Перевод без интернета: Google ML Kit переводит прямо на телефоне. Языки скачиваются один раз
 * (около 30 МБ каждый), дальше всё работает в поездке, в метро, без VPN и без лимитов.
 * Качество — как у простого машинного перевода; имена из глоссария подставляются, как у других
 * обычных переводчиков.
 */
object OfflineModels {
    /** Наш код языка → код ML Kit; null — офлайн этот язык не переводится. */
    fun code(our: String): String? = when (our) {
        "zh-tw" -> TranslateLanguage.CHINESE          // одна модель для китайского
        "fil" -> TranslateLanguage.TAGALOG
        Languages.LATIN, Languages.AUTO -> null
        else -> TranslateLanguage.fromLanguageTag(our)
    }

    private fun model(code: String) = TranslateRemoteModel.Builder(code).build()

    /** Скачан ли язык (вызывать не из главного потока). */
    fun isDownloaded(code: String): Boolean = try {
        Tasks.await(RemoteModelManager.getInstance().isModelDownloaded(model(code)), 10, TimeUnit.SECONDS)
    } catch (_: Exception) {
        false
    }

    /** Какие языки уже скачаны (коды ML Kit); ответ приходит в главный поток. */
    fun downloaded(done: (Set<String>) -> Unit) {
        RemoteModelManager.getInstance().getDownloadedModels(TranslateRemoteModel::class.java)
            .addOnSuccessListener { models -> done(models.map { it.language }.toSet()) }
            .addOnFailureListener { done(emptySet()) }
    }

    /** Скачать языки; ответ (null — успех, иначе текст ошибки) приходит в главный поток. */
    fun download(codes: Collection<String>, done: (String?) -> Unit) {
        val tasks: List<Task<Void>> = codes.distinct().map {
            RemoteModelManager.getInstance().download(model(it), DownloadConditions.Builder().build())
        }
        Tasks.whenAll(tasks)
            .addOnSuccessListener { done(null) }
            .addOnFailureListener { e -> done(e.message ?: e.javaClass.simpleName) }
    }

    /** Удалить скачанные языки (кроме английского — он встроен). */
    fun deleteAll(done: () -> Unit) {
        downloaded { codes ->
            val tasks = codes.filter { it != TranslateLanguage.ENGLISH }
                .map { RemoteModelManager.getInstance().deleteDownloadedModel(model(it)) }
            Tasks.whenAllComplete(tasks).addOnCompleteListener { done() }
        }
    }

    /** Готов ли офлайн-перевод этой пары языков прямо сейчас (не из главного потока). */
    fun ready(src: SourceLang, tgt: TargetLang): Boolean {
        val s = code(src.code) ?: return false
        val t = code(tgt.code) ?: return false
        return isDownloaded(s) && isDownloaded(t)
    }

    /** Есть ли сейчас интернет. */
    fun online(ctx: Context): Boolean = try {
        val cm = ctx.getSystemService(Context.CONNECTIVITY_SERVICE) as ConnectivityManager
        val caps = cm.getNetworkCapabilities(cm.activeNetwork)
        caps != null && caps.hasCapability(NetworkCapabilities.NET_CAPABILITY_INTERNET)
    } catch (_: Exception) {
        true        // не удалось узнать — считаем, что есть, и пусть решает обычный переводчик
    }
}

class OfflineTranslator : Translator {
    override val title = tr("Офлайн (на телефоне)")

    override fun translate(req: TranslateRequest): List<String> {
        val texts = req.anchored ?: req.texts
        val tgt = OfflineModels.code(req.tgt.code) ?: throw TranslateException(
            trf("Офлайн-переводчик не переводит на язык «%s». Выберите другой язык или переводчик.", req.tgt.name))
        val src = if (req.src.code == Languages.LATIN) identify(texts) else OfflineModels.code(req.src.code)
        if (src == null) throw TranslateException(
            trf("Офлайн-переводчик не переводит с языка «%s». Выберите другой язык или переводчик.", req.src.name))
        if (req.src.code == Languages.LATIN) req.detected = src
        if (src == tgt) return texts
        val missing = listOf(src, tgt).filter { !OfflineModels.isDownloaded(it) }
        if (missing.isNotEmpty()) throw TranslateException(
            trf("Для перевода без интернета нужно один раз скачать языки (%s): «Переводчик» → «Офлайн» → «Скачать языки».",
                missing.joinToString(", ")), NOT_DOWNLOADED)
        val client = Translation.getClient(TranslatorOptions.Builder().setSourceLanguage(src).setTargetLanguage(tgt).build())
        try {
            return texts.map { t ->
                if (t.isBlank()) "" else try {
                    Tasks.await(client.translate(t), 30, TimeUnit.SECONDS)
                } catch (e: Exception) {
                    throw TranslateException(trf("Офлайн-переводчик: %s", e.cause?.message ?: e.message ?: e.javaClass.simpleName))
                }
            }
        } finally {
            client.close()
        }
    }

    /** Язык текста на латинице (для «Латиница — язык определит переводчик»). */
    private fun identify(texts: List<String>): String? = try {
        val client = LanguageIdentification.getClient()
        try {
            val tag = Tasks.await(client.identifyLanguage(texts.joinToString(" ")), 10, TimeUnit.SECONDS)
            if (tag == "und") TranslateLanguage.ENGLISH else TranslateLanguage.fromLanguageTag(tag)
        } finally {
            client.close()
        }
    } catch (_: Exception) {
        TranslateLanguage.ENGLISH
    }

    companion object {
        /** Код ошибки «языки не скачаны» — запасным переводчиком такую ошибку не подменяем. */
        const val NOT_DOWNLOADED = -10
    }
}
