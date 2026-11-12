package com.jarvis.voice.audio

import android.annotation.SuppressLint
import android.media.AudioFormat
import android.media.AudioRecord
import android.media.MediaRecorder
import android.util.Log
import java.nio.ByteBuffer
import java.nio.ByteOrder
import java.util.concurrent.atomic.AtomicBoolean
import kotlin.math.sqrt

/**
 * Gerenciador de captura de áudio com microfone nativo do Android.
 * Captura fluxo PCM linear em 16.000 Hz, 16 bits, Mono em blocos de 1.280 bytes (40 ms).
 */
class AudioCaptureManager(
    private val onChunkReady: (chunk: ByteArray, rms: Float) -> Unit,
    private val onError: (errorMsg: String) -> Unit
) {

    private val isRecording = AtomicBoolean(false)
    private var recordThread: Thread? = null
    private var audioRecord: AudioRecord? = null

    val isCapturing: Boolean
        get() = isRecording.get()

    @SuppressLint("MissingPermission")
    fun startCapture() {
        if (isRecording.getAndSet(true)) {
            Log.d(TAG, "Captura de áudio já em andamento.")
            return
        }

        recordThread = Thread({
            android.os.Process.setThreadPriority(android.os.Process.THREAD_PRIORITY_URGENT_AUDIO)

            val minBufferSize = AudioRecord.getMinBufferSize(
                SAMPLE_RATE,
                CHANNEL_CONFIG,
                AUDIO_FORMAT
            )
            val bufferSize = maxOf(minBufferSize, CHUNK_BYTES * 4)

            // Tenta primeiro VOICE_RECOGNITION (melhor para cancelamento de ruído); fallback para MIC
            var record: AudioRecord? = null
            val audioSources = intArrayOf(
                MediaRecorder.AudioSource.VOICE_RECOGNITION,
                MediaRecorder.AudioSource.MIC
            )

            for (src in audioSources) {
                try {
                    val candidate = AudioRecord(
                        src,
                        SAMPLE_RATE,
                        CHANNEL_CONFIG,
                        AUDIO_FORMAT,
                        bufferSize
                    )
                    if (candidate.state == AudioRecord.STATE_INITIALIZED) {
                        record = candidate
                        Log.i(TAG, "AudioRecord inicializado com AudioSource: $src")
                        break
                    } else {
                        candidate.release()
                    }
                } catch (e: Exception) {
                    Log.w(TAG, "Falha ao inicializar AudioRecord com fonte $src: ${e.message}")
                }
            }

            if (record == null) {
                isRecording.set(false)
                onError("Não foi possível inicializar o microfone do dispositivo.")
                return@Thread
            }

            audioRecord = record

            try {
                record.startRecording()
                Log.i(TAG, "Gravador de áudio iniciado com sucesso (16 kHz, 16-bit Mono).")

                val chunk = ByteArray(CHUNK_BYTES)

                while (isRecording.get()) {
                    var bytesReadTotal = 0
                    while (bytesReadTotal < CHUNK_BYTES && isRecording.get()) {
                        val read = record.read(
                            chunk,
                            bytesReadTotal,
                            CHUNK_BYTES - bytesReadTotal
                        )
                        if (read > 0) {
                            bytesReadTotal += read
                        } else if (read == AudioRecord.ERROR_INVALID_OPERATION || read == AudioRecord.ERROR_BAD_VALUE) {
                            Log.e(TAG, "Erro na leitura do AudioRecord: $read")
                            break
                        }
                    }

                    if (bytesReadTotal == CHUNK_BYTES && isRecording.get()) {
                        val rms = calculateRms(chunk)
                        val copy = chunk.clone()
                        onChunkReady(copy, rms)
                    }
                }
            } catch (e: Exception) {
                Log.e(TAG, "Exceção no loop de captura de áudio", e)
                if (isRecording.get()) {
                    onError("Falha na captura de áudio: ${e.localizedMessage}")
                }
            } finally {
                try {
                    record.stop()
                    record.release()
                } catch (e: Exception) {
                    Log.w(TAG, "Exceção ao encerrar AudioRecord: ${e.message}")
                }
                audioRecord = null
                isRecording.set(false)
                Log.i(TAG, "Gravador de áudio finalizado.")
            }
        }, "JarvisAudioCaptureThread")

        recordThread?.start()
    }

    fun stopCapture() {
        isRecording.set(false)
        try {
            audioRecord?.stop()
        } catch (ignored: Exception) {}
        recordThread?.interrupt()
        recordThread = null
    }

    private fun calculateRms(pcmBytes: ByteArray): Float {
        if (pcmBytes.size < 2) return 0.0f
        val numSamples = pcmBytes.size / 2
        var sumSquares = 0.0

        val buffer = ByteBuffer.wrap(pcmBytes).order(ByteOrder.LITTLE_ENDIAN)
        for (i in 0 until numSamples) {
            val sample = buffer.short / 32768.0
            sumSquares += sample * sample
        }
        val mean = sumSquares / numSamples
        return minOf(1.0f, sqrt(mean).toFloat())
    }

    companion object {
        private const val TAG = "JarvisAudioCapture"

        const val SAMPLE_RATE = 16000
        const val CHANNEL_CONFIG = AudioFormat.CHANNEL_IN_MONO
        const val AUDIO_FORMAT = AudioFormat.ENCODING_PCM_16BIT
        const val CHUNK_SAMPLES = 640 // 40 ms @ 16 kHz
        const val CHUNK_BYTES = CHUNK_SAMPLES * 2 // 1.280 bytes
    }
}
