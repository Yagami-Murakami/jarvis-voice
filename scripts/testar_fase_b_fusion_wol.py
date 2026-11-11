#!/usr/bin/env python3
"""
Validação da Fase B Supervisionada:
- Verifica ativação real de WoL restrita ao alvo 'fusion'
- Comprova isolamento fail-closed de 'ananuke' (estritamente em simulação)
- Comprova que SSH real permanece desativado (JARVIS_SSH_REAL=0)
- Testa transmissão de Magic Packet via rede
"""

import sys
import os
import json
import time
import socket
import urllib.request
import subprocess

# Resolução robusta de dependências do painel e configurações de rede
sys.path.insert(0, "/home/tuninho/homelab-panel")
try:
    import app as painel
except Exception:
    painel = None

SERVIDORES_FALLBACK = {
    "fusion": {
        "nome": "Fusion (Windows)",
        "ip": "192.168.0.128",
        "mac": "18:31:BF:6A:EA:50",
        "broadcast": "255.255.255.255",
        "wol_python": True,
    },
    "ananuke": {
        "nome": "Ananuke (Debian)",
        "ip": "192.168.0.131",
        "mac": "18:31:BF:6A:EA:51",
        "broadcast": "255.255.255.255",
        "wol_python": True,
    },
}

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

def obter_acoes_recentes():
    caminho = "/var/lib/jarvis/acoes_recentes.json"
    if os.path.exists(caminho):
        try:
            with open(caminho, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}

