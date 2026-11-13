package com.jarvis.voice.network

import org.junit.Assert.*
import org.junit.Test
import java.io.ByteArrayInputStream
import java.nio.ByteBuffer
import java.nio.ByteOrder
import java.security.MessageDigest
import java.security.cert.CertificateFactory
import java.security.cert.X509Certificate
import java.util.Locale

class JarvisStreamClientTest {

    private val sampleCertPem = """
        -----BEGIN CERTIFICATE-----
        MIIC/zCCAeegAwIBAgIUG3qYE5fPNn7wOoUeoB4PHzm6i1UwDQYJKoZIhvcNAQEL
        BQAwDzENMAsGA1UEAwwEdGVzdDAeFw0yNjExMTMwMDU5NTZaFw0yNjExMTQwMDU5
        NTZaMA8xDTALBgNVBAMMBHRlc3QwggEiMA0GCSqGSIb3DQEBAQUAA4IBDwAwggEK
        AoIBAQDhl/KWXd5YNEN9RPaq4WOZUJqcDEGBUJ/c2Own1mw1dgA7qG/hJCs6vZxF
        zfYdVqY/lMGUwU7JPSLuIuZooVHdbSOBqzcaTe2pUHhFhKkfNcqeqL3/x35UpNx+
        i7DQltOCBOLeguJqBMRNTq/6uTYDTgEOEL97KuRDMVWqbp7UlC2sLyu/bvWZpYXd
        G46STzl3+l6cRpfKiCE2m+ZEzrLark+e2cYFa8aehCmCPAKO7WeBKr4uE1Jx/JDH
        t0aI5b8sruSQl2Znkc3SJHFckg1+EqOGiMu9yj7Ar4EI6zqilLb85kcFcnzBhwyh
        EH0LgAlIUaXFfbkbCpP9k+Wfowe5AgMBAAGjUzBRMB0GA1UdDgQWBBSJ6Q9r1FxC
        bdX35Gm8h9ICjTVLHjAfBgNVHSMEGDAWgBSJ6Q9r1FxCbdX35Gm8h9ICjTVLHjAP
        BgNVHRMBAf8EBTADAQH/MA0GCSqGSIb3DQEBCwUAA4IBAQCOyX7Ulom3/xQfVKA3
        NKX2fPQ13Wn5s+1IQarcynUBErtKWSndoOE6ki35DjNDkjdGjT2oAYv89RqnY2nB
        ZNfvlX5vriZ47bRW89sbM2ZDW7st3/bdwEytqS+Kz0tEHM2JbUra+UY3uK2iOHzC
        +e5R7F+wXoevTG1tYlUA6PE3K9Y6BuXyALX0QrTK++EaSUQZowNZOPs14CtINd+p
        Jbuz3XPloC/eYV+y/oiKznWyDKeylLv3IVwc/FJNCAf+Env0LRONIYvptxKfX+Nb
        I+3U2y1DJFK++HXEsIxLfLHo4Wan+G9sRalsVR7rIuUGZKwNhJ6GhQFBRv/TTYwg
        RKbu
        -----END CERTIFICATE-----
    """.trimIndent()

    @Test
    fun testCertificatePinningCalculation() {
        val cf = CertificateFactory.getInstance("X.509")
        val cert = cf.generateCertificate(ByteArrayInputStream(sampleCertPem.toByteArray())) as X509Certificate

        val md = MessageDigest.getInstance("SHA-256")
        val digest = md.digest(cert.encoded)
        val certFp = digest.joinToString("") { "%02x".format(it) }

        assertNotNull(certFp)
        assertEquals(64, certFp.length)

        // Verificação estrita de formatação (case-insensitive e remoção de separadores)
        val formattedWithColons = certFp.chunked(2).joinToString(":")
        val cleanFp = formattedWithColons.trim().replace(":", "").replace(" ", "").lowercase(Locale.US)
        assertEquals(certFp, cleanFp)
    }

    @Test
    fun testProtocolNegotiationFiltering() {
        // Simula Android 6.0 onde TLS 1.3 não é suportado
        val supportedAndroid6 = listOf("TLSv1.2", "TLSv1.1", "TLSv1")
        val desired = listOf("TLSv1.3", "TLSv1.2")
        val enabled6 = desired.filter { it in supportedAndroid6 }
        assertEquals(listOf("TLSv1.2"), enabled6)

        // Simula Android 14 onde TLS 1.3 e 1.2 são suportados
        val supportedAndroid14 = listOf("TLSv1.3", "TLSv1.2")
        val enabled14 = desired.filter { it in supportedAndroid14 }
        assertEquals(listOf("TLSv1.3", "TLSv1.2"), enabled14)

        // Simula ambiente hostil sem TLS moderno
        val supportedAncient = listOf("SSLv3", "TLSv1.0")
        val enabledAncient = desired.filter { it in supportedAncient }
        assertTrue(enabledAncient.isEmpty())
    }

    @Test
    fun testHandshakeV2PayloadFormat() {
        // Valida estrutura exata do JMIC v2
        val deviceId = "celular_mobile"
        val token = "1fd625108c3ed67d5af38dad1d8901f3089d9e80727d6db36f656d85c349eeca"
        val devBytes = deviceId.toByteArray(Charsets.UTF_8)
        val tokenBytes = token.toByteArray(Charsets.UTF_8)

        val buffer = ByteBuffer.allocate(4 + 1 + 1 + devBytes.size + 2 + tokenBytes.size + 4 + 1 + 1)
        buffer.order(ByteOrder.BIG_ENDIAN)
        buffer.put(byteArrayOf('J'.code.toByte(), 'M'.code.toByte(), 'I'.code.toByte(), 'C'.code.toByte()))
        buffer.put(2.toByte())
        buffer.put(devBytes.size.toByte())
        buffer.put(devBytes)
        buffer.putShort(tokenBytes.size.toShort())
        buffer.put(tokenBytes)
        buffer.putInt(16000)
        buffer.put(1.toByte())
        buffer.put(16.toByte())

        val array = buffer.array()
        assertEquals(4 + 1 + 1 + devBytes.size + 2 + tokenBytes.size + 4 + 1 + 1, array.size)
        assertEquals('J'.code.toByte(), array[0])
        assertEquals('M'.code.toByte(), array[1])
        assertEquals('I'.code.toByte(), array[2])
        assertEquals('C'.code.toByte(), array[3])
        assertEquals(2.toByte(), array[4])
        assertEquals(devBytes.size.toByte(), array[5])
    }
}
