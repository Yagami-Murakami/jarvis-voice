package com.jarvis.voice

import android.Manifest
import android.animation.ObjectAnimator
import android.content.Intent
import android.content.pm.PackageManager
import android.content.res.ColorStateList
import android.graphics.Color
import android.os.Build
import android.os.Bundle
import android.view.MotionEvent
import android.view.View
import android.view.animation.AccelerateDecelerateInterpolator
import android.widget.Toast
import androidx.appcompat.app.AppCompatActivity
import androidx.core.app.ActivityCompat
import androidx.core.content.ContextCompat
import com.jarvis.voice.audio.AudioCaptureManager
import com.jarvis.voice.databinding.ActivityMainBinding
import com.jarvis.voice.model.AnanukeMetrics
import com.jarvis.voice.model.DisplayEvent
import com.jarvis.voice.network.JarvisEventClient
import com.jarvis.voice.network.JarvisStreamClient
import com.jarvis.voice.ui.SettingsActivity
import com.jarvis.voice.util.PreferencesManager

class MainActivity : AppCompatActivity() {

    private lateinit var binding: ActivityMainBinding
    private lateinit var prefs: PreferencesManager

    private var audioCapture: AudioCaptureManager? = null
    private var streamClient: JarvisStreamClient? = null
    private var eventClient: JarvisEventClient? = null

    private var pulseAnimator: ObjectAnimator? = null
    private var isPttPressed = false

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        binding = ActivityMainBinding.inflate(layoutInflater)
        setContentView(binding.root)

        prefs = PreferencesManager(this)