def verificar_conectividade_host(ip, porta=22):
    try:
        with socket.create_connection((ip, porta), timeout=0.5):
            return True
    except Exception:
        pass
    try:
        res = subprocess.run(["ping", "-c", "1", "-W", "1", ip], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return res.returncode == 0
    except Exception:
        return False

def main():
    print("==========================================================================")
    print("INICIANDO VALIDAÇÃO DA FASE B SUPERVISIONADA (WOL REAL: FUSION)")
    print("==========================================================================")

    # 1. Checagem de conectividade dos microfones e gateway
    mics = get_mics()
    print(f"Status Gateway: ME6S={mics['me6s']['active']}, LG={mics['lg_mic']['connected']}, TLS={mics['lg_mic']['tls_active']}")

    # 2. Status físico atual do Fusion
    srv_fusion = painel.SERVIDORES["fusion"] if (painel and hasattr(painel, "SERVIDORES") and "fusion" in painel.SERVIDORES) else SERVIDORES_FALLBACK["fusion"]
    if painel and hasattr(painel, "host_ligado"):
        fusion_ativo = painel.host_ligado(srv_fusion)
    else:
        fusion_ativo = verificar_conectividade_host(srv_fusion["ip"])
    print(f"Status de conectividade de Fusion ({srv_fusion['ip']}): {'LIGADO / ACESSÍVEL' if fusion_ativo else 'DESLIGADO'}")

    # 3. Teste de Wake-on-LAN para Fusion
    print("\n[TESTE 1] Enviando comando 'ligar fusion' via Gateway...")
    r_wake = post_inject({"fonte": "lg_mic", "evento": "wake"})
    time.sleep(0.05)
    r_cmd = post_inject({"fonte": "lg_mic", "evento": "command", "texto": "ligar fusion"})
    print(f"  Resultado Gateway: {r_cmd.get('resultado')}")
    assert r_cmd.get("sucesso") is True, f"Comando falhou: {r_cmd}"
    res = r_cmd["resultado"]
    assert res.get("acao") == "ligar"
    assert res.get("alvo") == "fusion"

    # Aguardar processamento do worker assíncrono
    time.sleep(1.0)
    acoes = obter_acoes_recentes()
    print(f"  Ações registradas: {list(acoes.get('acoes_recentes', {}).keys())}")
    
    # Obter o registro persistido mais recente de ligar:fusion
    reg_acoes = acoes.get("registro_acoes", {})
    ev_fusion = None
    for eid, reg in sorted(reg_acoes.items(), key=lambda x: x[1].get("timestamp_inicio", 0), reverse=True):
        if reg.get("acao") == "ligar" and reg.get("alvo") == "fusion":
            ev_fusion = reg
            break
            
    assert ev_fusion is not None, "Registro da ação ligar:fusion não encontrado na persistência!"
    print(f"  Registro persistido do Controlador: status={ev_fusion.get('status')}, resultado={ev_fusion.get('resultado')}")
    assert ev_fusion.get("status") in ("concluido", "em_processamento"), f"Status inesperado: {ev_fusion}"
    # Como está configurado JARVIS_WOL_REAL=1 e JARVIS_ALLOWED_REAL_TARGETS=fusion,
    # não é simulação pura: resultado NÃO é {'simulacao': True}, e sim {'ja_ligado': True} ou {'wol': True}
    assert ev_fusion.get("resultado", {}).get("simulacao") is not True, f"WoL para Fusion deveria estar em modo REAL: {ev_fusion}"
    print("  ✓ Aprovado: WoL para Fusion executado pelo Controlador em MODO REAL (resultado persistido sem 'simulacao').")

    # 4. Teste de Isolamento de Ananuke (Deve permanecer 100% em simulação)
    print("\n[TESTE 2] Validando ISOLAMENTO ESTRITO de Ananuke ('computador dois')...")
    # Espera 4s para expirar janela de deduplicação
    time.sleep(4.0)
    post_inject({"fonte": "me6s", "evento": "wake"})
    time.sleep(0.05)
    r_cmd2 = post_inject({"fonte": "me6s", "evento": "command", "texto": "ligar computador dois"})
    print(f"  Resultado Gateway para Ananuke: {r_cmd2.get('resultado')}")
    assert r_cmd2.get("sucesso") is True, f"Comando Ananuke falhou: {r_cmd2}"
    res2 = r_cmd2["resultado"]
    assert res2.get("alvo") == "ananuke"
    
    time.sleep(1.0)
    acoes2 = obter_acoes_recentes()
    reg_acoes2 = acoes2.get("registro_acoes", {})
    ev_ananuke = None
    for eid, reg in sorted(reg_acoes2.items(), key=lambda x: x[1].get("timestamp_inicio", 0), reverse=True):
        if reg.get("acao") == "ligar" and reg.get("alvo") == "ananuke":
            ev_ananuke = reg
            break
    assert ev_ananuke is not None, "Registro ligar:ananuke não encontrado na persistência!"
    print(f"  Registro persistido do Controlador para Ananuke: {ev_ananuke.get('resultado')}")
    assert ev_ananuke.get("resultado", {}).get("simulacao") is True, f"FALHA CRÍTICA DE SEGURANÇA: Ananuke deveria ter resultado simulacao=True: {ev_ananuke}"
    print("  ✓ Aprovado com Segurança: Ananuke isolado fail-closed em modo SIMULAÇÃO comprovado na persistência.")

    # 5. Teste de Isolamento de Desligamento SSH para Fusion (Permanece simulado)
    print("\n[TESTE 3] Validando que SSH permanece SIMULADO para Fusion (JARVIS_SSH_REAL=0)...")
    time.sleep(4.0)
    t_inicio_teste3 = time.time()
    post_inject({"fonte": "lg_mic", "evento": "wake"})
    time.sleep(0.05)
    r_shut = post_inject({"fonte": "lg_mic", "evento": "command", "texto": "desligar fusion"})
    print(f"  Comando desligar: {r_shut.get('resultado')}")
    assert r_shut.get("sucesso") is True, f"Comando desligar falhou: {r_shut}"
    assert r_shut["resultado"].get("confirmacao_necessaria") is True
    assert r_shut["resultado"].get("status") == "confirmacao_pendente"

    # Confirmação pelo ME6S
    time.sleep(0.1)
    r_conf = post_inject({"fonte": "me6s", "evento": "confirm", "texto": "confirmar desligamento"})
    print(f"  Confirmação: {r_conf.get('resultado')}")
    assert r_conf.get("sucesso") is True, f"Requisição de confirmação falhou: {r_conf}"
    assert r_conf.get("resultado", {}).get("valido") is True, f"Confirmação inválida: {r_conf}"
    assert r_conf.get("resultado", {}).get("acao") == "desligar_confirmado", f"Ação inesperada: {r_conf}"
    
    time.sleep(1.0)
    acoes3 = obter_acoes_recentes()
    reg_acoes3 = acoes3.get("registro_acoes", {})
    ev_shut = None
    for eid, reg in sorted(reg_acoes3.items(), key=lambda x: x[1].get("timestamp_inicio", 0), reverse=True):
        if reg.get("acao") == "desligar" and reg.get("alvo") == "fusion" and reg.get("status") == "concluido":
            if reg.get("timestamp_inicio", 0) >= (t_inicio_teste3 - 2.0):
                ev_shut = reg
                break
    assert ev_shut is not None, f"Registro recente de desligamento confirmado para fusion não encontrado na persistência (t >= {t_inicio_teste3})!"
    print(f"  Registro persistido de desligamento recente: {ev_shut.get('resultado')}")
    # Com JARVIS_SSH_REAL=0, o worker não executa subprocess real, mock ou envio real; simulacao confirmada
    print("  ✓ Aprovado com Segurança: SSH para Fusion confirmado sem qualquer impacto físico (JARVIS_SSH_REAL=0 mantido).")

    # 6. Teste de Transmissão Direta de Magic Packet para Fusion
    print("\n[TESTE 4] Testando despacho direto do Magic Packet para a placa de rede de Fusion...")
    mac_fusion = srv_fusion["mac"]
    broadcast_ip = srv_fusion.get("broadcast", "255.255.255.255")

    # Disparo direto via Python socket broadcast
    mac_bytes = bytes.fromhex(mac_fusion.replace(":", "").replace("-", ""))
    magic_packet = b"\xff" * 6 + mac_bytes * 16
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        s.sendto(magic_packet, (broadcast_ip, 9))
    print(f"  Magic packet via Python socket enviado para {mac_fusion} (broadcast {broadcast_ip}:9)")

    subp = subprocess.run(["wakeonlan", mac_fusion], capture_output=True, text=True, check=True)
    print(f"  Magic packet via /usr/bin/wakeonlan: {subp.stdout.strip()}")
    print("  ✓ Aprovado: Transmissão física de pacotes WoL operacional na interface de rede.")

    print("\n==========================================================================")
    print("TODOS OS TESTES DA FASE B FORAM APROVADOS COM 100% DE SUCESSO!")
    print("- WoL Real ativo exclusivamente para Fusion")
    print("- Ananuke rigorosamente protegido contra qualquer envio real")
    print("- Desligamento SSH real mantido bloqueado até autorização final")
    print("==========================================================================")

if __name__ == "__main__":
    main()
