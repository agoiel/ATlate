package com.manhwatranslator

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Intent
import android.content.pm.ServiceInfo
import android.graphics.Bitmap
import android.graphics.PixelFormat
import android.graphics.Rect
import android.graphics.drawable.Icon
import android.media.projection.MediaProjectionManager
import android.os.Build
import android.os.Handler
import android.os.IBinder
import android.os.Looper
import android.view.Gravity
import android.view.View
import android.view.WindowManager
import android.widget.Toast
import com.manhwatranslator.core.DevLog
import com.manhwatranslator.core.I18n
import com.manhwatranslator.core.Languages
import com.manhwatranslator.core.PageResult
import com.manhwatranslator.core.Timings
import com.manhwatranslator.core.tr
import com.manhwatranslator.core.trf
import com.manhwatranslator.core.Pipeline
import com.manhwatranslator.core.Settings
import java.util.concurrent.Executors

/**
 * Фоновая служба: держит захват экрана, плавающую кнопку и слой с переводом.
 *
 * Нажатие на кнопку — перевести область (или скрыть перевод, если он показан).
 * Долгое нажатие — выделить новую область.
 */
class OverlayService : Service() {

    companion object {
        const val ACTION_START = "start"
        const val ACTION_STOP = "stop"
        const val ACTION_RESET = "reset"
        const val EXTRA_CODE = "code"
        const val EXTRA_DATA = "data"
        private const val CHANNEL = "overlay"
        private const val NOTIF_ID = 1
        private const val LIVE_INTERVAL_MS = 700L

        @Volatile var running = false
            private set
    }

    private lateinit var wm: WindowManager
    private lateinit var settings: Settings
    private lateinit var pipeline: Pipeline
    private val main = Handler(Looper.getMainLooper())
    private val worker = Executors.newSingleThreadExecutor()

    private var capturer: ScreenCapturer? = null
    private var button: FloatingButton? = null
    private var buttonParams: WindowManager.LayoutParams? = null
    private var translationView: TranslationView? = null
    private var selectView: RegionSelectView? = null
    private var menu: ButtonMenu? = null
    private var debugPanel: DebugPanel? = null
    private var busy = false

    // ---- режим Live
    private var liveOn = false
    private var liveShown = false             // перевод показан и страница не двигалась
    private var prevThumb: IntArray? = null   // предыдущий кадр (уменьшенный)
    private var baseThumb: IntArray? = null   // кадр сразу после показа перевода
    private val liveTick = object : Runnable {
        override fun run() {
            liveStep()
            if (liveOn) main.postDelayed(this, LIVE_INTERVAL_MS)
        }
    }

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onCreate() {
        super.onCreate()
        wm = getSystemService(WindowManager::class.java)
        settings = Settings(this)
        I18n.init(this)
        pipeline = Pipeline(this)
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        when (intent?.action) {
            ACTION_STOP -> {
                stopEverything()
                return START_NOT_STICKY
            }
            ACTION_RESET -> {
                pipeline.reset()   // контекст перечитается из памяти при следующем переводе
                translationView?.clear()
                updateButton()
                return START_NOT_STICKY
            }
        }

        // Android 14+: служба должна стать «передней» ДО получения разрешения на запись экрана
        startForegroundCompat()

        if (capturer == null) {
            val code = intent?.getIntExtra(EXTRA_CODE, 0) ?: 0
            val data: Intent? = if (Build.VERSION.SDK_INT >= 33) {
                intent?.getParcelableExtra(EXTRA_DATA, Intent::class.java)
            } else {
                @Suppress("DEPRECATION") intent?.getParcelableExtra(EXTRA_DATA)
            }
            if (data == null) {
                toast(tr("Нет разрешения на запись экрана. Запустите заново из приложения."))
                stopEverything()
                return START_NOT_STICKY
            }
            try {
                val mpm = getSystemService(MediaProjectionManager::class.java)
                val projection = mpm.getMediaProjection(code, data)
                capturer = ScreenCapturer(this, projection) {
                    main.post {
                        toast(tr("Запись экрана остановлена. Чтобы продолжить, запустите переводчик снова."))
                        stopEverything()
                    }
                }
            } catch (e: Exception) {
                toast(trf("Не удалось начать захват экрана: %s", e.message))
                stopEverything()
                return START_NOT_STICKY
            }
        }
        if (button == null) showButton()
        running = true
        if (settings.translateMode == "live") setLive(true)
        return START_NOT_STICKY
    }