        configurarUi()
        configurarAnimacaoReator()
        if (!temPermissaoMicrofone()) {
            solicitarPermissaoMicrofone()
        }
    }

    override fun onResume() {
        super.onResume()
        iniciarServicos()
    }

    override fun onPause() {
        super.onPause()
        pararServicos()
    }

    private fun configurarUi() {
        binding.btnSettings.setOnClickListener {
            startActivity(Intent(this, SettingsActivity::class.java))
        }

        binding.switchContinuous.isChecked = prefs.continuousListen
        binding.switchContinuous.setOnCheckedChangeListener { _, isChecked ->
            prefs.continuousListen = isChecked
            if (isChecked) {
                if (temPermissaoMicrofone()) {
                    audioCapture?.startCapture()
                    binding.tvPttHint.text = "Escuta contínua ativa: diga 'Assistente'"
                } else {
                    binding.switchContinuous.isChecked = false
                    solicitarPermissaoMicrofone()
                }
            } else {
                if (!isPttPressed) {
                    audioCapture?.stopCapture()
                    binding.tvPttHint.text = getString(R.string.ptt_hint_press)
                }
            }
        }

        // Configuração do botão Push-To-Talk (Touch Listener)
        binding.btnPtt.setOnTouchListener { _, event ->
            when (event.action) {
                MotionEvent.ACTION_DOWN -> {
                    isPttPressed = true
                    onPttPressionado()
                    true
                }
                MotionEvent.ACTION_UP, MotionEvent.ACTION_CANCEL -> {
                    isPttPressed = false
                    onPttLiberado()
                    true
                }
                else -> false
            }
        }
    }

    private fun configurarAnimacaoReator() {
        pulseAnimator = ObjectAnimator.ofFloat(binding.viewReactorRing, "scaleX", 1.0f, 1.12f, 1.0f).apply {
            duration = 1800
            repeatCount = ObjectAnimator.INFINITE
            interpolator = AccelerateDecelerateInterpolator()
        }
        ObjectAnimator.ofFloat(binding.viewReactorRing, "scaleY", 1.0f, 1.12f, 1.0f).apply {
            duration = 1800
            repeatCount = ObjectAnimator.INFINITE
            interpolator = AccelerateDecelerateInterpolator()
            start()
        }
        pulseAnimator?.start()
    }

    private fun onPttPressionado() {
        if (!temPermissaoMicrofone()) {
            solicitarPermissaoMicrofone()
            return
        }

        binding.btnPtt.animate().scaleX(0.92f).scaleY(0.92f).setDuration(120).start()
        binding.tvStateDescription.text = getString(R.string.state_listening)
        binding.tvStateDescription.setTextColor(ContextCompat.getColor(this, R.color.jarvis_amber))
        binding.tvPttHint.text = getString(R.string.ptt_hint_recording)
        binding.viewReactorRing.backgroundTintList = ColorStateList.valueOf(ContextCompat.getColor(this, R.color.jarvis_amber))

        audioCapture?.startCapture()
    }

    private fun onPttLiberado() {
        binding.btnPtt.animate().scaleX(1.0f).scaleY(1.0f).setDuration(150).start()

        if (!prefs.continuousListen) {
            audioCapture?.stopCapture()
            binding.tvPttHint.text = getString(R.string.ptt_hint_press)
        } else {
            binding.tvPttHint.text = "Escuta contínua ativa: diga 'Assistente'"
        }

        binding.tvStateDescription.text = getString(R.string.state_processing)
        binding.tvStateDescription.setTextColor(ContextCompat.getColor(this, R.color.jarvis_purple))
        binding.progressRms.progress = 0
    }

    private fun iniciarServicos() {
        val host = prefs.serverHost
        val audioPort = prefs.audioPort
        val displayPort = prefs.displayPort
        val deviceId = prefs.deviceId
        val token = prefs.deviceToken
        val useTls = prefs.useTls
        val fp = prefs.certFingerprint

        // 1. Cliente de Streaming TCP/TLS
        streamClient = JarvisStreamClient(
            onConnected = {
                runOnUiThread {
                    binding.dotStatus.backgroundTintList = ColorStateList.valueOf(ContextCompat.getColor(this, R.color.jarvis_emerald))
                    binding.tvConnectionStatus.text = getString(R.string.status_connected)
                    binding.tvConnectionStatus.setTextColor(ContextCompat.getColor(this, R.color.jarvis_cyan))
                }
            },
            onDisconnected = {
                runOnUiThread {
                    binding.dotStatus.backgroundTintList = ColorStateList.valueOf(ContextCompat.getColor(this, R.color.jarvis_red))
                    binding.tvConnectionStatus.text = getString(R.string.status_disconnected)
                    binding.tvConnectionStatus.setTextColor(ContextCompat.getColor(this, R.color.jarvis_red))
                }
            },
            onError = { msg ->
                runOnUiThread {
                    binding.tvErrorMessage.visibility = View.VISIBLE
                    binding.tvErrorMessage.text = msg
                }
            }
        )
        streamClient?.start(host, audioPort, deviceId, token, useTls, fp)

        // 2. Gerenciador de Áudio Nativo
        audioCapture = AudioCaptureManager(
            onChunkReady = { chunk, rms ->
                runOnUiThread {
                    binding.progressRms.progress = (rms * 100).toInt().coerceIn(0, 100)
                }
                streamClient?.sendAudioChunk(chunk)
            },
            onError = { err ->
                runOnUiThread {
                    Toast.makeText(this, err, Toast.LENGTH_SHORT).show()
                }
            }
        )

        // Se a escuta contínua estiver ativada, inicia a gravação no foco
        if (prefs.continuousListen && temPermissaoMicrofone()) {
            audioCapture?.startCapture()
            binding.tvPttHint.text = "Escuta contínua ativa: diga 'Assistente'"
        }

        // 3. Cliente SSE de Eventos e Telemetria
        eventClient = JarvisEventClient(
            onEventReceived = { event ->
                atualizarEventoNaUi(event)
            },
            onMetricsReceived = { metrics ->
                atualizarTelemetriaNaUi(metrics)
            },
            onConnectionStateChanged = { connected ->
                if (connected) {
                    binding.dotStatus.backgroundTintList = ColorStateList.valueOf(ContextCompat.getColor(this, R.color.jarvis_emerald))
                    binding.tvConnectionStatus.text = getString(R.string.status_connected)
                }
            }
        )
        eventClient?.start(host, displayPort)
    }

    private fun pararServicos() {
        audioCapture?.stopCapture()
        audioCapture = null

        streamClient?.stop()
        streamClient = null

        eventClient?.stop()
        eventClient = null
    }

    private fun atualizarEventoNaUi(event: DisplayEvent) {
        val colorRes = when (event.status) {
            "listening" -> R.color.status_listening
            "processing" -> R.color.status_processing
            "confirming" -> R.color.status_confirming
            "success" -> R.color.status_success
            "error", "offline" -> R.color.status_error
            else -> R.color.status_idle
        }
        val color = ContextCompat.getColor(this, colorRes)

        binding.tvStateDescription.text = event.mensagem.ifEmpty { getString(R.string.state_idle) }
        binding.tvStateDescription.setTextColor(color)
        binding.viewReactorRing.backgroundTintList = ColorStateList.valueOf(color)

        // Atualiza Card de Último Comando
        if (event.mensagem.isNotEmpty() && event.status != "idle") {
            binding.tvLastCommandText.text = event.mensagem
            binding.tvTargetMachine.text = event.alvo ?: "--"
            binding.tvExecutionResult.text = event.detalhe.ifEmpty { "Status: ${event.status}" }

            if (event.status == "error") {
                binding.tvErrorMessage.visibility = View.VISIBLE
                binding.tvErrorMessage.text = event.detalhe.ifEmpty { "Ocorreu um erro no processamento do comando." }
            } else {
                binding.tvErrorMessage.visibility = View.GONE
            }
        }
    }

    private fun atualizarTelemetriaNaUi(metrics: AnanukeMetrics) {
        binding.tvCpuValue.text = "${metrics.cpuPercent}%"
        binding.tvRamValue.text = "${metrics.ramPercent}%"
        binding.tvUptimeValue.text = metrics.uptime

        val cpuColor = if (metrics.cpuPercent > 80) Color.parseColor("#ef4444") else Color.parseColor("#f8fafc")
        binding.tvCpuValue.setTextColor(cpuColor)

        val ramColor = if (metrics.ramPercent > 85) Color.parseColor("#ef4444") else Color.parseColor("#f8fafc")
        binding.tvRamValue.setTextColor(ramColor)
    }

    private fun temPermissaoMicrofone(): Boolean {
        return ContextCompat.checkSelfPermission(
            this,
            Manifest.permission.RECORD_AUDIO
        ) == PackageManager.PERMISSION_GRANTED
    }

    private fun solicitarPermissaoMicrofone() {
        ActivityCompat.requestPermissions(
            this,
            arrayOf(Manifest.permission.RECORD_AUDIO),
            REQUEST_CODE_RECORD_AUDIO
        )
    }

    override fun onRequestPermissionsResult(
        requestCode: Int,
        permissions: Array<out String>,
        grantResults: IntArray
    ) {
        super.onRequestPermissionsResult(requestCode, permissions, grantResults)
        if (requestCode == REQUEST_CODE_RECORD_AUDIO) {
            if (grantResults.isNotEmpty() && grantResults[0] == PackageManager.PERMISSION_GRANTED) {
                Toast.makeText(this, "Permissão de microfone concedida.", Toast.LENGTH_SHORT).show()
                if (prefs.continuousListen) {
                    audioCapture?.startCapture()
                }
            } else {
                Toast.makeText(this, "Permissão de gravação de áudio necessária para usar o Jarvis.", Toast.LENGTH_LONG).show()
                binding.switchContinuous.isChecked = false
            }
        }
    }

    companion object {
        private const val REQUEST_CODE_RECORD_AUDIO = 1001
    }
}
