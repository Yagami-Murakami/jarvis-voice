package com.jarvis.display;

import android.content.Context;
import android.content.SharedPreferences;
import android.media.AudioAttributes;
import android.media.AudioFormat;
import android.media.AudioManager;
import android.media.AudioTrack;
import android.os.Build;
import android.util.Log;

public class SoundManager {
    private static final String TAG = "JarvisSound";
    private static final int SAMPLE_RATE = 44100;
    private static final String PREF_VOLUME = "sound_volume";
    private static final String PREF_MUTED = "sound_muted";

    private final Context context;
    private final SharedPreferences prefs;
    private float currentVolume = 0.85f;
    private boolean isMuted = false;
    private long lastWakeTime = 0;

    // Buffers PCM sintetizados em memória
    private byte[] pcmWake;
    private byte[] pcmSuccess;
    private byte[] pcmConfirm;
    private byte[] pcmCancel;

    public SoundManager(Context context) {
        this.context = context.getApplicationContext();
        this.prefs = context.getSharedPreferences("JarvisDisplayPrefs", Context.MODE_PRIVATE);
        this.currentVolume = prefs.getFloat(PREF_VOLUME, 0.85f);
        this.isMuted = prefs.getBoolean(PREF_MUTED, false);

        sintetizarAudios();
    }

    private void sintetizarAudios() {
        // 1. Som de Ativação ("Assistente"): tom duplo ascendente suave (587Hz -> 880Hz)
        pcmWake = gerarTomDuplo(587.33, 880.0, 90, 140);

        // 2. Som de Sucesso (WoL enviado / SSH confirmado): acorde alegre (659Hz -> 987Hz)
        pcmSuccess = gerarTomDuplo(659.25, 987.77, 80, 130);

        // 3. Som de Confirmação pendente: tom de alerta (440Hz -> 554Hz)
        pcmConfirm = gerarTomDuplo(440.0, 554.37, 100, 150);

        // 4. Som de Cancelamento / Timeout: tom descendente suave (520Hz -> 370Hz)
        pcmCancel = gerarTomDescendente(520.0, 370.0, 180);
    }

    public synchronized void playWakeSound() {
        long agora = System.currentTimeMillis();
        // Proteção contra disparos repetidos no mesmo comando (mínimo 1.5s entre ativações)
        if (agora - lastWakeTime < 1500) {
            Log.d(TAG, "playWakeSound ignorado: debounce ativo.");
            return;
        }
        lastWakeTime = agora;
        tocarPcm(pcmWake, "wake");
    }

    public void playSuccessSound() {
        tocarPcm(pcmSuccess, "success");
    }

    public void playConfirmSound() {
        tocarPcm(pcmConfirm, "confirm");
    }

    public void playCancelSound() {
        tocarPcm(pcmCancel, "cancel");
    }

    public synchronized void setVolume(float vol) {
        this.currentVolume = Math.max(0.0f, Math.min(1.0f, vol));
        prefs.edit().putFloat(PREF_VOLUME, this.currentVolume).apply();
        Log.i(TAG, "Volume ajustado para: " + this.currentVolume);
    }

    public float getVolume() {
        return this.currentVolume;
    }

    public synchronized void setMuted(boolean muted) {
        this.isMuted = muted;
        prefs.edit().putBoolean(PREF_MUTED, this.isMuted).apply();
        Log.i(TAG, "Estado de silêncio (muted): " + this.isMuted);
    }

    public boolean isMuted() {
        return this.isMuted;
    }

    private void tocarPcm(final byte[] pcmData, final String nomeSom) {
        if (isMuted || pcmData == null || pcmData.length == 0) {
            Log.d(TAG, "Som " + nomeSom + " não reproduzido (silenciado ou dados nulos).");
            return;
        }

        // Executa em thread separada para não causar atraso na UI
        new Thread(new SoundPlaybackRunnable(pcmData, currentVolume, nomeSom)).start();
    }