    override fun onDestroy() {
        running = false
        liveOn = false
        main.removeCallbacks(liveTick)
        removeViews()
        capturer?.stop()
        capturer = null
        worker.shutdownNow()
        pipeline.close()
        super.onDestroy()
    }

    // ---------------------------------------------------------------- уведомление
    private fun startForegroundCompat() {
        val nm = getSystemService(NotificationManager::class.java)
        if (nm.getNotificationChannel(CHANNEL) == null) {
            nm.createNotificationChannel(
                NotificationChannel(CHANNEL, tr("Переводчик поверх экрана"), NotificationManager.IMPORTANCE_LOW))
        }
        val flags = PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT
        val stopPi = PendingIntent.getService(this, 1,
            Intent(this, OverlayService::class.java).setAction(ACTION_STOP), flags)
        val openPi = PendingIntent.getActivity(this, 2, Intent(this, MainActivity::class.java), flags)
        val notif = Notification.Builder(this, CHANNEL)
            .setSmallIcon(R.drawable.ic_notification)
            .setColor(Brand.COCOA)
            .setContentTitle(tr("ATlate работает"))
            .setContentText(tr("Нажмите на кнопку «Аа», чтобы перевести область"))
            .setOngoing(true)
            .setContentIntent(openPi)
            .addAction(Notification.Action.Builder(
                Icon.createWithResource(this, R.drawable.ic_notification), tr("Остановить"), stopPi).build())
            .build()
        if (Build.VERSION.SDK_INT >= 29) {
            startForeground(NOTIF_ID, notif, ServiceInfo.FOREGROUND_SERVICE_TYPE_MEDIA_PROJECTION)
        } else {
            startForeground(NOTIF_ID, notif)
        }
    }

    // ---------------------------------------------------------------- окна поверх экрана
    private fun overlayParams(w: Int, h: Int, extraFlags: Int): WindowManager.LayoutParams {
        val p = WindowManager.LayoutParams(
            w, h,
            WindowManager.LayoutParams.TYPE_APPLICATION_OVERLAY,
            WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE or
                WindowManager.LayoutParams.FLAG_LAYOUT_IN_SCREEN or
                WindowManager.LayoutParams.FLAG_LAYOUT_NO_LIMITS or extraFlags,
            PixelFormat.TRANSLUCENT,
        )
        p.gravity = Gravity.TOP or Gravity.START
        if (Build.VERSION.SDK_INT >= 28) {
            p.layoutInDisplayCutoutMode = WindowManager.LayoutParams.LAYOUT_IN_DISPLAY_CUTOUT_MODE_SHORT_EDGES
        }
        return p
    }

    private fun showButton() {
        // Слой перевода: на весь экран, касания проходят сквозь него.
        // Android 12+ пропускает касания только через окна с прозрачностью не выше 0.8.
        val tv = TranslationView(this)
        val tp = overlayParams(WindowManager.LayoutParams.MATCH_PARENT, WindowManager.LayoutParams.MATCH_PARENT,
            WindowManager.LayoutParams.FLAG_NOT_TOUCHABLE)
        tp.alpha = 0.8f
        wm.addView(tv, tp)
        translationView = tv

        // Кнопка добавляется после слоя — значит, она поверх него
        val b = FloatingButton(this,
            onTap = { onButtonTap() },
            onLongPress = { openMenu() },
            onMove = { dx, dy ->
                buttonParams?.let { p ->
                    p.x += dx; p.y += dy
                    button?.let { wm.updateViewLayout(it, p) }
                }
            },
            onMoveEnd = {
                buttonParams?.let { settings.buttonX = it.x; settings.buttonY = it.y }
            },
        )
        val p = overlayParams(b.sizePx, b.sizePx, 0)
        p.x = settings.buttonX
        p.y = settings.buttonY
        wm.addView(b, p)
        button = b
        buttonParams = p

        if (settings.region == null) {
            toast(tr("Нажмите на кнопку, чтобы выделить область. Долгое нажатие — меню"))
        }
    }

    private fun removeViews() {
        closeMenu()
        closeDebugPanel()
        listOf<View?>(selectView, translationView, button).forEach { v ->
            if (v != null) try { wm.removeView(v) } catch (_: Exception) {}
        }
        selectView = null
        translationView = null
        button = null
    }

