#!/usr/bin/env python3
"""
Teste de reconhecimento real de áudio pelo canal TLS do LG Mic.
Lê os frames PCM de '5.wav' ('assistente') e transmite via TLS 1.2+
ao servidor LgMicServer no Gateway (porta 8088).
"""
import math
import os
import socket
import ssl
import struct
import sys
import time
import urllib.request
import wave
from pathlib import Path

WAV_PATH = "/tmp/assistente.wav"

def main():
    print("=== TESTE DE RECONHECIMENTO REAL DE ÁUDIO NO LG MIC (TLS) ===")
    token = sys.argv[1] if len(sys.argv) > 1 else os.getenv("JARVIS_AUDIO_TOKEN", "")
    assert len(token) >= 32, "Token deve ter pelo menos 32 caracteres"

    wf = wave.open(WAV_PATH, "rb")
    print(f"Áudio carregado: {WAV_PATH} ({wf.getframerate()}Hz, {wf.getnchannels()}ch, {wf.getnframes()} frames)")

    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    tls_sock = ctx.wrap_socket(sock, server_hostname="192.168.0.126")
    tls_sock.connect(("192.168.0.126", 8088))

    # Handshake
    token_bytes = token.encode("utf-8")
    tls_sock.sendall(b"JMIC")
    tls_sock.sendall(struct.pack(">B", 1))
    tls_sock.sendall(struct.pack(">H", len(token_bytes)))
    tls_sock.sendall(token_bytes)
    tls_sock.sendall(struct.pack(">IBB", 16000, 1, 16))

    print("Handshake TLS enviado. Transmitindo frames de áudio real...")
    seq = 0
    chunk_samples = 640  # 40ms @ 16kHz
    chunk_bytes = chunk_samples * 2

    while True:
        data = wf.readframes(chunk_samples)
        if len(data) == 0:
            break
        if len(data) < chunk_bytes:
            data += b"\x00" * (chunk_bytes - len(data))

        header = b"DATA" + struct.pack(">IQBH", seq, int(time.time() * 1000), 0, chunk_bytes)
        tls_sock.sendall(header + data)
        seq += 1
        time.sleep(0.038)  # Tempo real ~40ms

    # Envia 1 segundo de silêncio para fechar o enunciado no Vosk
    for _ in range(25):
        header = b"DATA" + struct.pack(">IQBH", seq, int(time.time() * 1000), 0, chunk_bytes)
        tls_sock.sendall(header + (b"\x00" * chunk_bytes))
        seq += 1
        time.sleep(0.04)

    tls_sock.close()
    print("Áudio real transmitido com sucesso. Verificando log do Gateway...")

if __name__ == "__main__":
    main()
