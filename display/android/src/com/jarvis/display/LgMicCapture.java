package com.jarvis.display;

import android.content.Context;
import android.content.SharedPreferences;
import android.media.AudioFormat;
import android.media.AudioRecord;
import android.media.MediaRecorder;
import android.os.SystemClock;
import android.util.Log;

import java.io.DataOutputStream;
import java.io.OutputStream;
import java.net.InetSocketAddress;
import java.net.Socket;
import java.nio.charset.StandardCharsets;
import java.util.concurrent.ArrayBlockingQueue;
import java.util.concurrent.atomic.AtomicBoolean;

/**
 * Capturador e transmissor nativo de áudio para o microfone do smartphone LG-X230DSV.
 * Utiliza AudioRecord em PCM 16-bit Mono @ 16 kHz com streaming TCP autenticado e
 * proteção de eco acústico (Echo Guard) integrada ao SoundManager.
 */
public class LgMicCapture {
    private static final String TAG = "JarvisMic";

    public static final int SAMPLE_RATE = 16000;
    public static final int CHUNK_SAMPLES = 640; // 40 ms @ 16kHz
    public static final int CHUNK_BYTES = CHUNK_SAMPLES * 2; // 1280 bytes
    public static final String DEFAULT_TOKEN = "jarvis-dual-mic-2026";
    public static final int DEFAULT_GATEWAY_PORT = 8088;

    private static final byte[] MAGIC_HANDSHAKE = new byte[]{'J', 'M', 'I', 'C'};
    private static final byte[] MAGIC_DATA = new byte[]{'D', 'A', 'T', 'A'};

    private final Context context;
    private final SoundManager soundManager;
    private final SharedPreferences prefs;

    private AudioRecord audioRecord;
    private Thread captureThread;
    private Thread sendThread;

    private final AtomicBoolean isRunning = new AtomicBoolean(false);
    private final AtomicBoolean isConnected = new AtomicBoolean(false);
    private final AtomicBoolean micEnabled = new AtomicBoolean(true);

    private final ArrayBlockingQueue<AudioFrame> queue = new ArrayBlockingQueue<AudioFrame>(12);

    private String gatewayHost = "192.168.0.126";
    private int gatewayPort = DEFAULT_GATEWAY_PORT;
    private String authToken = DEFAULT_TOKEN;

    // Métricas operacionais em tempo real
    private volatile float currentRms = 0.0f;
    private volatile long totalPacketsSent = 0;
    private volatile long totalPacketsDropped = 0;
    private volatile long lastHeartbeatTime = 0;

    public static class AudioFrame {
        public final int seq;
        public final long timestamp;
        public final byte flags; // 0x01 = echo_guard, 0x02 = mic_muted
        public final byte[] data;

        public AudioFrame(int seq, long timestamp, byte flags, byte[] data) {
            this.seq = seq;
            this.timestamp = timestamp;
            this.flags = flags;
            this.data = data;
        }
    }

    public LgMicCapture(Context context, SoundManager soundManager) {
        this.context = context.getApplicationContext();
        this.soundManager = soundManager;
        this.prefs = context.getSharedPreferences("JarvisDisplayPrefs", Context.MODE_PRIVATE);
        this.micEnabled.set(prefs.getBoolean("lg_mic_enabled", true));
    }

    public synchronized void setGatewayConfig(String host, int port, String token) {
        if (host != null && !host.trim().isEmpty()) {
            this.gatewayHost = host.trim();
        }
        if (port > 0 && port < 65536) {
            this.gatewayPort = port;
        }
        if (token != null && !token.trim().isEmpty()) {
            this.authToken = token.trim();
        }
        Log.i(TAG, "Configuração do Gateway de Áudio atualizada: " + gatewayHost + ":" + gatewayPort);
    }

    public synchronized void setMicEnabled(boolean enabled) {
        this.micEnabled.set(enabled);
        prefs.edit().putBoolean("lg_mic_enabled", enabled).apply();
        Log.i(TAG, "Captação do Microfone LG " + (enabled ? "ATIVADA" : "DESATIVADA"));
    }

    public boolean isMicEnabled() {
        return micEnabled.get();
    }

    public boolean isConnected() {
        return isConnected.get();
    }

    public boolean isRunning() {
        return isRunning.get();
    }

    public float getCurrentRms() {
        return currentRms;
    }

    public long getTotalPacketsSent() {
        return totalPacketsSent;
    }

    public long getTotalPacketsDropped() {
        return totalPacketsDropped;
    }