    private fun stopEverything() {
        running = false
        liveOn = false
        main.removeCallbacks(liveTick)
        removeViews()
        capturer?.stop()
        capturer = null
        stopForeground(STOP_FOREGROUND_REMOVE)
        stopSelf()
    }

    private fun updateButton() {
        button?.busy = busy
        button?.showingTranslation = translationView?.hasContent == true
        button?.live = if (settings.translateMode == "live") liveOn else null
    }

    // ---------------------------------------------------------------- действия
    private fun onButtonTap() {
        if (menu != null) { closeMenu(); return }
        if (settings.translateMode == "live") {
            if (settings.region == null) startSelection() else setLive(!liveOn)
            return
        }
        if (busy) return
        val tv = translationView
        if (tv != null && tv.hasContent) {
            tv.clear()
            updateButton()
            return
        }
        if (settings.region == null) startSelection() else translate()
    }

    private fun startSelection() {
        if (selectView != null || busy) return
        translationView?.clear()
        updateButton()
        val wasLive = liveOn
        if (wasLive) setLive(false)
        val v = RegionSelectView(this, settings.fullWidthSelect,
            onSelected = { r ->
                closeSelection()
                settings.region = r
                // даём экрану перерисоваться без затемнения
                if (settings.translateMode == "live") main.postDelayed({ setLive(true) }, 150)
                else main.postDelayed({ translate() }, 150)
            },
            onCancel = { closeSelection(); if (wasLive) setLive(true) },
        )
        val p = overlayParams(WindowManager.LayoutParams.MATCH_PARENT, WindowManager.LayoutParams.MATCH_PARENT, 0)
        wm.addView(v, p)
        selectView = v
        button?.visibility = View.GONE
    }

    private fun closeSelection() {
        selectView?.let { try { wm.removeView(it) } catch (_: Exception) {} }
        selectView = null
        button?.visibility = View.VISIBLE
    }

    // ---------------------------------------------------------------- меню кнопки
    private fun openMenu() {
        if (menu != null || selectView != null) return
        val live = settings.translateMode == "live"
        val items = ArrayList<Pair<String, () -> Unit>>()
        items.add(tr("Выделить новую зону") to { startSelection() })
        if (settings.region != null) items.add(tr("Показать зону") to { showZone() })
        if (translationView?.hasContent == true) items.add(tr("Убрать перевод") to { translationView?.clear(); updateButton() })
        items.add((if (live) tr("Режим: Live → Скриншот") else tr("Режим: Скриншот → Live")) to { switchMode() })
        if (settings.devMode && settings.region != null) items.add(tr("Сравнить: по очереди и параллельно") to { compareModes() })
        items.add(tr("Остановить переводчик") to { stopEverything() })

        val m = ButtonMenu(this, items) { closeMenu() }
        val p = overlayParams(WindowManager.LayoutParams.WRAP_CONTENT, WindowManager.LayoutParams.WRAP_CONTENT,
            WindowManager.LayoutParams.FLAG_WATCH_OUTSIDE_TOUCH)
        val bp = buttonParams
        val bw = button?.sizePx ?: 0
        val screenW = resources.displayMetrics.widthPixels
        val gap = dpF(8f).toInt()
        // меню — со стороны, где больше места
        val right = (bp?.x ?: 0) + bw / 2 < screenW / 2
        m.measure(View.MeasureSpec.UNSPECIFIED, View.MeasureSpec.UNSPECIFIED)
        p.x = if (right) (bp?.x ?: 0) + bw + gap else ((bp?.x ?: 0) - m.measuredWidth - gap).coerceAtLeast(gap)
        p.y = (bp?.y ?: 0).coerceAtLeast(gap)
        wm.addView(m, p)
        menu = m
    }

    private fun closeMenu() {
        menu?.let { try { wm.removeView(it) } catch (_: Exception) {} }
        menu = null
    }

    private fun showZone() {
        val r = settings.region
        if (r == null) {
            toast(tr("Зона ещё не выделена"))
            return
        }
        translationView?.showFrame(r)
    }

    private fun switchMode() {
        if (settings.translateMode == "live") {
            settings.translateMode = "shot"
            setLive(false)
            toast(tr("Режим «Скриншот»: перевод по нажатию на кнопку"))
        } else {
            settings.translateMode = "live"
            toast(tr("Режим Live: перевод появляется сам, когда страница перестаёт двигаться. Нажатие — пауза"))
            if (settings.region == null) startSelection() else setLive(true)
        }
        updateButton()
    }

