package com.jarvis.voice.model

import org.json.JSONObject

/**
 * Evento recebido em tempo real via Server-Sent Events (SSE) do Jarvis Display.
 */
data class DisplayEvent(
    val id: Long,
    val status: String, // idle, listening, processing, confirming, success, error, cancelled, timeout, offline
    val titulo: String,
    val mensagem: String,
    val detalhe: String,
    val alvo: String?,
    val acao: String?,
    val timestamp: Double
) {
    companion object {
        fun fromJson(jsonStr: String): DisplayEvent? {
            return try {
                val obj = JSONObject(jsonStr)
                DisplayEvent(
                    id = obj.optLong("id", 0L),
                    status = obj.optString("status", "idle"),
                    titulo = obj.optString("titulo", "JARVIS VOICE"),
                    mensagem = obj.optString("mensagem", ""),
                    detalhe = obj.optString("detalhe", ""),
                    alvo = if (obj.has("alvo") && !obj.isNull("alvo")) obj.optString("alvo") else null,
                    acao = if (obj.has("acao") && !obj.isNull("acao")) obj.optString("acao") else null,
                    timestamp = obj.optDouble("timestamp", 0.0)
                )
            } catch (e: Exception) {
                null
            }
        }
    }
}

/**
 * Métricas de hardware do computador Ananuke (Debian 13).
 */
data class AnanukeMetrics(
    val cpuPercent: Int,
    val ramPercent: Int,
    val uptime: String
) {
    companion object {
        // Exemplo: "Ananuke: CPU 14% | RAM 48% | Ativo há 4h 12m."
        private val METRICS_REGEX =
            Regex("CPU\\s+(\\d+)%\\s*\\|\\s*RAM\\s+(\\d+)%\\s*\\|\\s*Ativo há\\s+([^.]+)", RegexOption.IGNORE_CASE)

        fun parseFromText(text: String): AnanukeMetrics? {
            val match = METRICS_REGEX.find(text) ?: return null
            val cpu = match.groupValues[1].toIntOrNull() ?: 0
            val ram = match.groupValues[2].toIntOrNull() ?: 0
            val uptime = match.groupValues[3].trim()
            return AnanukeMetrics(cpuPercent = cpu, ramPercent = ram, uptime = uptime)
        }
    }
}