    /**
     * Inicia as threads de captura de áudio local e streaming para o Gateway.
     */
    public synchronized void start() {
        if (isRunning.get()) {
            Log.d(TAG, "Captura já em execução.");
            return;
        }

        isRunning.set(true);

        sendThread = new Thread(new SenderWorker(this), "JarvisMic-Sender");
        sendThread.start();

        captureThread = new Thread(new CaptureWorker(this), "JarvisMic-Capture");
        captureThread.start();

        Log.i(TAG, "Serviço de Microfone Nativo LG iniciado.");
    }

    /**
     * Encerra a captura de áudio e desliga as conexões de rede.
     */
    public synchronized void stop() {
        isRunning.set(false);
        isConnected.set(false);

        if (captureThread != null) {
            captureThread.interrupt();
            captureThread = null;
        }

        if (sendThread != null) {
            sendThread.interrupt();
            sendThread = null;
        }

        if (audioRecord != null) {
            try {
                if (audioRecord.getRecordingState() == AudioRecord.RECORDSTATE_RECORDING) {
                    audioRecord.stop();
                }
                audioRecord.release();
            } catch (Exception e) {
                Log.w(TAG, "Erro ao liberar AudioRecord: " + e.getMessage());
            } finally {
                audioRecord = null;
            }
        }

        queue.clear();
        currentRms = 0.0f;
        Log.i(TAG, "Serviço de Microfone Nativo LG parado.");
    }

    /**
     * Worker responsável por ler continuamente amostras do hardware de áudio.
     */
    private static class CaptureWorker implements Runnable {
        private final LgMicCapture mic;

        CaptureWorker(LgMicCapture mic) {
            this.mic = mic;
        }

        @Override
        public void run() {
            int minBuffer = AudioRecord.getMinBufferSize(
                    SAMPLE_RATE,
                    AudioFormat.CHANNEL_IN_MONO,
                    AudioFormat.ENCODING_PCM_16BIT
            );

            if (minBuffer <= 0) {
                minBuffer = CHUNK_BYTES * 4;
            }

            int audioSource = MediaRecorder.AudioSource.VOICE_RECOGNITION;
            try {
                mic.audioRecord = new AudioRecord(
                        audioSource,
                        SAMPLE_RATE,
                        AudioFormat.CHANNEL_IN_MONO,
                        AudioFormat.ENCODING_PCM_16BIT,
                        Math.max(minBuffer, CHUNK_BYTES * 4)
                );
            } catch (Exception e) {
                Log.w(TAG, "VOICE_RECOGNITION não disponível, tentando MIC padrão: " + e.getMessage());
                try {
                    audioSource = MediaRecorder.AudioSource.MIC;
                    mic.audioRecord = new AudioRecord(
                            audioSource,
                            SAMPLE_RATE,
                            AudioFormat.CHANNEL_IN_MONO,
                            AudioFormat.ENCODING_PCM_16BIT,
                            Math.max(minBuffer, CHUNK_BYTES * 4)
                    );
                } catch (Exception e2) {
                    Log.e(TAG, "Falha fatal ao instanciar AudioRecord: " + e2.getMessage());
                    mic.isRunning.set(false);
                    return;
                }
            }

            if (mic.audioRecord.getState() != AudioRecord.STATE_INITIALIZED) {
                Log.e(TAG, "AudioRecord não pôde ser inicializado (estado = " + mic.audioRecord.getState() + ")");
                mic.isRunning.set(false);
                return;
            }

            try {
                mic.audioRecord.startRecording();
                Log.i(TAG, "Gravação iniciada (fonte=" + audioSource + ", taxa=" + SAMPLE_RATE + "Hz, mono 16-bit).");
            } catch (Exception e) {
                Log.e(TAG, "Falha ao iniciar AudioRecord: " + e.getMessage());
                mic.isRunning.set(false);
                return;
            }

            byte[] buffer = new byte[CHUNK_BYTES];
            int seq = 0;

            while (mic.isRunning.get() && !Thread.currentThread().isInterrupted()) {
                int lidos = 0;
                while (lidos < CHUNK_BYTES && mic.isRunning.get()) {
                    int r = mic.audioRecord.read(buffer, lidos, CHUNK_BYTES - lidos);
                    if (r > 0) {
                        lidos += r;
                    } else if (r == AudioRecord.ERROR_INVALID_OPERATION || r == AudioRecord.ERROR_BAD_VALUE) {
                        Log.e(TAG, "Erro de leitura AudioRecord: " + r);
                        SystemClock.sleep(20);
                        break;
                    }
                }

                if (lidos < CHUNK_BYTES) {
                    continue;
                }

                // Cálculo de RMS para diagnóstico local
                float rms = calcularRms(buffer, CHUNK_BYTES);
                mic.currentRms = rms;

                byte flags = 0;
                boolean echoGuard = (mic.soundManager != null && mic.soundManager.isEchoGuardActive());
                if (echoGuard) {
                    flags |= 0x01; // Bit 0: Echo Guard ativo
                }

                boolean muted = !mic.micEnabled.get();
                if (muted) {
                    flags |= 0x02; // Bit 1: Microfone silenciado manualmente
                }

                byte[] frameData = new byte[CHUNK_BYTES];
                if (muted || echoGuard) {
                    // Quando o alto-falante estiver tocando ou o microfone estiver mudo,
                    // envia silêncio (zeros) com flag para evitar disparos espúrios
                    java.util.Arrays.fill(frameData, (byte) 0);
                } else {
                    System.arraycopy(buffer, 0, frameData, 0, CHUNK_BYTES);
                }

                AudioFrame frame = new AudioFrame(seq++, System.currentTimeMillis(), flags, frameData);

                // Gerenciamento de fila com política de descarte de quadros antigos (manter baixa latência)
                if (!mic.queue.offer(frame)) {
                    mic.queue.poll(); // Descartar quadro mais antigo
                    mic.totalPacketsDropped++;
                    mic.queue.offer(frame);
                }
            }
        }
    }

