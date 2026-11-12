package com.jarvis.voice.network

import android.os.Handler
import android.os.Looper
import android.util.Log
import com.jarvis.voice.model.AnanukeMetrics
import com.jarvis.voice.model.DisplayEvent
import okhttp3.*
import okhttp3.sse.EventSource
import okhttp3.sse.EventSourceListener
import okhttp3.sse.EventSources
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicBoolean

/**
 * Cliente SSE (Server-Sent Events) nativo para recepção em tempo real de eventos,
 * comandos, confirmações e telemetria emitidos pelo servidor Jarvis.
 */
class JarvisEventClient(
    private val onEventReceived: (event: DisplayEvent) -> Unit,
    private val onMetricsReceived: (metrics: AnanukeMetrics) -> Unit,
    private val onConnectionStateChanged: (connected: Boolean) -> Unit
) {

    private val isRunning = AtomicBoolean(false)
    private val mainHandler = Handler(Looper.getMainLooper())

    private var client: OkHttpClient? = null
    private var eventSource: EventSource? = null

    fun start(host: String, displayPort: Int) {
        if (isRunning.getAndSet(true)) return

        val sseUrl = "http://$host:$displayPort/api/events"
        Log.i(TAG, "Iniciando cliente SSE em: $sseUrl")

        val okHttpClient = OkHttpClient.Builder()
            .connectTimeout(8, TimeUnit.SECONDS)
            .readTimeout(0, TimeUnit.MILLISECONDS) // Sem timeout para conexão SSE persistente
            .retryOnConnectionFailure(true)
            .build()
        this.client = okHttpClient

        val request = Request.Builder()
            .url(sseUrl)
            .header("Accept", "text/event-stream")
            .header("Cache-Control", "no-cache")
            .build()

        val sseFactory = EventSources.createFactory(okHttpClient)
        eventSource = sseFactory.newEventSource(request, object : EventSourceListener() {
            override fun onOpen(eventSource: EventSource, response: Response) {
                Log.i(TAG, "Conexão SSE estabelecida com sucesso.")
                mainHandler.post { onConnectionStateChanged(true) }
            }

            override fun onEvent(eventSource: EventSource, id: String?, type: String?, data: String) {
                val event = DisplayEvent.fromJson(data) ?: return
                mainHandler.post {
                    onEventReceived(event)

                    // Verifica se a mensagem contém métricas do Ananuke
                    val metrics = AnanukeMetrics.parseFromText(event.mensagem)
                        ?: AnanukeMetrics.parseFromText(event.detalhe)
                    if (metrics != null) {
                        onMetricsReceived(metrics)
                    }
                }
            }

            override fun onClosed(eventSource: EventSource) {
                Log.i(TAG, "Conexão SSE fechada.")
                mainHandler.post { onConnectionStateChanged(false) }
                scheduleReconnect(host, displayPort)
            }

            override fun onFailure(eventSource: EventSource, t: Throwable?, response: Response?) {
                Log.w(TAG, "Falha na conexão SSE: ${t?.message}. Tentando reconectar...")
                mainHandler.post { onConnectionStateChanged(false) }
                scheduleReconnect(host, displayPort)
            }
        })
    }

    private fun scheduleReconnect(host: String, displayPort: Int) {
        if (!isRunning.get()) return
        mainHandler.postDelayed({
            if (isRunning.get()) {
                try {
                    eventSource?.cancel()
                } catch (ignored: Exception) {}
                val request = Request.Builder()
                    .url("http://$host:$displayPort/api/events")
                    .header("Accept", "text/event-stream")
                    .build()
                client?.let {
                    val factory = EventSources.createFactory(it)
                    eventSource = factory.newEventSource(request, this.createListener(host, displayPort))
                }
            }
        }, RECONNECT_DELAY_MS)
    }

    private fun createListener(host: String, displayPort: Int): EventSourceListener {
        return object : EventSourceListener() {
            override fun onOpen(eventSource: EventSource, response: Response) {
                mainHandler.post { onConnectionStateChanged(true) }
            }

            override fun onEvent(eventSource: EventSource, id: String?, type: String?, data: String) {
                val event = DisplayEvent.fromJson(data) ?: return
                mainHandler.post {
                    onEventReceived(event)
                    val metrics = AnanukeMetrics.parseFromText(event.mensagem)
                        ?: AnanukeMetrics.parseFromText(event.detalhe)
                    if (metrics != null) {
                        onMetricsReceived(metrics)
                    }
                }
            }

            override fun onClosed(eventSource: EventSource) {
                mainHandler.post { onConnectionStateChanged(false) }
                scheduleReconnect(host, displayPort)
            }

            override fun onFailure(eventSource: EventSource, t: Throwable?, response: Response?) {
                mainHandler.post { onConnectionStateChanged(false) }
                scheduleReconnect(host, displayPort)
            }
        }
    }

    fun stop() {
        isRunning.set(false)
        try {
            eventSource?.cancel()
            client?.dispatcher?.executorService?.shutdown()
        } catch (ignored: Exception) {}
        eventSource = null
        client = null
        mainHandler.removeCallbacksAndMessages(null)
    }

    companion object {
        private const val TAG = "JarvisEventClient"
        private const val RECONNECT_DELAY_MS = 5000L
    }
}
