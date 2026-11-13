package com.jarvis.voice.network

import android.os.SystemClock
import android.util.Log
import java.io.DataOutputStream
import java.net.InetSocketAddress
import java.net.Socket
import java.nio.ByteBuffer
import java.nio.ByteOrder
import java.nio.charset.StandardCharsets
import java.security.MessageDigest
import java.security.cert.CertificateException
import java.security.cert.X509Certificate
import java.util.Locale
import java.util.concurrent.ArrayBlockingQueue
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicBoolean
import javax.net.ssl.SSLContext
import javax.net.ssl.SSLSocket
import javax.net.ssl.TrustManager
import javax.net.ssl.X509TrustManager

/**
 * Cliente de streaming de áudio nativo que transmite frames PCM 16 kHz Mono
 * ao Jarvis Audio Gateway via TCP ou TLS 1.2+ autenticado por token individual.
 */
class JarvisStreamClient(
    private val onConnected: () -> Unit,
    private val onDisconnected: () -> Unit,
    private val onError: (message: String) -> Unit
) {

    private val isRunning = AtomicBoolean(false)
    private val isConnected = AtomicBoolean(false)
    private var streamThread: Thread? = null

    private val frameQueue = ArrayBlockingQueue<ByteArray>(16)
    private var sequenceNumber = 0

    val connected: Boolean
        get() = isConnected.get()

    fun start(
        host: String,
        port: Int,
        deviceId: String,
        token: String,
        useTls: Boolean,
        certFingerprint: String?
    ) {
        if (isRunning.getAndSet(true)) {
            Log.d(TAG, "Stream já em execução.")
            return
        }

        streamThread = Thread({
            android.os.Process.setThreadPriority(android.os.Process.THREAD_PRIORITY_BACKGROUND)

            while (isRunning.get() && !Thread.currentThread().isInterrupted) {
                var socket: Socket? = null
                var out: DataOutputStream? = null

                try {
                    Log.i(TAG, "Conectando ao Jarvis Gateway em $host:$port (TLS=$useTls, Device=$deviceId)...")
                    val rawSocket = Socket()
                    rawSocket.connect(InetSocketAddress(host, port), CONNECT_TIMEOUT_MS)
                    rawSocket.soTimeout = SOCKET_TIMEOUT_MS
                    rawSocket.tcpNoDelay = true
                    rawSocket.keepAlive = true

                    socket = if (useTls) {
                        wrapTlsSocket(rawSocket, host, port, certFingerprint)
                    } else {
                        rawSocket
                    }

                    out = DataOutputStream(socket.getOutputStream())

                    // 1. Handshake Versão 2 (com identificação explícita de dispositivo)
                    enviarHandshakeV2(out, deviceId, token)
                    out.flush()

                    isConnected.set(true)
                    sequenceNumber = 0
                    frameQueue.clear()
                    onConnected()
                    Log.i(TAG, "Handshake autenticado com sucesso no Gateway ($host:$port).")

                    var lastAudioSendTime = SystemClock.elapsedRealtime()
                    val silenceChunk = ByteArray(CHUNK_BYTES)

                    // 2. Loop de Envio de Frames de Áudio (DATA)
                    while (isRunning.get() && !Thread.currentThread().isInterrupted) {
                        val pcmChunk = frameQueue.poll(200, TimeUnit.MILLISECONDS)
                        if (pcmChunk != null) {
                            if (pcmChunk.size == CHUNK_BYTES) {
                                enviarFrameAudio(out, sequenceNumber++, pcmChunk, 0x00)
                                out.flush()
                                lastAudioSendTime = SystemClock.elapsedRealtime()
                            }
                        } else {
                            // Se ocioso por mais de 4s em modo PTT, transmite heartbeat silencioso
                            val now = SystemClock.elapsedRealtime()
                            if (now - lastAudioSendTime >= HEARTBEAT_INTERVAL_MS) {
                                enviarFrameAudio(out, sequenceNumber++, silenceChunk, 0x02)
                                out.flush()
                                lastAudioSendTime = now
                            }
                        }
                    }

                } catch (e: Exception) {
                    if (isRunning.get()) {
                        Log.e(TAG, "Erro no streaming TCP/TLS: ${e.message}", e)
                        onError("Conexão de áudio instável: ${e.localizedMessage ?: "Tentando reconectar..."}")
                    }
                } finally {
                    isConnected.set(false)
                    try {
                        out?.close()
                    } catch (ignored: Exception) {}
                    try {
                        socket?.close()
                    } catch (ignored: Exception) {}
                    onDisconnected()
                }

                if (isRunning.get() && !Thread.currentThread().isInterrupted) {
                    SystemClock.sleep(RECONNECT_DELAY_MS)
                }
            }
            Log.i(TAG, "Thread de streaming finalizada.")
        }, "JarvisStreamClientThread")

        streamThread?.start()
    }

    fun sendAudioChunk(pcmChunk: ByteArray) {
        if (!isConnected.get()) return
        if (!frameQueue.offer(pcmChunk)) {
            // Descarta o frame mais antigo para manter latência ultrabaixa
            frameQueue.poll()
            frameQueue.offer(pcmChunk)
        }
    }

    fun stop() {
        isRunning.set(false)
        streamThread?.interrupt()
        streamThread = null
    }

    private fun enviarHandshakeV2(out: DataOutputStream, deviceId: String, token: String) {
        val magic = MAGIC_HANDSHAKE
        val version: Byte = 2
        val devBytes = deviceId.trim().toByteArray(StandardCharsets.UTF_8)
        val devLen: Byte = devBytes.size.toByte()

        val tokenBytes = token.trim().toByteArray(StandardCharsets.UTF_8)
        val tokenLen: Short = tokenBytes.size.toShort()

        val sampleRate = 16000
        val channels: Byte = 1
        val bitDepth: Byte = 16

        val buffer = ByteBuffer.allocate(4 + 1 + 1 + devBytes.size + 2 + tokenBytes.size + 4 + 1 + 1)
        buffer.order(ByteOrder.BIG_ENDIAN)
        buffer.put(magic)
        buffer.put(version)
        buffer.put(devLen)
        buffer.put(devBytes)
        buffer.putShort(tokenLen)
        buffer.put(tokenBytes)
        buffer.putInt(sampleRate)
        buffer.put(channels)
        buffer.put(bitDepth)

        out.write(buffer.array())
    }

    private fun enviarFrameAudio(out: DataOutputStream, seq: Int, payload: ByteArray, flags: Byte = 0x00) {
        val buffer = ByteBuffer.allocate(4 + 4 + 8 + 1 + 2 + payload.size)
        buffer.order(ByteOrder.BIG_ENDIAN)
        buffer.put(MAGIC_DATA)
        buffer.putInt(seq)
        buffer.putLong(SystemClock.elapsedRealtime())
        buffer.put(flags)
        buffer.putShort(payload.size.toShort())
        buffer.put(payload)

        out.write(buffer.array())
    }

    private fun wrapTlsSocket(
        rawSocket: Socket,
        host: String,
        port: Int,
        pinnedFingerprint: String?
    ): SSLSocket {
        val sslContext = SSLContext.getInstance("TLSv1.2")
        val cleanFp = pinnedFingerprint?.trim()?.replace(":", "")?.replace(" ", "")?.lowercase(Locale.US)

        val trustManager = object : X509TrustManager {
            override fun checkClientTrusted(chain: Array<out X509Certificate>?, authType: String?) {}

            override fun checkServerTrusted(chain: Array<out X509Certificate>?, authType: String?) {
                if (chain.isNullOrEmpty()) {
                    throw CertificateException("Cadeia de certificados do servidor vazia.")
                }
                val serverCert = chain[0]
                val md = MessageDigest.getInstance("SHA-256")
                val digest = md.digest(serverCert.encoded)
                val certFp = digest.joinToString("") { "%02x".format(it) }

                if (!cleanFp.isNullOrEmpty()) {
                    if (!certFp.equals(cleanFp, ignoreCase = true)) {
                        throw CertificateException("Fingerprint do certificado divergente: esperado=$cleanFp recebido=$certFp")
                    }
                } else {
                    Log.w(TAG, "Conexão TLS em LAN sem pinning estrito. Fingerprint do certificado: $certFp")
                }
            }

            override fun getAcceptedIssuers(): Array<X509Certificate> = arrayOf()
        }
        sslContext.init(null, arrayOf<TrustManager>(trustManager), null)

        val factory = sslContext.socketFactory
        val sslSocket = factory.createSocket(rawSocket, host, port, true) as SSLSocket
        sslSocket.enabledProtocols = arrayOf("TLSv1.2", "TLSv1.3")
        sslSocket.startHandshake()
        return sslSocket
    }

    companion object {
        private const val TAG = "JarvisStreamClient"

        private val MAGIC_HANDSHAKE = byteArrayOf('J'.code.toByte(), 'M'.code.toByte(), 'I'.code.toByte(), 'C'.code.toByte())
        private val MAGIC_DATA = byteArrayOf('D'.code.toByte(), 'A'.code.toByte(), 'T'.code.toByte(), 'A'.code.toByte())

        private const val CHUNK_BYTES = 1280
        private const val CONNECT_TIMEOUT_MS = 6000
        private const val SOCKET_TIMEOUT_MS = 15000
        private const val RECONNECT_DELAY_MS = 2000L
        private const val HEARTBEAT_INTERVAL_MS = 4000L

        fun testTlsHandshake(host: String, port: Int, pinnedFingerprint: String?, timeoutMs: Int = 4000): Pair<Boolean, String> {
            return try {
                val raw = Socket()
                raw.connect(InetSocketAddress(host, port), timeoutMs)
                raw.soTimeout = timeoutMs
                val client = JarvisStreamClient({}, {}, {})
                val ssl = client.wrapTlsSocket(raw, host, port, pinnedFingerprint)
                ssl.close()
                Pair(true, "Handshake TLS 1.2+ concluído com sucesso")
            } catch (e: Exception) {
                Pair(false, e.localizedMessage ?: e.javaClass.simpleName)
            }
        }
    }
}