    /**
     * Worker responsável por manter a conexão TCP com o Gateway e transmitir os frames.
     */
    private static class SenderWorker implements Runnable {
        private final LgMicCapture mic;

        SenderWorker(LgMicCapture mic) {
            this.mic = mic;
        }

        @Override
        public void run() {
            while (mic.isRunning.get() && !Thread.currentThread().isInterrupted()) {
                Socket socket = null;
                DataOutputStream out = null;

                try {
                    Log.d(TAG, "Conectando ao Gateway de Áudio em " + mic.gatewayHost + ":" + mic.gatewayPort + "...");
                    socket = new Socket();
                    socket.setTcpNoDelay(true);
                    socket.setSoTimeout(10000); // 10s de timeout
                    socket.connect(new InetSocketAddress(mic.gatewayHost, mic.gatewayPort), 4000);

                    OutputStream rawOut = socket.getOutputStream();
                    out = new DataOutputStream(rawOut);

                    // 1. Handshake inicial autenticado
                    byte[] tokenBytes = mic.authToken.getBytes(StandardCharsets.UTF_8);
                    out.write(MAGIC_HANDSHAKE); // 4 bytes: 'J','M','I','C'
                    out.writeByte(1); // Versão 1
                    out.writeShort(tokenBytes.length); // Tamanho do token
                    out.write(tokenBytes); // Token
                    out.writeInt(SAMPLE_RATE); // 16000
                    out.writeByte(1); // 1 canal (mono)
                    out.writeByte(16); // 16 bits
                    out.flush();

                    mic.isConnected.set(true);
                    Log.i(TAG, "Conexão com Gateway de Áudio estabelecida com sucesso!");

                    // 2. Loop de streaming contínuo de quadros PCM
                    while (mic.isRunning.get() && !Thread.currentThread().isInterrupted()) {
                        AudioFrame frame = mic.queue.take(); // Bloqueia até haver quadro disponível

                        out.write(MAGIC_DATA); // 4 bytes: 'D','A','T','A'
                        out.writeInt(frame.seq);
                        out.writeLong(frame.timestamp);
                        out.writeByte(frame.flags);
                        out.writeShort(frame.data.length);
                        out.write(frame.data);
                        out.flush();

                        mic.totalPacketsSent++;
                    }
                } catch (InterruptedException e) {
                    Thread.currentThread().interrupt();
                    break;
                } catch (Exception e) {
                    mic.isConnected.set(false);
                    Log.w(TAG, "Falha na conexão com Gateway de Áudio (" + e.getMessage() + "). Reconectando em 3s...");
                    SystemClock.sleep(3000);
                } finally {
                    mic.isConnected.set(false);
                    if (out != null) {
                        try {
                            out.close();
                        } catch (Exception ignored) {}
                    }
                    if (socket != null) {
                        try {
                            socket.close();
                        } catch (Exception ignored) {}
                    }
                }
            }
        }
    }

    /**
     * Calcula o nível RMS (Root Mean Square) normalizado entre 0.0 e 1.0 para amostras PCM 16-bit LE.
     */
    private static float calcularRms(byte[] pcm, int length) {
        if (pcm == null || length < 2) return 0.0f;
        int numSamples = length / 2;
        double sumSquares = 0.0;

        for (int i = 0; i < length; i += 2) {
            int low = pcm[i] & 0xff;
            int high = pcm[i + 1];
            short sample = (short) ((high << 8) | low);
            double norm = (double) sample / 32768.0;
            sumSquares += norm * norm;
        }

        double mean = sumSquares / numSamples;
        return (float) Math.min(1.0, Math.sqrt(mean));
    }
}
