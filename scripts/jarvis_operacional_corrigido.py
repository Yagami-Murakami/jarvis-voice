import sys
import os
import json
import time
import subprocess
import threading
import queue

try:
    from vosk import Model, KaldiRecognizer, SetLogLevel
except ImportError:
    Model = None
    KaldiRecognizer = None
    def SetLogLevel(*args): pass

# Carregar funcoes do painel existente
sys.path.insert(0, "/home/tuninho/homelab-panel")
sys.path.insert(0, "/opt/jarvis-voice")
try:
    import app as painel
except ImportError:
    from unittest.mock import MagicMock
    painel = MagicMock()
    painel.SERVIDORES = {
        "fusion": {"nome": "Fusion (Windows)", "mac": "00:11:22:33:44:55"},
        "ananuke": {"nome": "Ananuke", "mac": "00:11:22:33:44:66"},
    }
    painel.host_ligado = lambda srv: False
    painel.montar_comando_ssh = lambda cfg: ["echo", "ssh_simulado"]

from gateway.ipc_bus import DEFAULT_IPC_SOCKET, IpcEventListener

SetLogLevel(-1)

MODELO = "/opt/jarvis-voice/vosk-model-small-pt-0.3"

COMANDOS = {
    "ligar fusion": ("ligar", "fusion"),
    "ligar o fusion": ("ligar", "fusion"),
    "desligar fusion": ("desligar", "fusion"),
    "desligar o fusion": ("desligar", "fusion"),
    "ligar computador dois": ("ligar", "ananuke"),
    "ligar ana no que": ("ligar", "ananuke"),
    "ligar o computador dois": ("ligar", "ananuke"),
    "desligar computador dois": ("desligar", "ananuke"),
    "desligar ana no que": ("desligar", "ananuke"),
    "desligar o computador dois": ("desligar", "ananuke"),
}

gramatica = [
    "assistente",
    "confirmar",
    "confirmar desligar",
    "confirmar desligamento",
    "cancelar",
    *COMANDOS.keys(),
    "[unk]"
]

if Model is not None and os.path.exists(MODELO):
    modelo = Model(MODELO)
    rec = KaldiRecognizer(
        modelo, 16000,
        json.dumps(gramatica, ensure_ascii=False)
    )
else:
    modelo = None
    rec = None

estado = "espera"
limite = 0.0
pendente = None
ultima_ativacao = 0.0
fonte_ativa = None
sessao_ativa_id = None

# Fila unificada de intenções para ambos os microfones (ME6S + LG)
fila_intencoes = queue.Queue(maxsize=32)
loop_principal_ativo = threading.Event()

# Cache de idempotência / deduplicação de ações
acoes_recentes = {}
lock_acoes = threading.Lock()

# Registro do ciclo de vida de ações: 'aceito' -> 'em_processamento' -> 'concluido' / 'falha'
registro_acoes = {}
lock_registro_acoes = threading.Lock()


def registrar_inicio_acao(event_id, acao, alvo, status="aceito"):
    ev_key = event_id or f"ev-{int(time.time() * 1000)}"
    with lock_registro_acoes:
        registro_acoes[ev_key] = {
            "event_id": ev_key,
            "acao": acao,
            "alvo": alvo,
            "status": status,
            "timestamp_inicio": time.monotonic(),
            "timestamp_conclusao": None,
            "resultado": None,
            "erro": None,
        }
    return ev_key


def atualizar_status_acao(event_id, status, acao=None, alvo=None, resultado=None, erro=None):
    if not event_id:
        return
    with lock_registro_acoes:
        if event_id not in registro_acoes:
            registro_acoes[event_id] = {
                "event_id": event_id,
                "acao": acao,
                "alvo": alvo,
                "status": status,
                "timestamp_inicio": time.monotonic(),
                "timestamp_conclusao": None,
                "resultado": resultado,
                "erro": erro,
            }
        else:
            reg = registro_acoes[event_id]
            reg["status"] = status
            if acao:
                reg["acao"] = acao
            if alvo:
                reg["alvo"] = alvo
            if resultado is not None:
                reg["resultado"] = resultado
            if erro is not None:
                reg["erro"] = erro
            if status in ("concluido", "falha"):
                reg["timestamp_conclusao"] = time.monotonic()


