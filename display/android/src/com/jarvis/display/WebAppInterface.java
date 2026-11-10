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
    private final Handler mainHandler;

    public WebAppInterface(MainActivity activity, SoundManager soundManager) {
        this.activity = activity;
        this.soundManager = soundManager;
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
