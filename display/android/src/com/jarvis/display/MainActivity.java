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
import android.text.InputType;
import android.util.Log;
import android.view.View;
import android.view.Window;
import android.view.WindowManager;
import android.webkit.WebChromeClient;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.CheckBox;
import android.widget.EditText;
import android.widget.FrameLayout;
import android.widget.LinearLayout;
import android.widget.ScrollView;
import android.widget.TextView;
import android.widget.Toast;

public class MainActivity extends Activity implements 
        View.OnLongClickListener, 
        View.OnSystemUiVisibilityChangeListener,
        DialogInterface.OnClickListener {

    private static final String TAG = "JarvisDisplay";
    private static final String PREFS_NAME = "JarvisDisplayPrefs";
    private static final String KEY_SERVER_URL = "server_url";
    private static final String DEFAULT_SERVER_URL = "http://192.168.0.126:8080";
    public static final String ACTION_PLAY_SOUND = "com.jarvis.display.ACTION_PLAY_SOUND";
    public static final String ACTION_PAIR_DEVICE = "com.jarvis.display.PAIR_DEVICE";
    private static final int REQUEST_RECORD_AUDIO = 200;

    private WebView webView;
    private SharedPreferences prefs;
    private SoundManager soundManager;
    private LgMicCapture micCapture;
    private final Handler handler = new Handler();
    private boolean isErrorState = false;

    // Campos do diálogo de configuração
    private EditText configInput;
    private EditText hostInput;
    private EditText portInput;
    private EditText tokenInput;
    private CheckBox cbTls;
    private EditText fpInput;

    private SoundReceiver soundReceiver;
    private PairingReceiver pairingReceiver;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);

        getWindow().addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);
        requestWindowFeature(Window.FEATURE_NO_TITLE);

        prefs = getSharedPreferences(PREFS_NAME, MODE_PRIVATE);
        soundManager = new SoundManager(this);
        micCapture = new LgMicCapture(this, soundManager);

        FrameLayout layout = new FrameLayout(this);
        layout.setBackgroundColor(Color.parseColor("#050b14"));

        webView = new WebView(this);
        webView.setBackgroundColor(Color.parseColor("#050b14"));
        layout.addView(webView);
        setContentView(layout);

        configurarImersao();
        configurarWebView();
        registrarReceiver();

        processarIntent(getIntent());
        atualizarGatewayHost();

        solicitarPermissaoMic();
        carregarPagina();
    }

    @Override
    protected void onNewIntent(Intent intent) {
        super.onNewIntent(intent);
        setIntent(intent);
        processarIntent(intent);
    }

    public void processarIntent(Intent intent) {
        if (intent == null) return;
        boolean recarregar = false;

        if (intent.hasExtra("server_url")) {
            String url = intent.getStringExtra("server_url");
            if (url != null && !url.trim().isEmpty()) {
                prefs.edit().putString(KEY_SERVER_URL, url.trim()).apply();
                recarregar = true;
            }
        }

        String token = intent.getStringExtra("token");
        String host = intent.getStringExtra("host");
        int port = intent.getIntExtra("port", -1);
        if (port == -1 && intent.hasExtra("port_str")) {
            try {
                port = Integer.parseInt(intent.getStringExtra("port_str"));
            } catch (Exception ignored) {}
        }
        boolean hasTlsExtra = intent.hasExtra("use_tls");
        boolean useTls = intent.getBooleanExtra("use_tls", prefs.getBoolean("lg_mic_tls", false));
        String fp = intent.getStringExtra("fingerprint");

        if (token != null || host != null || port > 0 || hasTlsExtra || fp != null) {
            String h = (host != null && !host.trim().isEmpty()) ? host.trim() : prefs.getString("lg_mic_host", "192.168.0.126");
            int p = (port > 0) ? port : prefs.getInt("lg_mic_port", LgMicCapture.DEFAULT_GATEWAY_PORT);
            String t = (token != null) ? token.trim() : prefs.getString("lg_mic_token", "");
            String f = (fp != null) ? fp.trim() : prefs.getString("lg_mic_fingerprint", "");

            prefs.edit()
                .putString("lg_mic_host", h)
                .putInt("lg_mic_port", p)
                .putString("lg_mic_token", t)
                .putBoolean("lg_mic_tls", useTls)
                .putString("lg_mic_fingerprint", f)
                .apply();

            if (micCapture != null) {
                micCapture.setGatewayConfig(h, p, t, useTls, f);
            }
            Log.i(TAG, "Configuração de pareamento atualizada: host=" + h + ":" + p + " TLS=" + useTls);
            Toast.makeText(this, "Jarvis pareado: " + h + ":" + p + " (TLS=" + useTls + ")", Toast.LENGTH_SHORT).show();
        }

        if (recarregar) {
            carregarPagina();
        }
    }

    private void registrarReceiver() {
        soundReceiver = new SoundReceiver(soundManager);
        IntentFilter filterSound = new IntentFilter(ACTION_PLAY_SOUND);
        registerReceiver(soundReceiver, filterSound);

        pairingReceiver = new PairingReceiver(this);
        IntentFilter filterPairing = new IntentFilter(ACTION_PAIR_DEVICE);
        registerReceiver(pairingReceiver, filterPairing);

        Log.i(TAG, "Receivers registrados para som e pareamento");
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
        if (pairingReceiver != null) {
            try {
                unregisterReceiver(pairingReceiver);
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
        if (webView == null) return;
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
        builder.setTitle("Configurar Jarvis");

        ScrollView scroll = new ScrollView(this);
        LinearLayout container = new LinearLayout(this);
        container.setOrientation(LinearLayout.VERTICAL);
        container.setPadding(30, 20, 30, 10);

        TextView lblUrl = new TextView(this);
        lblUrl.setText("URL do Servidor Display:");
        container.addView(lblUrl);

        configInput = new EditText(this);
        configInput.setText(prefs.getString(KEY_SERVER_URL, DEFAULT_SERVER_URL));
        container.addView(configInput);

        TextView lblHost = new TextView(this);
        lblHost.setText("Gateway Host (IP):");
        lblHost.setPadding(0, 15, 0, 0);
        container.addView(lblHost);

        hostInput = new EditText(this);
        hostInput.setText(prefs.getString("lg_mic_host", "192.168.0.126"));
        container.addView(hostInput);

        TextView lblPort = new TextView(this);
        lblPort.setText("Gateway Porta TCP:");
        lblPort.setPadding(0, 15, 0, 0);
        container.addView(lblPort);

        portInput = new EditText(this);
        portInput.setInputType(InputType.TYPE_CLASS_NUMBER);
        portInput.setText(String.valueOf(prefs.getInt("lg_mic_port", LgMicCapture.DEFAULT_GATEWAY_PORT)));
        container.addView(portInput);

        TextView lblToken = new TextView(this);
        lblToken.setText("Token de Autenticação (32+ caracteres):");
        lblToken.setPadding(0, 15, 0, 0);
        container.addView(lblToken);

        tokenInput = new EditText(this);
        tokenInput.setHint("Token gerado no servidor");
        tokenInput.setText(prefs.getString("lg_mic_token", ""));
        container.addView(tokenInput);

        cbTls = new CheckBox(this);
        cbTls.setText("Ativar Criptografia TLS 1.2+");
        cbTls.setChecked(prefs.getBoolean("lg_mic_tls", false));
        container.addView(cbTls);

        TextView lblFp = new TextView(this);
        lblFp.setText("Fingerprint SHA-256 do Certificado:");
        lblFp.setPadding(0, 15, 0, 0);
        container.addView(lblFp);

        fpInput = new EditText(this);
        fpInput.setHint("Ex: A1B2C3... (Opcional)");
        fpInput.setText(prefs.getString("lg_mic_fingerprint", ""));
        container.addView(fpInput);

        scroll.addView(container);
        builder.setView(scroll);

        builder.setPositiveButton("Salvar", this);
        builder.setNeutralButton("Config Android", this);
        builder.setNegativeButton("Cancelar", null);
        builder.show();
    }

    @Override
    public void onClick(DialogInterface dialog, int which) {
        if (which == DialogInterface.BUTTON_POSITIVE) {
            String novaUrl = (configInput != null) ? configInput.getText().toString().trim() : "";
            if (!novaUrl.isEmpty()) {
                if (!novaUrl.startsWith("http://") && !novaUrl.startsWith("https://")) {
                    novaUrl = "http://" + novaUrl;
                }
                prefs.edit().putString(KEY_SERVER_URL, novaUrl).apply();
            }

            String h = (hostInput != null) ? hostInput.getText().toString().trim() : "192.168.0.126";
            int p = LgMicCapture.DEFAULT_GATEWAY_PORT;
            if (portInput != null && !portInput.getText().toString().trim().isEmpty()) {
                try {
                    p = Integer.parseInt(portInput.getText().toString().trim());
                } catch (Exception ignored) {}
            }
            String t = (tokenInput != null) ? tokenInput.getText().toString().trim() : "";
            boolean tls = (cbTls != null) && cbTls.isChecked();
            String f = (fpInput != null) ? fpInput.getText().toString().trim() : "";

            prefs.edit()
                .putString("lg_mic_host", h)
                .putInt("lg_mic_port", p)
                .putString("lg_mic_token", t)
                .putBoolean("lg_mic_tls", tls)
                .putString("lg_mic_fingerprint", f)
                .apply();

            if (micCapture != null) {
                micCapture.setGatewayConfig(h, p, t, tls, f);
            }
            carregarPagina();
        } else if (which == DialogInterface.BUTTON_NEUTRAL) {
            new WebAppInterface(this, soundManager, micCapture).openAndroidSettings();
        }
    }

    public void atualizarGatewayHost() {
        if (micCapture == null) return;
        String url = prefs.getString(KEY_SERVER_URL, DEFAULT_SERVER_URL);
        String host = prefs.getString("lg_mic_host", "");
        if (host.isEmpty()) {
            try {
                java.net.URI uri = new java.net.URI(url);
                host = uri.getHost();
            } catch (Exception e) {
                Log.w(TAG, "Falha ao extrair host de " + url + ": " + e.getMessage());
            }
        }
        if (host == null || host.isEmpty()) {
            host = "192.168.0.126";
        }
        int port = prefs.getInt("lg_mic_port", LgMicCapture.DEFAULT_GATEWAY_PORT);
        String token = prefs.getString("lg_mic_token", "");
        boolean tls = prefs.getBoolean("lg_mic_tls", false);
        String fp = prefs.getString("lg_mic_fingerprint", "");
        micCapture.setGatewayConfig(host, port, token, tls, fp);
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

    private static class PairingReceiver extends BroadcastReceiver {
        private final MainActivity activity;

        PairingReceiver(MainActivity activity) {
            this.activity = activity;
        }

        @Override
        public void onReceive(Context context, Intent intent) {
            if (intent == null || !ACTION_PAIR_DEVICE.equals(intent.getAction())) {
                return;
            }
            Log.i(TAG, "Broadcast ACTION_PAIR_DEVICE recebido");
            activity.processarIntent(intent);
        }
    }
}