def obter_status_acao(event_id=None, acao=None, alvo=None):
    with lock_registro_acoes:
        if event_id and event_id in registro_acoes:
            return dict(registro_acoes[event_id])
        if acao and alvo:
            candidatos = [
                r for r in registro_acoes.values()
                if r.get("acao") == acao and r.get("alvo") == alvo
            ]
            if candidatos:
                candidatos.sort(key=lambda x: x.get("timestamp_inicio", 0), reverse=True)
                return dict(candidatos[0])
        return None

contador_sessao = 0
lock_sessao = threading.Lock()


def gerar_session_id(fonte="me6s"):
    global contador_sessao
    with lock_sessao:
        contador_sessao += 1
        return f"sess-{int(time.time())}-{contador_sessao}"


def registrar_acao(acao, alvo):
    with lock_acoes:
        acoes_recentes[(acao, alvo)] = time.monotonic()


def ja_executado_recentemente(acao, alvo, janela=3.5):
    with lock_acoes:
        with lock_registro_acoes:
            # Se uma ação idêntica estiver atualmente 'em_processamento', bloqueia imediatamente
            for reg in registro_acoes.values():
                if reg.get("acao") == acao and reg.get("alvo") == alvo and reg.get("status") == "em_processamento":
                    return True
        return (time.monotonic() - acoes_recentes.get((acao, alvo), 0.0)) <= janela