    /**
     * Gera dois tons sequenciais com ataque e decaimento suaves em PCM 16-bit Mono.
     */
    private byte[] gerarTomDuplo(double freq1, double freq2, int duracaoMs1, int duracaoMs2) {
        int amostras1 = (SAMPLE_RATE * duracaoMs1) / 1000;
        int amostras2 = (SAMPLE_RATE * duracaoMs2) / 1000;
        int totalAmostras = amostras1 + amostras2;

        byte[] pcm = new byte[totalAmostras * 2];
        int idx = 0;

        // Primeiro tom
        for (int i = 0; i < amostras1; i++) {
            double tempo = (double) i / SAMPLE_RATE;
            double envelope = calcularEnvelope(i, amostras1);
            double valor = Math.sin(2.0 * Math.PI * freq1 * tempo) * envelope;
            short amostraShort = (short) (valor * 32767.0 * 0.9);
            pcm[idx++] = (byte) (amostraShort & 0xff);
            pcm[idx++] = (byte) ((amostraShort >> 8) & 0xff);
        }

        // Segundo tom
        for (int i = 0; i < amostras2; i++) {
            double tempo = (double) i / SAMPLE_RATE;
            double envelope = calcularEnvelope(i, amostras2);
            double valor = Math.sin(2.0 * Math.PI * freq2 * tempo) * envelope;
            short amostraShort = (short) (valor * 32767.0 * 0.95);
            pcm[idx++] = (byte) (amostraShort & 0xff);
            pcm[idx++] = (byte) ((amostraShort >> 8) & 0xff);
        }

        return pcm;
    }

    /**
     * Gera um tom descendente com modulação de frequência contínua.
     */
    private byte[] gerarTomDescendente(double freqInicial, double freqFinal, int duracaoMs) {
        int totalAmostras = (SAMPLE_RATE * duracaoMs) / 1000;
        byte[] pcm = new byte[totalAmostras * 2];
        int idx = 0;
        double fase = 0.0;

        for (int i = 0; i < totalAmostras; i++) {
            double progresso = (double) i / totalAmostras;
            double freqAtual = freqInicial + (freqFinal - freqInicial) * progresso;
            fase += 2.0 * Math.PI * freqAtual / SAMPLE_RATE;

            double envelope = 1.0 - progresso; // decaimento linear
            double valor = Math.sin(fase) * envelope;
            short amostraShort = (short) (valor * 32767.0 * 0.85);

            pcm[idx++] = (byte) (amostraShort & 0xff);
            pcm[idx++] = (byte) ((amostraShort >> 8) & 0xff);
        }

        return pcm;
    }

    private double calcularEnvelope(int indice, int totalAmostras) {
        int ataque = Math.min(totalAmostras / 6, (SAMPLE_RATE * 15) / 1000); // 15ms de ataque
        if (indice < ataque) {
            return (double) indice / ataque;
        }
        // Decaimento exponencial suave
        double dec = (double) (totalAmostras - indice) / (totalAmostras - ataque);
        return Math.pow(dec, 1.2);
    }

    private static class SoundPlaybackRunnable implements Runnable {
        private final byte[] pcmData;
        private final float volume;
        private final String soundName;

        SoundPlaybackRunnable(byte[] pcmData, float volume, String soundName) {
            this.pcmData = pcmData;
            this.volume = volume;
            this.soundName = soundName;
        }

        @Override
        public void run() {
            AudioTrack track = null;
            try {
                int bufferSize = pcmData.length;

                if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.LOLLIPOP) {
                    AudioAttributes attributes = new AudioAttributes.Builder()
                            .setUsage(AudioAttributes.USAGE_MEDIA)
                            .setContentType(AudioAttributes.CONTENT_TYPE_SONIFICATION)
                            .build();

                    AudioFormat format = new AudioFormat.Builder()
                            .setSampleRate(SAMPLE_RATE)
                            .setEncoding(AudioFormat.ENCODING_PCM_16BIT)
                            .setChannelMask(AudioFormat.CHANNEL_OUT_MONO)
                            .build();

                    track = new AudioTrack(
                            attributes,
                            format,
                            bufferSize,
                            AudioTrack.MODE_STATIC,
                            AudioManager.AUDIO_SESSION_ID_GENERATE
                    );
                } else {
                    track = new AudioTrack(
                            AudioManager.STREAM_MUSIC,
                            SAMPLE_RATE,
                            AudioFormat.CHANNEL_OUT_MONO,
                            AudioFormat.ENCODING_PCM_16BIT,
                            bufferSize,
                            AudioTrack.MODE_STATIC
                    );
                }

                track.write(pcmData, 0, bufferSize);

                if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.LOLLIPOP) {
                    track.setVolume(volume);
                } else {
                    track.setStereoVolume(volume, volume);
                }

                Log.i(TAG, "Reproduzindo som '" + soundName + "' no alto-falante (vol=" + volume + ")...");
                track.play();

                // Aguarda o término da reprodução antes de liberar
                int duracaoMs = (bufferSize * 1000) / (SAMPLE_RATE * 2);
                Thread.sleep(duracaoMs + 60);

                track.stop();
            } catch (Exception e) {
                Log.e(TAG, "Erro na reprodução de áudio (" + soundName + "): " + e.getMessage());
            } finally {
                if (track != null) {
                    try {
                        track.release();
                    } catch (Exception ignored) {
                    }
                }
            }
        }
    }
}
