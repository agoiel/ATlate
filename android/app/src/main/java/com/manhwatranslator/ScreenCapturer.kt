package com.manhwatranslator

import android.content.Context
import android.graphics.Bitmap
import android.graphics.PixelFormat
import android.graphics.Rect
import android.hardware.display.DisplayManager
import android.hardware.display.VirtualDisplay
import android.media.Image
import android.media.ImageReader
import android.media.projection.MediaProjection
import android.os.Build
import android.os.Handler
import android.os.HandlerThread
import android.util.DisplayMetrics
import android.view.WindowManager

/**
 * Захват экрана через MediaProjection.
 *
 * Виртуальный экран работает всю сессию; мы всегда держим последний кадр
 * и превращаем его в картинку только по запросу — так телефон почти не нагружается.
 */
class ScreenCapturer(
    ctx: Context,
    private val projection: MediaProjection,
    private val onStopped: () -> Unit,
) {
    val width: Int
    val height: Int
    private val thread = HandlerThread("capture").apply { start() }
    private val handler = Handler(thread.looper)
    private val reader: ImageReader
    private var display: VirtualDisplay? = null
    private val lock = Any()
    private var latest: Image? = null
    @Volatile private var released = false

    init {
        val wm = ctx.getSystemService(WindowManager::class.java)
        if (Build.VERSION.SDK_INT >= 30) {
            val b = wm.maximumWindowMetrics.bounds
            width = b.width()
            height = b.height()
        } else {
            val m = DisplayMetrics()
            @Suppress("DEPRECATION")
            wm.defaultDisplay.getRealMetrics(m)
            width = m.widthPixels
            height = m.heightPixels
        }
        val density = ctx.resources.displayMetrics.densityDpi

        reader = ImageReader.newInstance(width, height, PixelFormat.RGBA_8888, 3)
        reader.setOnImageAvailableListener({ r ->
            val img = try { r.acquireLatestImage() } catch (e: IllegalStateException) { null }
            if (img != null) {
                synchronized(lock) {
                    latest?.close()
                    latest = img
                }
            }
        }, handler)

        // На Android 14+ без колбэка createVirtualDisplay падает
        projection.registerCallback(object : MediaProjection.Callback() {
            override fun onStop() {
                release()
                onStopped()
            }
        }, handler)

        display = projection.createVirtualDisplay(
            "ATlate", width, height, density,
            DisplayManager.VIRTUAL_DISPLAY_FLAG_AUTO_MIRROR, reader.surface, null, handler,
        )
    }

    /** Последний кадр экрана целиком или null, если кадра ещё нет. */
    fun capture(): Bitmap? = synchronized(lock) {
        val img = latest ?: return null
        val plane = img.planes[0]
        val pixelStride = plane.pixelStride
        val rowStride = plane.rowStride
        val buffer = plane.buffer
        buffer.rewind()
        val padded = Bitmap.createBitmap(rowStride / pixelStride, img.height, Bitmap.Config.ARGB_8888)
        padded.copyPixelsFromBuffer(buffer)
        if (padded.width == img.width) return padded
        val out = Bitmap.createBitmap(padded, 0, 0, img.width, img.height)
        padded.recycle()
        out
    }

    /**
     * Уменьшенная серая копия участка экрана — по ней Live следит, движется ли страница.
     *
     * Точки читаются прямо из последнего кадра. Раньше ради этого каждые 0,7 с создавалась
     * картинка всего экрана (около 10 МБ на обычном телефоне), потом её вырезка и уменьшенная
     * копия — и всё это в главном потоке.
     */
    fun sampleGray(r: Rect, w: Int): IntArray? = synchronized(lock) {
        val img = latest ?: return null
        val rr = Rect(r)
        if (!rr.intersect(0, 0, img.width, img.height) || rr.width() < 10 || rr.height() < 10) return null
        val h = maxOf(1, w * rr.height() / rr.width())
        val plane = img.planes[0]
        val buf = plane.buffer
        val rowStride = plane.rowStride
        val pixelStride = plane.pixelStride
        val limit = buf.limit()
        val cellW = rr.width().toFloat() / w
        val cellH = rr.height().toFloat() / h
        val out = IntArray(w * h)
        for (j in 0 until h) for (i in 0 until w) {
            // четыре точки на клетку: одна точка слишком «шумит» на мелком тексте
            var sum = 0
            var n = 0
            for (dy in 0..1) for (dx in 0..1) {
                val x = (rr.left + (i + 0.25f + 0.5f * dx) * cellW).toInt().coerceIn(rr.left, rr.right - 1)
                val y = (rr.top + (j + 0.25f + 0.5f * dy) * cellH).toInt().coerceIn(rr.top, rr.bottom - 1)
                val p = y * rowStride + x * pixelStride
                if (p + 2 < limit) {
                    // RGBA: три байта цвета подряд
                    sum += (buf.get(p).toInt() and 0xFF) + (buf.get(p + 1).toInt() and 0xFF) +
                        (buf.get(p + 2).toInt() and 0xFF)
                    n++
                }
            }
            out[j * w + i] = if (n > 0) sum / (3 * n) else 0
        }
        out
    }

    fun release() {
        if (released) return
        released = true
        try { display?.release() } catch (_: Exception) {}
        display = null
        synchronized(lock) {
            latest?.close()
            latest = null
        }
        try { reader.close() } catch (_: Exception) {}
        thread.quitSafely()
    }

    fun stop() {
        release()
        try { projection.stop() } catch (_: Exception) {}
    }
}
