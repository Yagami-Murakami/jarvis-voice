package com.jarvis.display;

import android.content.Intent;
import android.os.Build;
import android.os.Handler;
import android.os.Looper;
import android.provider.Settings;
import android.view.WindowManager;
import android.webkit.JavascriptInterface;
import android.widget.Toast;

public class WebAppInterface {
    private final MainActivity activity;
    private final SoundManager soundManager;
    private final LgMicCapture micCapture;
    private final Handler mainHandler;

    public WebAppInterface(MainActivity activity, SoundManager soundManager, LgMicCapture micCapture) {
        this.activity = activity;
        this.soundManager = soundManager;
        this.micCapture = micCapture;
        this.mainHandler = new Handler(Looper.getMainLooper());
    }

    @JavascriptInterface
    public void playWakeSound() {
        if (soundManager != null) {
            soundManager.playWakeSound();
        }
    }

    @JavascriptInterface
    public void playSuccessSound() {
        if (soundManager != null) {
            soundManager.playSuccessSound();
        }
    }

    @JavascriptInterface
    public void playConfirmSound() {
        if (soundManager != null) {
            soundManager.playConfirmSound();
        }
    }

    @JavascriptInterface
    public void playCancelSound() {
        if (soundManager != null) {
            soundManager.playCancelSound();
        }
    }

    @JavascriptInterface
    public void setSoundMuted(boolean muted) {
        if (soundManager != null) {
            soundManager.setMuted(muted);
        }
    }

    @JavascriptInterface
    public boolean isSoundMuted() {
        return soundManager != null && soundManager.isMuted();
    }

    @JavascriptInterface
    public void setVolume(float volume) {
        if (soundManager != null) {
            soundManager.setVolume(volume);
        }
    }

    @JavascriptInterface
    public float getVolume() {
        return soundManager != null ? soundManager.getVolume() : 0.85f;
    }

    @JavascriptInterface
    public void setBrightness(final float brightness) {
        mainHandler.post(new BrightnessRunnable(activity, brightness));
    }

    @JavascriptInterface
    public void showToast(final String message) {
        mainHandler.post(new ToastRunnable(activity, message));
    }

    @JavascriptInterface
    public void openAndroidSettings() {
        try {
            Intent intent = new Intent(Settings.ACTION_SETTINGS);
            intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
            activity.startActivity(intent);
        } catch (Exception e) {
            e.printStackTrace();
        }
    }

    @JavascriptInterface
    public String getDeviceInfo() {
        return "Model=" + Build.MODEL + ";SDK=" + Build.VERSION.SDK_INT;
    }

    @JavascriptInterface
    public boolean hasMicPermission() {
        return activity.hasMicPermission();
    }

    @JavascriptInterface
    public void requestMicPermission() {
        activity.solicitarPermissaoMic();
    }

    @JavascriptInterface
    public boolean isMicEnabled() {
        return micCapture != null && micCapture.isMicEnabled();
    }

    @JavascriptInterface
    public void setMicEnabled(boolean enabled) {
        if (micCapture != null) {
            micCapture.setMicEnabled(enabled);
        }
    }

    @JavascriptInterface
    public boolean isMicConnected() {
        return micCapture != null && micCapture.isConnected();
    }

    @JavascriptInterface
    public float getMicRms() {
        return micCapture != null ? micCapture.getCurrentRms() : 0.0f;
    }

    @JavascriptInterface
    public boolean setPairingConfig(String token, String host, int port, boolean useTls, String fingerprint) {
        if (activity == null || micCapture == null) return false;
        try {
            activity.getSharedPreferences("JarvisDisplayPrefs", android.content.Context.MODE_PRIVATE)
                    .edit()
                    .putString("lg_mic_host", host)
                    .putInt("lg_mic_port", port > 0 ? port : LgMicCapture.DEFAULT_GATEWAY_PORT)
                    .putString("lg_mic_token", token)
                    .putBoolean("lg_mic_tls", useTls)
                    .putString("lg_mic_fingerprint", fingerprint != null ? fingerprint : "")
                    .apply();
            micCapture.setGatewayConfig(host, port > 0 ? port : LgMicCapture.DEFAULT_GATEWAY_PORT, token, useTls, fingerprint);
            return true;
        } catch (Exception e) {
            e.printStackTrace();
            return false;
        }
    }

    @JavascriptInterface
    public boolean isTlsActive() {
        return micCapture != null && micCapture.isTlsActive();
    }

    @JavascriptInterface
    public boolean hasPairingToken() {
        return micCapture != null && micCapture.hasValidToken();
    }

    @JavascriptInterface
    public String getMicDiagnostics() {
        boolean enabled = micCapture != null && micCapture.isMicEnabled();
        boolean connected = micCapture != null && micCapture.isConnected();
        boolean permission = activity.hasMicPermission();
        boolean tlsActive = micCapture != null && micCapture.isTlsActive();
        boolean hasToken = micCapture != null && micCapture.hasValidToken();
        float rms = micCapture != null ? micCapture.getCurrentRms() : 0.0f;
        long sent = micCapture != null ? micCapture.getTotalPacketsSent() : 0;
        long dropped = micCapture != null ? micCapture.getTotalPacketsDropped() : 0;

        return "{\"enabled\":" + enabled +
                ",\"connected\":" + connected +
                ",\"permission\":" + permission +
                ",\"tls_active\":" + tlsActive +
                ",\"has_token\":" + hasToken +
                ",\"rms\":" + String.format(java.util.Locale.US, "%.3f", rms) +
                ",\"sent\":" + sent +
                ",\"dropped\":" + dropped + "}";
    }

    private static class BrightnessRunnable implements Runnable {
        private final MainActivity activity;
        private final float brightness;

        BrightnessRunnable(MainActivity activity, float brightness) {
            this.activity = activity;
            this.brightness = brightness;
        }

        @Override
        public void run() {
            try {
                WindowManager.LayoutParams layout = activity.getWindow().getAttributes();
                layout.screenBrightness = Math.max(0.01f, Math.min(1.0f, brightness));
                activity.getWindow().setAttributes(layout);
            } catch (Exception e) {
                e.printStackTrace();
            }
        }
    }

    private static class ToastRunnable implements Runnable {
        private final MainActivity activity;
        private final String message;

        ToastRunnable(MainActivity activity, String message) {
            this.activity = activity;
            this.message = message;
        }

        @Override
        public void run() {
            Toast.makeText(activity, message, Toast.LENGTH_SHORT).show();
        }
    }
}