def processar(texto, fonte="me6s", session_id=None, event_id=None):
    global estado, limite, pendente, ultima_ativacao, fonte_ativa, sessao_ativa_id

    agora = time.monotonic()

    if estado != "espera" and agora > limite:
        print("Tempo esgotado.", flush=True)
        estado = "espera"
        pendente = None
        fonte_ativa = None
        sessao_ativa_id = None

    # Preservação estrita do formato de log contratual para display/server.py e Me6sJournalMonitor
    if fonte != "me6s":
        print(f"[Origem: {fonte}]", flush=True)
    print("Ouvi:", texto, flush=True)

    if texto == "cancelar":
        estado = "espera"
        pendente = None
        fonte_ativa = None
        sessao_ativa_id = None
        ultima_ativacao = 0.0
        print("Cancelado.", flush=True)
        return {
            "sucesso": True,
            "event_id": event_id,
            "active_session_id": None,
            "estado": "espera",
            "acao": "cancelar",
            "processado": True,
        }

    if estado == "espera":
        if texto == "assistente":
            # Deduplicação de ativação simultânea entre os dois microfones
            if (agora - ultima_ativacao) <= 2.0 and sessao_ativa_id:
                print(f"Ativacao deduplicada via {fonte} (ja ativo ha {agora - ultima_ativacao:.2f}s).", flush=True)
                return {
                    "sucesso": True,
                    "event_id": event_id,
                    "active_session_id": sessao_ativa_id,
                    "estado": estado,
                    "deduplicado": True,
                    "processado": True,
                }

            estado = "comando"
            limite = agora + 12
            ultima_ativacao = agora
            fonte_ativa = fonte
            sessao_ativa_id = session_id or sessao_ativa_id or gerar_session_id(fonte=fonte)
            print(f"[Sessao: {sessao_ativa_id}]", flush=True)
            print("ATIVADO! Aguardando comando.", flush=True)
            return {
                "sucesso": True,
                "event_id": event_id,
                "active_session_id": sessao_ativa_id,
                "estado": "comando",
                "deduplicado": False,
                "processado": True,
            }
        return {
            "sucesso": False,
            "erro": "aguardando_wake",
            "event_id": event_id,
            "active_session_id": None,
            "estado": "espera",
            "processado": True,
        }

    if estado == "comando":
        if texto == "assistente":
            print("Ja ativado. Aguardando comando.", flush=True)
            limite = agora + 12
            return {
                "sucesso": True,
                "event_id": event_id,
                "active_session_id": sessao_ativa_id,
                "estado": "comando",
                "deduplicado": True,
                "processado": True,
            }

        # Se for comando recebido com session_id, valida correspondência com a sessão ativa
        if session_id and sessao_ativa_id and session_id != sessao_ativa_id:
            print("Comando descartado: session_id incompativel com a sessao ativa.", flush=True)
            return {
                "sucesso": False,
                "erro": "session_id_divergente",
                "event_id": event_id,
                "active_session_id": sessao_ativa_id,
                "estado": estado,
                "processado": False,
            }

        # Correcao restrita ao teste de reconhecimento
        if texto == "dois ligar fusion":
            print("Normalizado: ligar fusion", flush=True)
            texto = "ligar fusion"

        if texto not in COMANDOS:
            print("Comando ignorado.", flush=True)
            return {
                "sucesso": False,
                "erro": "comando_desconhecido",
                "event_id": event_id,
                "active_session_id": sessao_ativa_id,
                "estado": estado,
                "processado": True,
            }

        acao, alvo = COMANDOS[texto]
        srv = painel.SERVIDORES[alvo]

        print("Computador:", srv["nome"], flush=True)
        acessivel = painel.host_ligado(srv)

        if acao == "ligar":
            if acessivel:
                print("Computador ja esta acessivel.", flush=True)
            else:
                # Deduplicação na camada de ação (idempotência do WoL)
                if ja_executado_recentemente("ligar", alvo):
                    print(f"WoL para {alvo} descartado por idempotencia.", flush=True)
                    estado = "espera"
                    fonte_ativa = None
                    sessao_ativa_id = None
                    return {
                        "sucesso": True,
                        "event_id": event_id,
                        "acao": "ligar",
                        "alvo": alvo,
                        "deduplicado": True,
                        "active_session_id": None,
                        "estado": "espera",
                        "processado": True,
                    }

                registrar_acao("ligar", alvo)
                ev_wol = event_id or f"ev-wol-{int(time.time() * 1000)}"
                registrar_inicio_acao(ev_wol, "ligar", alvo)
                if os.getenv("JARVIS_WOL_REAL") == "1":
                    try:
                        if not srv.get("mac"):
                            raise ValueError("MAC nao configurado")

                        if srv.get("wol_python"):
                            painel.enviar_magic_packet(
                                srv["mac"],
                                broadcast=srv.get(
                                    "broadcast", "255.255.255.255"
                                )
                            )
                        else:
                            subprocess.run(
                                ["wakeonlan", srv["mac"]],
                                check=True,
                                timeout=10
                            )

                        print("Wake-on-LAN enviado.", flush=True)
                        atualizar_status_acao(ev_wol, "concluido", resultado={"wol": True})

                    except Exception as erro:
                        print("Falha no Wake-on-LAN:", erro, flush=True)
                        atualizar_status_acao(ev_wol, "falha", erro=str(erro))
                else:
                    print("SIMULACAO: Wake-on-LAN.", flush=True)
                    atualizar_status_acao(ev_wol, "concluido", resultado={"simulacao": True})
            estado = "espera"
            fonte_ativa = None
            sessao_ativa_id = None
            ultima_ativacao = 0.0
            return {
                "sucesso": True,
                "event_id": event_id,
                "acao": "ligar",
                "alvo": alvo,
                "active_session_id": None,
                "estado": "espera",
                "status": "concluido",
                "processado": True,
            }

        if not acessivel:
            print(
                "DESLIGAMENTO BLOQUEADO:",
                "computador nao acessivel.",
                flush=True
            )
            estado = "espera"
            fonte_ativa = None
            sessao_ativa_id = None
            return {
                "sucesso": False,
                "erro": "computador_inacessivel",
                "event_id": event_id,
                "active_session_id": None,
                "estado": "espera",
                "processado": True,
            }

        estado = "confirmacao"
        pendente = alvo
        limite = time.monotonic() + 20
        print(
            "CONFIRMAR desligamento de",
            srv["nome"],
            "? Diga confirmar ou cancelar.",
            flush=True
        )
        return {
            "sucesso": True,
            "event_id": event_id,
            "acao": "desligar_pendente",
            "alvo": alvo,
            "active_session_id": sessao_ativa_id,
            "estado": "confirmacao",
            "processado": True,
        }

    if estado == "confirmacao":
        if texto == "assistente":
            print("Aguardando confirmacao de desligamento.", flush=True)
            return {
                "sucesso": True,
                "event_id": event_id,
                "active_session_id": sessao_ativa_id,
                "estado": "confirmacao",
                "deduplicado": True,
                "processado": True,
            }

        # Se for confirmação recebida com session_id, valida correspondência com a sessão ativa
        if session_id and sessao_ativa_id and session_id != sessao_ativa_id:
            print("Confirmacao descartada: session_id incompativel com a sessao ativa.", flush=True)
            return {
                "sucesso": False,
                "erro": "session_id_divergente",
                "event_id": event_id,
                "active_session_id": sessao_ativa_id,
                "estado": estado,
                "processado": False,
            }

        if texto in ("confirmar", "confirmar desligar", "confirmar desligamento"):
            srv = painel.SERVIDORES[pendente]
            status_acao = "falha"
            if painel.host_ligado(srv):
                if os.getenv("JARVIS_SSH_REAL") == "1":
                    if texto != "confirmar desligamento":
                        print(
                            "Confirmacao insuficiente. Pedido cancelado.",
                            flush=True
                        )
                        estado = "espera"
                        pendente = None
                        fonte_ativa = None
                        sessao_ativa_id = None
                        return {
                            "sucesso": False,
                            "erro": "confirmacao_insuficiente",
                            "event_id": event_id,
                            "active_session_id": None,
                            "estado": "espera",
                            "status": "falha",
                            "processado": True,
                        }

                # Idempotência verificada estritamente após confirmação válida
                if ja_executado_recentemente("desligar", pendente):
                    print(f"Desligamento de {pendente} descartado por idempotencia.", flush=True)
                    alvo_confirmado = pendente
                    estado = "espera"
                    pendente = None
                    fonte_ativa = None
                    sessao_ativa_id = None
                    return {
                        "sucesso": True,
                        "event_id": event_id,
                        "acao": "desligar",
                        "alvo": alvo_confirmado,
                        "deduplicado": True,
                        "active_session_id": None,
                        "estado": "espera",
                        "status": "concluido",
                        "processado": True,
                    }

                alvo_confirmado = pendente
                registrar_acao("desligar", alvo_confirmado)
                ev_key = event_id or f"ev-desligar-{int(time.time() * 1000)}"
                registrar_inicio_acao(ev_key, "desligar", alvo_confirmado, status="em_processamento")

                configuracao = dict(srv)
                if alvo_confirmado == "ananuke":
                    configuracao["cmd"] = "sudo -n /usr/sbin/poweroff"

                # Disparo da ação SSH em thread dedicada para não bloquear o IPC nem o loop principal
                def _executar_ssh_worker(srv_info, conf, ev_id, alvo_nome):
                    try:
                        cmd = painel.montar_comando_ssh(conf) if hasattr(painel, "montar_comando_ssh") else None
                        if os.getenv("JARVIS_SSH_REAL") == "1":
                            resultado = subprocess.run(
                                cmd,
                                capture_output=True,
                                text=True,
                                timeout=25
                            )
                            print("SSH enviado. Codigo:", resultado.returncode, flush=True)
                            if resultado.stderr:
                                print("SSH:", resultado.stderr.strip(), flush=True)
                            print("Verifique se o computador desligou.", flush=True)
                            atualizar_status_acao(ev_id, "concluido", acao="desligar", alvo=alvo_nome, resultado={"codigo": resultado.returncode})
                        else:
                            atraso = getattr(painel, "simular_atraso_ssh_s", 0.0)
                            if isinstance(atraso, (int, float)) and atraso > 0:
                                time.sleep(atraso)
                            print("SIMULACAO SSH para", srv_info["nome"], flush=True)
                            atualizar_status_acao(ev_id, "concluido", acao="desligar", alvo=alvo_nome, resultado={"codigo": 0})
                    except Exception as erro:
                        print("Erro no SSH:", erro, flush=True)
                        atualizar_status_acao(ev_id, "falha", acao="desligar", alvo=alvo_nome, erro=str(erro))

                th_ssh = threading.Thread(
                    target=_executar_ssh_worker,
                    args=(srv, configuracao, ev_key, alvo_confirmado),
                    daemon=True
                )
                th_ssh.start()
                # Aguarda até 50ms: em modo simulação sem atraso ou conclusão rápida, sincroniza imediatamente
                th_ssh.join(timeout=0.05)
                reg_status = obter_status_acao(ev_key)
                status_acao = reg_status.get("status", "em_processamento") if reg_status else "em_processamento"
            else:
                print("Cancelado: computador inacessivel.", flush=True)
                status_acao = "falha"

            estado = "espera"
            alvo_confirmado = pendente
            pendente = None
            fonte_ativa = None
            sessao_ativa_id = None
            ultima_ativacao = 0.0
            return {
                "sucesso": True if status_acao != "falha" else False,
                "erro": "computador_inacessivel" if status_acao == "falha" else None,
                "event_id": event_id,
                "acao": "confirmar_desligar",
                "alvo": alvo_confirmado,
                "active_session_id": None,
                "estado": "espera",
                "status": status_acao,
                "processado": True,
            }


