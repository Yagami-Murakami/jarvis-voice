#!/usr/bin/env python3
"""
Watchdog de Desativação Automática do SSH Real (Fail-Safe Supervisor).
Monitora continuamente o token one-shot e o serviço jarvis-voice:
- Se o token for consumido: aguarda 3s para persistência final e desativa o SSH real.
- Se o TTL expirar: desativa imediatamente o SSH real.
- Se o serviço jarvis-voice cair ou reiniciar: desativa imediatamente o SSH real.
- Timeout rígido de segurança: encerra a janela após o TTL mesmo se houver qualquer anomalia.
"""

import json
import os
import subprocess
import sys
import time

TOKEN_FILE = os.getenv("JARVIS_ONE_SHOT_TOKEN_FILE", "/var/lib/jarvis/one_shot_ssh_token.json")
DISARM_SCRIPT = "/opt/jarvis-voice/scripts/desativar_ssh_real_supervisionado.sh"


def log(msg):
    print(f"[WATCHDOG-SSH] [{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}", flush=True)


def desarmar():
    log("Disparando desativação automática imediata do SSH real...")
    if os.path.exists(DISARM_SCRIPT):
        res = subprocess.run(["/bin/bash", DISARM_SCRIPT], capture_output=True, text=True)
        log(f"Resultado do script de desativação (ret={res.returncode}):\n{res.stdout}\n{res.stderr}")
    else:
        # Fallback de desativação manual direta caso o script não esteja no caminho esperado
        dropin = "/etc/systemd/system/jarvis-voice.service.d/zz-ssh-real-oneshot.conf"
        if os.path.exists(dropin):
            try:
                os.unlink(dropin)
                log(f"Drop-in removido: {dropin}")
            except Exception as e:
                log(f"Erro ao remover drop-in: {e}")
        subprocess.run(["systemctl", "daemon-reload"], check=False)
        subprocess.run(["systemctl", "restart", "jarvis-voice.service"], check=False)
        log("Serviço jarvis-voice reiniciado com drop-in removido.")


def obter_pid_servico():
    try:
        res = subprocess.run(
            ["systemctl", "show", "-p", "MainPID", "--value", "jarvis-voice.service"],
            capture_output=True,
            text=True,
            timeout=5
        )
        pid_str = res.stdout.strip()
        return int(pid_str) if pid_str.isdigit() else 0
    except Exception:
        return 0


def main():
    log("Iniciando monitoramento de segurança One-Shot SSH.")
    pid_inicial = obter_pid_servico()
    log(f"PID inicial monitorado do serviço jarvis-voice: {pid_inicial}")

    inicio = time.time()
    limite_absoluto = inicio + 900.0  # Máximo de 15 minutos de vigência absoluta

    while time.time() < limite_absoluto:
        time.sleep(0.5)

        # 1. Verifica integridade do serviço
        pid_atual = obter_pid_servico()
        if pid_atual <= 0:
            log("ALERTA: jarvis-voice.service não está em execução! Desarmando...")
            desarmar()
            return

        if pid_inicial > 0 and pid_atual != pid_inicial:
            log(f"ALERTA: PID do serviço mudou ({pid_inicial} -> {pid_atual})! Possível crash/restart. Desarmando...")
            desarmar()
            return

        # 2. Verifica estado do token
        if not os.path.exists(TOKEN_FILE):
            log("Token não encontrado ou removido. Desarmando...")
            desarmar()
            return

        try:
            with open(TOKEN_FILE, "r", encoding="utf-8") as f:
                dados = json.load(f)
        except Exception as e:
            log(f"Erro ao ler token ({e}). Desarmando...")
            desarmar()
            return

        status = dados.get("status")
        consumed = dados.get("consumed", False)
        expires_at = dados.get("expires_at", 0)

        if consumed or status == "consumed":
            ev_id = dados.get("consumed_by_event")
            log(f"SUCESSO: Token {dados.get('token_id')} foi consumido pelo evento {ev_id}.")
            log("Aguardando conclusão da execução SSH no controlador...")
            t_espera_inicio = time.time()
            concluido = False
            state_file = os.getenv("JARVIS_STATE_FILE", "/var/lib/jarvis/acoes_recentes.json")
            while (time.time() - t_espera_inicio) < 35.0:
                if dados.get("finished"):
                    concluido = True
                    break
                if ev_id and os.path.exists(state_file):
                    try:
                        with open(state_file, "r", encoding="utf-8") as sf:
                            st_data = json.load(sf)
                        reg = st_data.get("registro_acoes", {}).get(ev_id, {})
                        if reg.get("status") in ("concluido", "falha", "indeterminado"):
                            concluido = True
                            break
                    except Exception:
                        pass
                time.sleep(0.5)
                try:
                    with open(TOKEN_FILE, "r", encoding="utf-8") as f:
                        dados = json.load(f)
                except Exception:
                    pass

            log(f"Execução SSH concluída (finalizado={concluido}, decorrido={time.time()-t_espera_inicio:.2f}s). Aguardando sincronização de buffers...")
            time.sleep(2.0)
            desarmar()
            log("Ciclo One-Shot concluído com sucesso e SSH real desativado.")
            return

        if status in ("invalidated_by_restart", "invalidated_by_disarm"):
            log(f"Token invalidado ({status}). Desarmando...")
            desarmar()
            return

        if time.time() > expires_at:
            log(f"TTL EXPIRADO: Token {dados.get('token_id')} expirou em {expires_at}. Desarmando...")
            desarmar()
            return

    log("LIMITE ABSOLUTO EXCEDIDO! Desarmando por segurança...")
    desarmar()


if __name__ == "__main__":
    main()
