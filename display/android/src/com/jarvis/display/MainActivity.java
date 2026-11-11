package com.jarvis.display;

import android.app.Activity;
import android.app.AlertDialog;
import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.DialogInterface;
import android.content.Intent;
import android.content.IntentFilter;
import android.content.SharedPreferences;
import android.graphics.Color;
import android.os.Build;
import android.os.Bundle;
import android.os.Handler;
import android.util.Log;
import android.view.View;
import android.view.Window;
import android.view.WindowManager;
import android.webkit.WebChromeClient;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.EditText;
import android.widget.FrameLayout;

public class MainActivity extends Activity implements 
        View.OnLongClickListener, 
        View.OnSystemUiVisibilityChangeListener,
        DialogInterface.OnClickListener {

    private static final String TAG = "JarvisMainActivity";
    private static final String PREFS_NAME = "JarvisDisplayPrefs";
    private static final String KEY_SERVER_URL = "server_url";
    private static final String DEFAULT_SERVER_URL = "http://192.168.0.131:8080";
    public static final String ACTION_PLAY_SOUND = "com.jarvis.display.ACTION_PLAY_SOUND";
    private static final int REQUEST_RECORD_AUDIO = 200;

    private WebView webView;
    private SharedPreferences prefs;
    private SoundManager soundManager;
    private LgMicCapture micCapture;
    private final Handler handler = new Handler();
    private boolean isErrorState = false;
    private EditText configInput;
    private SoundReceiver soundReceiver;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);

        getWindow().addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);
        requestWindowFeature(Window.FEATURE_NO_TITLE);

        prefs = getSharedPreferences(PREFS_NAME, MODE_PRIVATE);
        soundManager = new SoundManager(this);
        micCapture = new LgMicCapture(this, soundManager);
        atualizarGatewayHost();

        FrameLayout layout = new FrameLayout(this);
        layout.setBackgroundColor(Color.parseColor("#050b14"));

        webView = new WebView(this);
        webView.setBackgroundColor(Color.parseColor("#050b14"));
        layout.addView(webView);
        setContentView(layout);

        configurarImersao();
        configurarWebView();
        registrarReceiver();

        solicitarPermissaoMic();
        carregarPagina();
    }

    private void registrarReceiver() {
        soundReceiver = new SoundReceiver(soundManager);
        IntentFilter filter = new IntentFilter(ACTION_PLAY_SOUND);
        registerReceiver(soundReceiver, filter);
        Log.i(TAG, "SoundReceiver registrado para action: " + ACTION_PLAY_SOUND);
    }

    @Override
    protected void onDestroy() {
        super.onDestroy();
        if (soundReceiver != null) {
            try {
                unregisterReceiver(soundReceiver);
            } catch (Exception ignored) {
            }
        }
        if (micCapture != null) {
            micCapture.stop();
        }
    }

    public void configurarImersao() {
        View decorView = getWindow().getDecorView();
        int uiOptions = View.SYSTEM_UI_FLAG_LAYOUT_STABLE
                | View.SYSTEM_UI_FLAG_LAYOUT_HIDE_NAVIGATION
                | View.SYSTEM_UI_FLAG_LAYOUT_FULLSCREEN
                | View.SYSTEM_UI_FLAG_HIDE_NAVIGATION
                | View.SYSTEM_UI_FLAG_FULLSCREEN
                | View.SYSTEM_UI_FLAG_IMMERSIVE_STICKY;
        decorView.setSystemUiVisibility(uiOptions);
        decorView.setOnSystemUiVisibilityChangeListener(this);
    }

    @Override
    public void onSystemUiVisibilityChange(int visibility) {
        if ((visibility & View.SYSTEM_UI_FLAG_FULLSCREEN) == 0) {
            handler.postDelayed(new ImmersiveRunnable(this), 2000);
        }
    }

    private void configurarWebView() {
        WebSettings settings = webView.getSettings();
        settings.setJavaScriptEnabled(true);
        settings.setDomStorageEnabled(true);
        settings.setDatabaseEnabled(true);
        settings.setAllowFileAccess(true);
        settings.setCacheMode(WebSettings.LOAD_DEFAULT);

        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.JELLY_BEAN_MR1) {
            settings.setMediaPlaybackRequiresUserGesture(false);
        }

        webView.setWebChromeClient(new WebChromeClient());
        webView.setWebViewClient(new CustomWebViewClient(this));
        webView.addJavascriptInterface(new WebAppInterface(this, soundManager, micCapture), "AndroidHost");
        webView.setOnLongClickListener(this);
    }

    @Override
    public boolean onLongClick(View v) {
        abrirConfiguracaoServidor();
        return true;
    }

    public void carregarPagina() {
        String url = prefs.getString(KEY_SERVER_URL, DEFAULT_SERVER_URL);
        webView.loadUrl(url);
    }

    public void onPageError(String failingUrl, String desc) {
        isErrorState = true;
        String html = "<!DOCTYPE html><html><head><meta name='viewport' content='width=device-width, initial-scale=1'>"
                + "<style>body{background:#050b14;color:#f0f6fc;font-family:sans-serif;text-align:center;padding:40px 20px;}"
                + "h2{color:#00e5ff;font-size:1.5rem;margin-bottom:10px;}"
                + "p{color:#8b9bb4;font-size:0.95rem;line-height:1.5;}"
                + ".btn{background:#0077ff;color:#fff;border:none;padding:12px 24px;border-radius:8px;font-size:1rem;margin-top:25px;cursor:pointer;}"
                + ".url{font-family:monospace;color:#ffd166;margin:15px 0;word-break:break-all;}"
                + "</style></head><body>"
                + "<h2>JARVIS VOICE DISPLAY</h2>"
                + "<p>Aguardando conexão com o servidor...</p>"
                + "<div class='url'>" + failingUrl + "</div>"
                + "<p>Tentando reconectar automaticamente a cada 5 segundos.</p>"
                + "<button class='btn' onclick='location.reload()'>Tentar Agora</button>"
                + "<br><br><button class='btn' style='background:#334155;' onclick='window.AndroidHost.openAndroidSettings()'>Configurações do Android</button>"
                + "<script>setTimeout(function(){ location.reload(); }, 5000);</script>"
                + "</body></html>";
        webView.loadDataWithBaseURL(null, html, "text/html", "utf-8", null);

        handler.postDelayed(new RetryRunnable(this), 5000);
    }

    public void checkAndRetry() {
        if (isErrorState) {
            carregarPagina();
        }
    }

    public void onPageSuccess() {
        isErrorState = false;
    }

    private void abrirConfiguracaoServidor() {
        AlertDialog.Builder builder = new AlertDialog.Builder(this);
        builder.setTitle("Configurar Jarvis Display");
        builder.setMessage("Informe a URL do Jarvis Display Server:");

        configInput = new EditText(this);
        configInput.setText(prefs.getString(KEY_SERVER_URL, DEFAULT_SERVER_URL));
        builder.setView(configInput);

        builder.setPositiveButton("Salvar", this);
        builder.setNeutralButton("Config Android", this);
        builder.setNegativeButton("Cancelar", null);
        builder.show();
    }

    @Override
    public void onClick(DialogInterface dialog, int which) {
        if (which == DialogInterface.BUTTON_POSITIVE) {
            if (configInput != null) {
                String novaUrl = configInput.getText().toString().trim();
                if (!novaUrl.isEmpty()) {
                    if (!novaUrl.startsWith("http://") && !novaUrl.startsWith("https://")) {
                        novaUrl = "http://" + novaUrl;
                    }
                    prefs.edit().putString(KEY_SERVER_URL, novaUrl).apply();
                    atualizarGatewayHost();
                    carregarPagina();
                }
            }
        } else if (which == DialogInterface.BUTTON_NEUTRAL) {
            new WebAppInterface(this, soundManager, micCapture).openAndroidSettings();
        }
    }

    public void atualizarGatewayHost() {
        if (micCapture == null) return;
        String url = prefs.getString(KEY_SERVER_URL, DEFAULT_SERVER_URL);
        try {
            java.net.URI uri = new java.net.URI(url);
            String host = uri.getHost();
            if (host != null && !host.isEmpty()) {
                micCapture.setGatewayConfig(host, LgMicCapture.DEFAULT_GATEWAY_PORT, LgMicCapture.DEFAULT_TOKEN);
            }
        } catch (Exception e) {
            Log.w(TAG, "Falha ao extrair host de " + url + ": " + e.getMessage());
        }
    }

    public boolean hasMicPermission() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.M) {
            return checkSelfPermission(android.Manifest.permission.RECORD_AUDIO) == android.content.pm.PackageManager.PERMISSION_GRANTED;
        }
        return true;
    }

    public void solicitarPermissaoMic() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.M) {
            if (!hasMicPermission()) {
                Log.i(TAG, "Solicitando permissão RECORD_AUDIO em tempo de execução...");
                requestPermissions(new String[]{android.Manifest.permission.RECORD_AUDIO}, REQUEST_RECORD_AUDIO);
                return;
            }
        }
        iniciarMicSePermitido();
    }

    private void iniciarMicSePermitido() {
        if (hasMicPermission() && micCapture != null) {
            micCapture.start();
        }
    }

    @Override
    public void onRequestPermissionsResult(int requestCode, String[] permissions, int[] grantResults) {
        super.onRequestPermissionsResult(requestCode, permissions, grantResults);
        if (requestCode == REQUEST_RECORD_AUDIO) {
            if (grantResults.length > 0 && grantResults[0] == android.content.pm.PackageManager.PERMISSION_GRANTED) {
                Log.i(TAG, "Permissão RECORD_AUDIO concedida pelo usuário.");
                iniciarMicSePermitido();
            } else {
                Log.w(TAG, "Permissão RECORD_AUDIO negada pelo usuário.");
            }
        }
    }



    @Override
    public void onBackPressed() {
        // Bloqueia botão voltar para o modo quiosque
    }

    @Override
    protected void onResume() {
        super.onResume();
        configurarImersao();
    }

    private static class CustomWebViewClient extends WebViewClient {
        private final MainActivity activity;

        CustomWebViewClient(MainActivity activity) {
            this.activity = activity;
        }

        @Override
        public void onReceivedError(WebView view, int errorCode, String description, String failingUrl) {
            activity.onPageError(failingUrl, description);
        }

        @Override
        public void onPageFinished(WebView view, String url) {
            activity.onPageSuccess();
        }
    }

    private static class ImmersiveRunnable implements Runnable {
        private final MainActivity activity;

        ImmersiveRunnable(MainActivity activity) {
            this.activity = activity;
        }

        @Override
        public void run() {
            activity.configurarImersao();
        }
    }

    private static class RetryRunnable implements Runnable {
        private final MainActivity activity;

        RetryRunnable(MainActivity activity) {
            this.activity = activity;
        }

        @Override
        public void run() {
            activity.checkAndRetry();
        }
    }

    private static class SoundReceiver extends BroadcastReceiver {
        private final SoundManager soundManager;

        SoundReceiver(SoundManager soundManager) {
            this.soundManager = soundManager;
        }

        @Override
        public void onReceive(Context context, Intent intent) {
            if (intent == null || !ACTION_PLAY_SOUND.equals(intent.getAction())) {
                return;
            }

            if (intent.hasExtra("volume")) {
                float vol = intent.getFloatExtra("volume", 0.85f);
                soundManager.setVolume(vol);
            }

            if (intent.hasExtra("mute")) {
                boolean mute = intent.getBooleanExtra("mute", false);
                soundManager.setMuted(mute);
            }

            String type = intent.getStringExtra("type");
            if (type == null) {
                type = "wake";
            }

            Log.i(TAG, "Broadcast recebido para reproduzir som: " + type);
            if ("wake".equalsIgnoreCase(type)) {
                soundManager.playWakeSound();
            } else if ("success".equalsIgnoreCase(type)) {
                soundManager.playSuccessSound();
            } else if ("confirm".equalsIgnoreCase(type)) {
                soundManager.playConfirmSound();
            } else if ("cancel".equalsIgnoreCase(type) || "timeout".equalsIgnoreCase(type)) {
                soundManager.playCancelSound();
            }
        }
    }
}