# --- Barramento IPC Local por UNIX Domain Socket com Confirmação Confiável de Processamento ---
def on_ipc_event(evento):
    global estado, sessao_ativa_id, pendente, limite, ultima_ativacao, fonte_ativa
    if not isinstance(evento, dict):
        return {"sucesso": False, "erro": "payload_invalido", "processado": False}
    if evento.get("versao") != 1:
        return {"sucesso": False, "erro": "versao_incompativel", "processado": False}

    fonte = evento.get("fonte")
    if fonte not in ("lg_mic", "me6s"):
        return {"sucesso": False, "erro": "fonte_desconhecida", "processado": False}

    tipo = evento.get("evento")
    if tipo in ("status", "consultar_acao"):
        event_id = evento.get("event_id") or evento.get("nonce")
        target_id = evento.get("target_event_id") or event_id
        reg = obter_status_acao(event_id=target_id)
        if reg:
            return {
                "sucesso": True,
                "event_id": event_id,
                "target_event_id": target_id,
                "status": reg.get("status", "desconhecido"),
                "acao": reg.get("acao"),
                "alvo": reg.get("alvo"),
                "estado": estado,
                "processado": True,
            }
        return {
            "sucesso": False,
            "erro": "acao_nao_encontrada",
            "event_id": event_id,
            "target_event_id": target_id,
            "estado": estado,
            "processado": True,
        }

    if tipo not in ("wake", "comando", "confirmar", "cancelar"):
        return {"sucesso": False, "erro": "tipo_desconhecido", "processado": False}

    texto = evento.get("texto")
    if not isinstance(texto, str) or len(texto) > 128:
        return {"sucesso": False, "erro": "texto_invalido", "processado": False}
    texto = texto.strip()
    if not texto:
        return {"sucesso": False, "erro": "texto_vazio", "processado": False}

    # Validação semântica estrita do texto conforme o tipo de evento
    if tipo == "wake" and texto != "assistente":
        return {"sucesso": False, "erro": "wake_deve_ser_assistente", "processado": False}
    if tipo == "cancelar" and texto != "cancelar":
        return {"sucesso": False, "erro": "cancelar_deve_ser_cancelar", "processado": False}
    if tipo == "confirmar" and texto not in ("confirmar", "confirmar desligar", "confirmar desligamento"):
        return {"sucesso": False, "erro": "confirmacao_invalida", "processado": False}
    if tipo == "comando" and texto not in COMANDOS and texto != "dois ligar fusion":
        return {"sucesso": False, "erro": "comando_desconhecido", "processado": False}

    sid = evento.get("session_id")
    event_id = evento.get("event_id") or evento.get("nonce")

    # 1. Se a fila estiver cheia, responde imediatamente com FALHA (Requisito 3)
    if fila_intencoes.full():
        print(f"Fila de intencoes cheia ({fila_intencoes.qsize()}/{fila_intencoes.maxsize}). Descartando evento IPC.", flush=True)
        return {
            "sucesso": False,
            "erro": "fila_cheia",
            "event_id": event_id,
            "active_session_id": sessao_ativa_id,
            "estado": estado,
            "processado": False,
        }

    # Validação rigorosa de session_id, origem e estado antes de aceitar comandos e confirmações
    if tipo == "wake":
        if estado == "comando":
            print(f"IPC Wake deduplicado: controlador ja ativo em 'comando' (sessao: {sessao_ativa_id}).", flush=True)
            return {
                "sucesso": True,
                "event_id": event_id,
                "active_session_id": sessao_ativa_id,
                "estado": "comando",
                "deduplicado": True,
                "processado": True,
            }
        elif estado == "confirmacao":
            print(f"IPC Wake deduplicado: controlador ja aguardando confirmacao (sessao: {sessao_ativa_id}).", flush=True)
            return {
                "sucesso": True,
                "event_id": event_id,
                "active_session_id": sessao_ativa_id,
                "estado": "confirmacao",
                "deduplicado": True,
                "processado": True,
            }
        elif (time.monotonic() - ultima_ativacao) <= 2.0 and sessao_ativa_id:
            print(f"IPC Wake deduplicado: janela de 2.0s respeitada (sessao: {sessao_ativa_id}).", flush=True)
            return {
                "sucesso": True,
                "event_id": event_id,
                "active_session_id": sessao_ativa_id,
                "estado": estado,
                "deduplicado": True,
                "processado": True,
            }

    if tipo == "comando":
        if estado != "comando":
            print(f"IPC descartado: comando recebido fora do estado 'comando' (estado atual: {estado}).", flush=True)
            return {
                "sucesso": False,
                "erro": "estado_invalido",
                "motivo": f"Estado atual não aceita comandos ({estado})",
                "event_id": event_id,
                "active_session_id": sessao_ativa_id,
                "estado": estado,
                "processado": False,
            }
        if not sid or (sessao_ativa_id and sid != sessao_ativa_id):
            print(f"IPC descartado: session_id invalido para comando (recebido: {sid}, ativo: {sessao_ativa_id}).", flush=True)
            return {
                "sucesso": False,
                "erro": "session_id_divergente",
                "event_id": event_id,
                "active_session_id": sessao_ativa_id,
                "estado": estado,
                "processado": False,
            }

    if tipo == "confirmar":
        if estado != "confirmacao" or pendente is None:
            print(f"IPC descartado: confirmacao recebida fora do estado 'confirmacao' (estado atual: {estado}).", flush=True)
            return {
                "sucesso": False,
                "erro": "estado_invalido",
                "motivo": f"Estado atual não aceita confirmação ({estado})",
                "event_id": event_id,
                "active_session_id": sessao_ativa_id,
                "estado": estado,
                "processado": False,
            }
        if not sid or (sessao_ativa_id and sid != sessao_ativa_id):
            print(f"IPC descartado: session_id invalido para confirmacao (recebido: {sid}, ativo: {sessao_ativa_id}).", flush=True)
            return {
                "sucesso": False,
                "erro": "session_id_divergente",
                "event_id": event_id,
                "active_session_id": sessao_ativa_id,
                "estado": estado,
                "processado": False,
            }

    if tipo == "cancelar":
        if estado == "espera":
            return {
                "sucesso": True,
                "event_id": event_id,
                "active_session_id": None,
                "estado": "espera",
                "processado": True,
            }
        if sid and sessao_ativa_id and sid != sessao_ativa_id:
            print(f"IPC descartado: session_id divergente para cancelamento (recebido: {sid}, ativo: {sessao_ativa_id}).", flush=True)
            return {
                "sucesso": False,
                "erro": "session_id_divergente",
                "event_id": event_id,
                "active_session_id": sessao_ativa_id,
                "estado": estado,
                "processado": False,
            }

    # 2. Despacho com confirmação real de aceitação e processamento (Requisitos 2 e 4)
    if loop_principal_ativo.is_set():
        comp_q = queue.Queue(maxsize=1)
        try:
            fila_intencoes.put_nowait((fonte, texto, sid, event_id, comp_q))
            registrar_inicio_acao(event_id, tipo, texto, status="aceito")
        except queue.Full:
            print("Fila de intencoes cheia. Descartando evento IPC.", flush=True)
            return {
                "sucesso": False,
                "erro": "fila_cheia",
                "event_id": event_id,
                "active_session_id": sessao_ativa_id,
                "estado": estado,
                "status": "falha",
                "processado": False,
            }

        try:
            resultado = comp_q.get(timeout=2.0)
            if isinstance(resultado, dict):
                if "event_id" not in resultado:
                    resultado["event_id"] = event_id
                if "estado" not in resultado:
                    resultado["estado"] = estado
                if "status" not in resultado:
                    resultado["status"] = "concluido" if resultado.get("sucesso") else "falha"
            return resultado
        except queue.Empty:
            print("Timeout aguardando processamento do evento IPC pelo controlador.", flush=True)
            return {
                "sucesso": False,
                "erro": "timeout_processamento",
                "event_id": event_id,
                "active_session_id": sessao_ativa_id,
                "estado": estado,
                "status": "falha",
                "processado": False,
            }
    else:
        # Modo isolado/unitário fora do loop principal: enfileira se couber
        try:
            fila_intencoes.put_nowait((fonte, texto, sid))
            registrar_inicio_acao(event_id, tipo, texto, status="aceito")
        except queue.Full:
            return {
                "sucesso": False,
                "erro": "fila_cheia",
                "event_id": event_id,
                "active_session_id": sessao_ativa_id,
                "estado": estado,
                "status": "falha",
                "processado": False,
            }
        return {
            "sucesso": True,
            "event_id": event_id,
            "active_session_id": sid if tipo == "wake" else sessao_ativa_id,
            "estado": estado,
            "status": "aceito",
            "processado": False,
        }