    // ---------------------------------------------------------------- режим Live
    private fun setLive(on: Boolean) {
        liveOn = on
        main.removeCallbacks(liveTick)
        prevThumb = null
        baseThumb = null
        liveShown = false
        if (on) main.postDelayed(liveTick, 300)
        updateButton()
    }

    /** Уменьшенная серая копия зоны — для сравнения кадров. */
    private fun thumb(full: Bitmap, r: Rect): IntArray? {
        val rr = Rect(r)
        if (!rr.intersect(0, 0, full.width, full.height) || rr.width() < 10 || rr.height() < 10) return null
        val crop = Bitmap.createBitmap(full, rr.left, rr.top, rr.width(), rr.height())
        val w = 48
        val h = maxOf(1, w * rr.height() / rr.width())
        val small = Bitmap.createScaledBitmap(crop, w, h, true)
        if (crop !== full && crop !== small) crop.recycle()
        val px = IntArray(w * h)
        small.getPixels(px, 0, w, 0, 0, w, h)
        small.recycle()
        return IntArray(px.size) { i ->
            val c = px[i]
            (android.graphics.Color.red(c) + android.graphics.Color.green(c) + android.graphics.Color.blue(c)) / 3
        }
    }

    private fun diff(a: IntArray, b: IntArray): Float {
        if (a.size != b.size) return 255f
        var sum = 0L
        for (i in a.indices) sum += kotlin.math.abs(a[i] - b[i])
        return sum.toFloat() / a.size
    }

    /**
     * Шаг Live: пока страница двигается — перевода нет; как только два кадра подряд совпали —
     * переводим. Перевод держится, пока картинка не изменится (пользователь листает дальше).
     */
    private fun liveStep() {
        if (!liveOn || busy || selectView != null || menu != null) return
        val region = settings.region ?: return
        val full = capturer?.capture() ?: return
        val cur = thumb(full, region)
        full.recycle()
        if (cur == null) return
        if (liveShown) {
            val base = baseThumb
            if (base == null) {
                baseThumb = cur
            } else if (diff(base, cur) > 6f) {
                // страница сдвинулась — убираем старый перевод и ждём остановки
                translationView?.clear()
                liveShown = false
                baseThumb = null
                prevThumb = cur
                updateButton()
            }
            return
        }
        val prev = prevThumb
        prevThumb = cur
        if (prev != null && diff(prev, cur) < 2.5f) {
            translate(fromLive = true)
        }
    }

    private fun translate(fromLive: Boolean = false) {
        val region = settings.region ?: return startSelection()
        val cap = capturer ?: return
        if (busy) return
        busy = true
        updateButton()

        // Прячем свои окна, чтобы они не попали в снимок, и ждём новый кадр
        val timings = Timings()
        button?.visibility = View.INVISIBLE
        debugWrap?.visibility = View.INVISIBLE
        translationView?.clear()
        main.postDelayed({
            val full = cap.capture()
            button?.visibility = View.VISIBLE
            debugWrap?.visibility = View.VISIBLE
            if (full == null) {
                busy = false
                updateButton()
                toast(tr("Нет кадра экрана, попробуйте ещё раз"))
                return@postDelayed
            }
            val r = Rect(region)
            if (!r.intersect(0, 0, full.width, full.height) || r.width() < 10 || r.height() < 10) {
                busy = false
                updateButton()
                toast(tr("Область вне экрана. Выделите её заново (долгое нажатие)"))
                return@postDelayed
            }
            val crop = Bitmap.createBitmap(full, r.left, r.top, r.width(), r.height())
            if (crop !== full) full.recycle()
            timings.mark(tr("снимок"))
            worker.execute {
                val res = try {
                    pipeline.process(crop, settings, timings = timings, live = fromLive)
                } catch (e: Exception) {
                    if (settings.devMode) DevLog.append(this, android.util.Log.getStackTraceString(e))
                    PageResult(error = trf("Ошибка: %s", e.message))
                }
                crop.recycle()
                main.post { showResult(res, r, fromLive) }
            }
        }, 220)
    }

