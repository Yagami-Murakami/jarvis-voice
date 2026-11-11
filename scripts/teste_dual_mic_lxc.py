#!/usr/bin/env python3
"""
Validação automatizada de bancada:
- Wake simultâneo com deduplicação
- Deduplicação de ação (WoL e Desligamento)
- Confirmação de segurança obrigatória
- Interoperabilidade entre microfones
"""
import json
import time
import sys
import urllib.request

BASE_URL = "http://127.0.0.1:8089"

def post_inject(data):
    req = urllib.request.Request(
        f"{BASE_URL}/api/test/inject",
        data=json.dumps(data).encode("utf-8"),
        headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read().decode("utf-8"))

def get_mics():
    with urllib.request.urlopen(f"{BASE_URL}/api/mics") as resp:
        return json.loads(resp.read().decode("utf-8"))

def main():
    print("==================================================")
    print("INICIANDO BATERIA DE TESTES DE BANCADA (SIMULAÇÃO)")
    print("==================================================")

    # 0. Checagem de conectividade dos microfones
    mics = get_mics()
    print(f"Status inicial: ME6S={mics['me6s']['active']}, LG_MIC_CONNECTED={mics['lg_mic']['connected']}, TLS={mics['lg_mic']['tls_active']}")
    assert mics['lg_mic']['tls_active'] is True, "TLS 1.2+ deve estar ativo!"

    # 1. Wake simultâneo
    print("\n[TESTE 1] Wake simultâneo (ME6S + LG com 50ms de diferença)...")
    r1 = post_inject({"fonte": "me6s", "evento": "wake"})
    time.sleep(0.05)
    r2 = post_inject({"fonte": "lg_mic", "evento": "wake"})
    print(f"  ME6S Wake: is_new={r1.get('is_new_session')}, sid={r1.get('session_id')}")
    print(f"  LG   Wake: is_new={r2.get('is_new_session')}, sid={r2.get('session_id')}")
    assert r1.get("is_new_session") is True, "ME6S deveria ter iniciado sessão"
    assert r2.get("is_new_session") is False, "LG deveria ter sido deduplicado!"
    assert r1.get("session_id") == r2.get("session_id"), "Sessão deve ser a mesma!"
    print("  ✓ Aprovado: Apenas 1 sessão criada, wake simultâneo deduplicado.")

    # 2. Comando WoL com deduplicação de ação
    print("\n[TESTE 2] Comando WoL com deduplicação de ação...")
    cmd1 = post_inject({"fonte": "me6s", "evento": "command", "texto": "ligar fusion"})
    print(f"  Comando 1 (ME6S): {cmd1.get('resultado')}")
    assert cmd1["resultado"]["valido"] is True
    assert cmd1["resultado"]["simulacao"] is True

    # Nova sessão rápida pela outra fonte dentro da janela de deduplicação (ex: 200ms depois)
    time.sleep(0.2)
    w2 = post_inject({"fonte": "lg_mic", "evento": "wake"})
    cmd2 = post_inject({"fonte": "lg_mic", "evento": "command", "texto": "ligar fusion"})
    print(f"  Comando 2 (LG em nova sessão imediata): {cmd2.get('resultado')}")
    assert cmd2["resultado"]["valido"] is False
    assert cmd2["resultado"].get("deduplicado") is True
    print("  ✓ Aprovado: WoL deduplicado com sucesso na camada de ação (janela de 3.5s).")

    # Espera a janela de ação expirar para o próximo teste
    print("  Aguardando expiração da janela de deduplicação de ação (4s)...")
    time.sleep(4)

    # 3. Fluxo de desligamento com confirmação obrigatória
    print("\n[TESTE 3] Desligamento: LG inicia, ME6S confirma...")
    w = post_inject({"fonte": "lg_mic", "evento": "wake"})
    print(f"  Wake LG: sid={w.get('session_id')}")

    cmd_shut = post_inject({"fonte": "lg_mic", "evento": "command", "texto": "desligar fusion"})
    print(f"  Comando desligar: {cmd_shut.get('resultado')}")
    assert cmd_shut["resultado"]["valido"] is True
    assert cmd_shut["resultado"]["acao"] == "desligar"
    assert cmd_shut["resultado"]["confirmacao_necessaria"] is True

    # Confirmação pelo outro microfone
    conf = post_inject({"fonte": "me6s", "evento": "confirm"})
    print(f"  Confirmação (ME6S): {conf.get('resultado')}")
    assert conf["resultado"]["valido"] is True
    assert conf["resultado"]["acao"] == "desligar_confirmado"
    assert conf["resultado"]["simulacao"] is True

    # Tentativa de confirmação duplicada (replay ou segundo microfone)
    time.sleep(0.1)
    conf_dup = post_inject({"fonte": "lg_mic", "evento": "confirm"})
    print(f"  Confirmação duplicada (LG): {conf_dup.get('resultado')}")
    assert conf_dup["resultado"]["valido"] is False
    print("  ✓ Aprovado: Confirmação obrigatória garantida e deduplicada.")

    print("\n==================================================")
    print("TODOS OS TESTES DE BANCADA PASSARAM COM SUCESSO!")
    print("==================================================")

if __name__ == "__main__":
    main()