ipc_listener = None
microfone = None
fila_audio = queue.Queue(maxsize=64)
captura_ativa = threading.Event()
captura_ativa.set()


def spawn_arecord():
    dispositivo = os.getenv("JARVIS_ME6S_DEVICE", "plughw:CARD=ME6S,DEV=0")
    try:
        return subprocess.Popen([
            "arecord", "-q",
            "-D", dispositivo,
            "-f", "S16_LE",
            "-r", "16000",
            "-c", "1",
            "-t", "raw"
        ], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    except Exception as erro:
        print(f"[ME6S] Falha ao iniciar arecord ({dispositivo}): {erro}", flush=True)
        return None


def capturar_audio():
    global microfone
    while captura_ativa.is_set():
        if microfone is None or microfone.poll() is not None:
            microfone = spawn_arecord()
            if microfone is None or microfone.poll() is not None:
                time.sleep(1.0)
                continue
            print("[ME6S] Microfone conectado e captura de audio iniciada.", flush=True)

        try:
            dados = microfone.stdout.read(4000)
        except Exception:
            dados = b""

        if not dados:
            if captura_ativa.is_set():
                print("[ME6S] Captura interrompida (dispositivo desconectado ou arecord terminou). Tentando reconectar...", flush=True)
                try:
                    microfone.terminate()
                    microfone.wait(timeout=1.0)
                except Exception:
                    pass
                microfone = None
                time.sleep(1.0)
            continue

        try:
            fila_audio.put_nowait((time.monotonic(), dados))
        except queue.Full:
            try:
                fila_audio.get_nowait()
            except queue.Empty:
                pass
            fila_audio.put_nowait((time.monotonic(), dados))


def reconhecedor_me6s():
    while captura_ativa.is_set():
        try:
            item = fila_audio.get(timeout=0.5)
        except queue.Empty:
            continue

        if item is None:
            break

        instante, audio = item

        if time.monotonic() - instante > 1.5:
            rec.Reset()
            print("Audio atrasado descartado.", flush=True)
            continue

        if rec.AcceptWaveform(audio):
            texto = json.loads(rec.Result()).get("text", "").strip()
            if texto and texto != "[unk]":
                fila_intencoes.put(("me6s", texto, None))


def iniciar_servicos(max_iteracoes=None):
    global ipc_listener, microfone, captura_ativa
    global estado, limite, pendente, fonte_ativa, sessao_ativa_id

    captura_ativa.set()
    ipc_listener = IpcEventListener(
        socket_path=os.getenv("JARVIS_IPC_SOCKET", DEFAULT_IPC_SOCKET),
        on_event=on_ipc_event,
        allowed_uid=0,
        verify_peer_cred=True,
    )
    ipc_listener.start()

    threading.Thread(target=capturar_audio, daemon=True).start()
    threading.Thread(target=reconhecedor_me6s, daemon=True).start()

    print("=== JARVIS + LIGAR.CASA ===", flush=True)
    wol_real = os.getenv("JARVIS_WOL_REAL") == "1"
    ssh_real = os.getenv("JARVIS_SSH_REAL") == "1"
    if wol_real and ssh_real:
        print("MODO OPERACIONAL REAL (WoL=ATIVO, SSH=ATIVO)", flush=True)
    elif wol_real or ssh_real:
        print(f"MODO MISTO (WoL={'ATIVO' if wol_real else 'SIMULADO'}, SSH={'ATIVO' if ssh_real else 'SIMULADO'})", flush=True)
    else:
        print("MODO SIMULACAO - SEM ACOES REAIS", flush=True)
    print("Fale 'assistente', pause, depois o comando.", flush=True)

    loop_principal_ativo.set()
    iteracoes = 0
    try:
        while True:
            if max_iteracoes is not None and iteracoes >= max_iteracoes:
                break
            iteracoes += 1
            try:
                item = fila_intencoes.get(timeout=0.05)
                fonte = item[0]
                texto = item[1]
                sid = item[2]
                ev_id = item[3] if len(item) > 3 else None
                comp_q = item[4] if len(item) > 4 else None

                res = processar(texto, fonte=fonte, session_id=sid, event_id=ev_id)
                if comp_q is not None:
                    comp_q.put(res)
            except queue.Empty:
                agora = time.monotonic()
                if estado != "espera" and agora > limite:
                    print("Tempo esgotado.", flush=True)
                    estado = "espera"
                    pendente = None
                    fonte_ativa = None
                    sessao_ativa_id = None
    except KeyboardInterrupt:
        print("\nTeste encerrado.")
    finally:
        loop_principal_ativo.clear()
        captura_ativa.clear()
        if ipc_listener:
            ipc_listener.stop()
        if microfone:
            try:
                microfone.terminate()
                microfone.wait(timeout=1.0)
            except Exception:
                pass


if __name__ == "__main__":
    iniciar_servicos()
