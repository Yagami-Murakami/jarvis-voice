package com.jarvis.voice.ui

import android.graphics.Color
import android.os.Bundle
import android.text.method.PasswordTransformationMethod
import android.view.View
import android.widget.Toast
import androidx.appcompat.app.AppCompatActivity
import androidx.lifecycle.lifecycleScope
import com.jarvis.voice.databinding.ActivitySettingsBinding
import com.jarvis.voice.util.PreferencesManager
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import java.net.InetSocketAddress
import java.net.Socket
import java.net.URL

class SettingsActivity : AppCompatActivity() {

    private lateinit var binding: ActivitySettingsBinding
    private lateinit var prefs: PreferencesManager

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        binding = ActivitySettingsBinding.inflate(layoutInflater)
        setContentView(binding.root)

        prefs = PreferencesManager(this)

        // Mascarar campo de token individual para evitar exposição visual de credencial
        binding.etDeviceToken.transformationMethod = PasswordTransformationMethod.getInstance()

        carregarPreferencias()
        configurarListeners()
    }

    private fun carregarPreferencias() {
        binding.etServerHost.setText(prefs.serverHost)
        binding.etAudioPort.setText(prefs.audioPort.toString())
        binding.etDisplayPort.setText(prefs.displayPort.toString())
        binding.etDeviceId.setText(prefs.deviceId)
        binding.etDeviceToken.setText(prefs.deviceToken)
        binding.switchTls.isChecked = prefs.useTls
        binding.etCertFingerprint.setText(prefs.certFingerprint)
    }

    private fun configurarListeners() {
        binding.btnBack.setOnClickListener {
            finish()
        }

        binding.btnSaveSettings.setOnClickListener {
            salvarPreferencias()
        }

        binding.btnTestConnection.setOnClickListener {
            executarTesteConexao()
        }
    }

    private fun salvarPreferencias() {
        val host = binding.etServerHost.text.toString().trim()
        val audioPort = binding.etAudioPort.text.toString().toIntOrNull() ?: 8088
        val displayPort = binding.etDisplayPort.text.toString().toIntOrNull() ?: 8080
        val deviceId = binding.etDeviceId.text.toString().trim()
        val token = binding.etDeviceToken.text.toString().trim()
        val useTls = binding.switchTls.isChecked
        val fp = binding.etCertFingerprint.text.toString().trim()

        if (host.isEmpty()) {
            Toast.makeText(this, "Informe o endereço IP do servidor.", Toast.LENGTH_SHORT).show()
            return
        }

        if (useTls && fp.isEmpty()) {
            Toast.makeText(this, "Validação estrita de TLS ativa: informe o Fingerprint SHA-256 do certificado.", Toast.LENGTH_LONG).show()
            return
        }

        if (token.isNotEmpty() && token.length < 32) {
            Toast.makeText(this, "O token de segurança deve possuir pelo menos 32 caracteres.", Toast.LENGTH_SHORT).show()
            return
        }

        prefs.serverHost = host
        prefs.audioPort = audioPort
        prefs.displayPort = displayPort
        prefs.deviceId = if (deviceId.isNotEmpty()) deviceId else PreferencesManager.DEFAULT_DEVICE_ID
        prefs.deviceToken = token
        prefs.useTls = useTls
        prefs.certFingerprint = fp

        Toast.makeText(this, "Configurações salvas com sucesso!", Toast.LENGTH_SHORT).show()
        finish()
    }

    private fun executarTesteConexao() {
        val host = binding.etServerHost.text.toString().trim()
        val audioPort = binding.etAudioPort.text.toString().toIntOrNull() ?: 8088
        val displayPort = binding.etDisplayPort.text.toString().toIntOrNull() ?: 8080
        val useTls = binding.switchTls.isChecked
        val fp = binding.etCertFingerprint.text.toString().trim()

        binding.tvTestFeedback.visibility = View.VISIBLE
        binding.tvTestFeedback.setTextColor(Color.parseColor("#f59e0b"))
        binding.tvTestFeedback.text = "Testando conexão com $host (TLS=$useTls)..."
        binding.btnTestConnection.isEnabled = false

        lifecycleScope.launch(Dispatchers.IO) {
            var audioOk = false
            var displayOk = false
            var audioMsg = ""
            var displayMsg = ""

            // 1. Testa Socket TCP / Handshake TLS da porta de Áudio
            if (useTls) {
                val (tlsOk, msg) = com.jarvis.voice.network.JarvisStreamClient.testTlsHandshake(host, audioPort, fp, 4000)
                audioOk = tlsOk
                audioMsg = msg
            } else {
                try {
                    Socket().use { s ->
                        s.connect(InetSocketAddress(host, audioPort), 3000)
                        audioOk = true
                    }
                } catch (e: Exception) {
                    audioMsg = e.localizedMessage ?: "Inacessível"
                }
            }

            // 2. Testa HTTP Display
            try {
                val url = URL("http://$host:$displayPort/api/status")
                val conn = url.openConnection()
                conn.connectTimeout = 3000
                conn.readTimeout = 3000
                conn.getInputStream().use { it.read() }
                displayOk = true
            } catch (e: Exception) {
                displayMsg = e.localizedMessage ?: "Inacessível"
            }

            withContext(Dispatchers.Main) {
                binding.btnTestConnection.isEnabled = true
                val tlsLabel = if (useTls) "TLS 1.2+" else "TCP puro"
                if (audioOk && displayOk) {
                    binding.tvTestFeedback.setTextColor(Color.parseColor("#10b981"))
                    binding.tvTestFeedback.text = "SUCESSO: Áudio ($tlsLabel :$audioPort) e Display (HTTP :$displayPort) validados!"
                } else if (audioOk) {
                    binding.tvTestFeedback.setTextColor(Color.parseColor("#f59e0b"))
                    binding.tvTestFeedback.text = "Áudio $tlsLabel OK (:$audioPort). Display HTTP indisponível: $displayMsg"
                } else if (displayOk) {
                    binding.tvTestFeedback.setTextColor(Color.parseColor("#f59e0b"))
                    binding.tvTestFeedback.text = "Display OK (:$displayPort). Áudio indisponível: $audioMsg"
                } else {
                    binding.tvTestFeedback.setTextColor(Color.parseColor("#ef4444"))
                    binding.tvTestFeedback.text = "FALHA: Servidor não respondeu nas portas $audioPort e $displayPort ($audioMsg)."
                }
            }
        }
    }
}
