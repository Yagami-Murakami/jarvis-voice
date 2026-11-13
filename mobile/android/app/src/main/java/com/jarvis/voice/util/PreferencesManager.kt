package com.jarvis.voice.util

import android.content.Context
import android.content.SharedPreferences

/**
 * Gerenciador centralizado de preferências locais do Jarvis Voice Mobile.
 * Protege tokens de pareamento no armazenamento privado do aplicativo (MODE_PRIVATE).
 */
class PreferencesManager(context: Context) {

    private val prefs: SharedPreferences =
        context.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)

    var serverHost: String
        get() = prefs.getString(KEY_SERVER_HOST, DEFAULT_SERVER_HOST) ?: DEFAULT_SERVER_HOST
        set(value) = prefs.edit().putString(KEY_SERVER_HOST, value.trim()).apply()

    var audioPort: Int
        get() = prefs.getInt(KEY_AUDIO_PORT, DEFAULT_AUDIO_PORT)
        set(value) = prefs.edit().putInt(KEY_AUDIO_PORT, value).apply()

    var displayPort: Int
        get() = prefs.getInt(KEY_DISPLAY_PORT, DEFAULT_DISPLAY_PORT)
        set(value) = prefs.edit().putInt(KEY_DISPLAY_PORT, value).apply()

    var deviceId: String
        get() = prefs.getString(KEY_DEVICE_ID, DEFAULT_DEVICE_ID) ?: DEFAULT_DEVICE_ID
        set(value) = prefs.edit().putString(KEY_DEVICE_ID, value.trim()).apply()

    var deviceToken: String
        get() = prefs.getString(KEY_DEVICE_TOKEN, "") ?: ""
        set(value) = prefs.edit().putString(KEY_DEVICE_TOKEN, value.trim()).apply()

    var useTls: Boolean
        get() = prefs.getBoolean(KEY_USE_TLS, true)
        set(value) = prefs.edit().putBoolean(KEY_USE_TLS, value).apply()

    var certFingerprint: String
        get() {
            val v = prefs.getString(KEY_CERT_FINGERPRINT, DEFAULT_CERT_FINGERPRINT)
            return if (v.isNullOrBlank()) DEFAULT_CERT_FINGERPRINT else v.trim()
        }
        set(value) {
            val sanitized = if (value.isBlank()) DEFAULT_CERT_FINGERPRINT else value.trim()
            prefs.edit().putString(KEY_CERT_FINGERPRINT, sanitized).apply()
        }

    var continuousListen: Boolean
        get() = prefs.getBoolean(KEY_CONTINUOUS_LISTEN, false)
        set(value) = prefs.edit().putBoolean(KEY_CONTINUOUS_LISTEN, value).apply()

    companion object {
        private const val PREFS_NAME = "jarvis_mobile_prefs"

        private const val KEY_SERVER_HOST = "server_host"
        private const val KEY_AUDIO_PORT = "audio_port"
        private const val KEY_DISPLAY_PORT = "display_port"
        private const val KEY_DEVICE_ID = "device_id"
        private const val KEY_DEVICE_TOKEN = "device_token"
        private const val KEY_USE_TLS = "use_tls"
        private const val KEY_CERT_FINGERPRINT = "cert_fingerprint"
        private const val KEY_CONTINUOUS_LISTEN = "continuous_listen"

        const val DEFAULT_SERVER_HOST = "192.168.0.126"
        const val DEFAULT_AUDIO_PORT = 8088
        const val DEFAULT_DISPLAY_PORT = 8080
        const val DEFAULT_DEVICE_ID = "celular_mobile"
        const val DEFAULT_CERT_FINGERPRINT = "16D3EAD61DDB0C569275F3F68991798EC936D337378DCBEA73667519B632EE5C"
    }
}