    private fun showResult(res: PageResult, region: Rect, fromLive: Boolean) {
        busy = false
        val dev = settings.devMode
        val boxes = if (dev && settings.debugBoxes) res.debug?.passes.orEmpty() else emptyList()
        translationView?.show(res.bubbles, region, settings.maxFontSp, boxes)
        if (dev) {
            val report = res.debug?.report() ?: res.error
            showDebugPanel(report, region)
            DevLog.append(this, report)
        } else {
            closeDebugPanel()
        }
        if (fromLive && liveOn) {
            // запоминаем кадр уже с переводом: дальше следим, не сдвинулась ли страница
            liveShown = true
            baseThumb = null
        }
        updateButton()
        // техническая строка — только в режиме разработчика (там есть своя панель)
        val diag = ""
        when {
            res.blankFrame -> { if (fromLive) setLive(false); toast(res.error) }
            res.error.isNotEmpty() -> toast(res.error)
            // автоопределение переключило язык — говорим об этом один раз, в момент смены
            res.detectedLang.isNotEmpty() ->
                toast(trf("Язык оригинала: %s", Languages.src(res.detectedLang).name.substringBefore(" (").lowercase()))
            fromLive -> {}   // в Live не мешаем чтению сообщениями
            res.bubbles.isEmpty() -> toast(tr("Текст не найден в выделенной области") + diag)
            res.bubbles.none { it.translation.isNotBlank() } -> toast(tr("Нечего переводить") + diag)
        }
    }

    // ---------------------------------------------------------------- режим разработчика
    private fun showDebugPanel(text: String, region: Rect) {
        var panel = debugPanel
        if (panel == null) {
            panel = DebugPanel(this,
                onCopy = { copy(it) },
                onClose = { closeDebugPanel() })
            val p = overlayParams(WindowManager.LayoutParams.MATCH_PARENT, WindowManager.LayoutParams.WRAP_CONTENT, 0)
            val m = dpF(12f).toInt()
            val screenH = resources.displayMetrics.heightPixels
            // панель — с той стороны, где меньше перевода: зона внизу — панель сверху, и наоборот
            p.gravity = (if (region.centerY() > screenH * 0.6f) Gravity.TOP else Gravity.BOTTOM) or Gravity.START
            p.horizontalMargin = 0f
            p.x = 0
            p.y = m * 4
            val wrap = android.widget.FrameLayout(this).apply {
                setPadding(m, 0, m, 0)
                addView(panel)
            }
            try {
                wm.addView(wrap, p)
            } catch (_: Exception) {
                return
            }
            debugPanel = panel
            debugWrap = wrap
        }
        panel.set(text)
    }

    private var debugWrap: View? = null

    private fun closeDebugPanel() {
        debugWrap?.let { try { wm.removeView(it) } catch (_: Exception) {} }
        debugWrap = null
        debugPanel = null
    }

    private fun copy(text: String) {
        val cm = getSystemService(android.content.ClipboardManager::class.java)
        cm?.setPrimaryClip(android.content.ClipData.newPlainText("ATlate", text))
        toast(tr("Скопировано"))
    }

    /** Распознать текущую зону по очереди и параллельно и показать, что быстрее и совпал ли результат. */
    private fun compareModes() {
        val region = settings.region ?: return
        val cap = capturer ?: return
        if (busy) return
        busy = true
        updateButton()
        button?.visibility = View.INVISIBLE
        debugWrap?.visibility = View.INVISIBLE
        translationView?.clear()
        toast(tr("Сравниваю… это займёт несколько секунд"))
        main.postDelayed({
            val full = cap.capture()
            button?.visibility = View.VISIBLE
            debugWrap?.visibility = View.VISIBLE
            val r = Rect(region)
            if (full == null || !r.intersect(0, 0, full.width, full.height) || r.width() < 10 || r.height() < 10) {
                full?.recycle()
                busy = false
                updateButton()
                toast(tr("Нет кадра экрана, попробуйте ещё раз"))
                return@postDelayed
            }
            val crop = Bitmap.createBitmap(full, r.left, r.top, r.width(), r.height())
            if (crop !== full) full.recycle()
            worker.execute {
                val text = try {
                    tr("Сравнение распознавания") + "\n" + pipeline.compare(crop, settings).report()
                } catch (e: Exception) {
                    trf("Ошибка: %s", e.message)
                }
                crop.recycle()
                DevLog.append(this, text)
                main.post {
                    busy = false
                    updateButton()
                    showDebugPanel(text, r)
                }
            }
        }, 220)
    }

    private fun toast(msg: String) {
        main.post { Toast.makeText(applicationContext, msg.take(300), Toast.LENGTH_LONG).show() }
    }
}
