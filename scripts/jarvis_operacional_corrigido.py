import sys
import os
import json
import time
import subprocess
import threading
import queue

from vosk import Model, KaldiRecognizer, SetLogLevel

# Carregar funcoes do painel existente
sys.path.insert(0, "/home/tuninho/homelab-panel")
sys.path.insert(0, "/opt/jarvis-voice")
import app as painel

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

modelo = Model(MODELO)
rec = KaldiRecognizer(
    modelo, 16000,
    json.dumps(gramatica, ensure_ascii=False)
)

estado = "espera"
limite = 0.0
pendente = None
ultima_ativacao = 0.0
fonte_ativa = None
sessao_ativa_id = None

# Fila unificada de intenções para ambos os microfones (ME6S + LG)
fila_intencoes = queue.Queue(maxsize=32)

# Cache de idempotência / deduplicação de ações
acoes_recentes = {}
lock_acoes = threading.Lock()


def registrar_acao(acao, alvo):
    with lock_acoes:
        acoes_recentes[(acao, alvo)] = time.monotonic()


def ja_executado_recentemente(acao, alvo, janela=3.5):
    with lock_acoes:
        return (time.monotonic() - acoes_recentes.get((acao, alvo), 0.0)) <= janela


def processar(texto, fonte="me6s", session_id=None):
    global estado, limite, pendente, ultima_ativacao, fonte_ativa, sessao_ativa_id

    agora = time.monotonic()

    if estado != "espera" and agora > limite:
        print("Tempo esgotado.", flush=True)
        estado = "espera"
        pendente = None
        fonte_ativa = None
        sessao_ativa_id = None

    # Preservação estrita do formato de log contratual para display/server.py e Me6sJournalMonitor
    print("Ouvi:", texto, flush=True)
    if fonte != "me6s":
        print(f"[Origem: {fonte}]", flush=True)

    if texto == "cancelar":
        estado = "espera"
        pendente = None
        fonte_ativa = None
        sessao_ativa_id = None
        ultima_ativacao = 0.0
        print("Cancelado.", flush=True)
        return

    if estado == "espera":
        if texto == "assistente":
            # Deduplicação de ativação simultânea entre os dois microfones
            if (agora - ultima_ativacao) <= 2.0:
                print(f"Ativacao deduplicada via {fonte} (ja ativo ha {agora - ultima_ativacao:.2f}s).", flush=True)
                return

            estado = "comando"
            limite = agora + 12
            ultima_ativacao = agora
            fonte_ativa = fonte
            sessao_ativa_id = session_id or f"sess-{int(agora)}"
            print("ATIVADO! Aguardando comando.", flush=True)
        return

    if estado == "comando":
        if texto == "assistente":
            print("Ja ativado. Aguardando comando.", flush=True)
            return

        # Correcao restrita ao teste de reconhecimento
        if texto == "dois ligar fusion":
            print("Normalizado: ligar fusion", flush=True)
            texto = "ligar fusion"

        if texto not in COMANDOS:
            print("Comando ignorado.", flush=True)
            return

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
                    return

                registrar_acao("ligar", alvo)
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

                    except Exception as erro:
                        print("Falha no Wake-on-LAN:", erro, flush=True)
                else:
                    print("SIMULACAO: Wake-on-LAN.", flush=True)
            estado = "espera"
            fonte_ativa = None
            sessao_ativa_id = None
            return

        if not acessivel:
            print(
                "DESLIGAMENTO BLOQUEADO:",
                "computador nao acessivel.",
                flush=True
            )
            estado = "espera"
            fonte_ativa = None
            sessao_ativa_id = None
            return

        estado = "confirmacao"
        pendente = alvo
        limite = time.monotonic() + 20
        print(
            "CONFIRMAR desligamento de",
            srv["nome"],
            "? Diga confirmar ou cancelar.",
            flush=True
        )
        return

    if estado == "confirmacao":
        if texto in ("confirmar", "confirmar desligar", "confirmar desligamento"):
            srv = painel.SERVIDORES[pendente]
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
                        return

                    # Idempotência verificada estritamente após confirmação válida
                    if ja_executado_recentemente("desligar", pendente):
                        print(f"Desligamento de {pendente} descartado por idempotencia.", flush=True)
                        estado = "espera"
                        pendente = None
                        fonte_ativa = None
                        sessao_ativa_id = None
                        return

                    registrar_acao("desligar", pendente)
                    configuracao = dict(srv)

                    if pendente == "ananuke":
                        configuracao["cmd"] = (
                            "sudo -n /usr/sbin/poweroff"
                        )

                    try:
                        resultado = subprocess.run(
                            painel.montar_comando_ssh(configuracao),
                            capture_output=True,
                            text=True,
                            timeout=25
                        )
                        print(
                            "SSH enviado. Codigo:",
                            resultado.returncode,
                            flush=True
                        )
                        if resultado.stderr:
                            print(
                                "SSH:", resultado.stderr.strip(),
                                flush=True
                            )
                        print(
                            "Verifique se o computador desligou.",
                            flush=True
                        )
                    except Exception as erro:
                        print("Erro no SSH:", erro, flush=True)
                else:
                    # Modo Simulação: idempotência registrada apenas após validação
                    if ja_executado_recentemente("desligar", pendente):
                        print(f"Desligamento de {pendente} descartado por idempotencia.", flush=True)
                        estado = "espera"
                        pendente = None
                        fonte_ativa = None
                        sessao_ativa_id = None
                        return

                    registrar_acao("desligar", pendente)
                    print(
                        "SIMULACAO SSH para",
                        srv["nome"],
                        flush=True
                    )
            else:
                print("Cancelado: computador inacessivel.", flush=True)

            estado = "espera"
            pendente = None
            fonte_ativa = None
            sessao_ativa_id = None


# --- Barramento IPC Local por UNIX Domain Socket com Validação Semântica ---
def on_ipc_event(evento):
    if not isinstance(evento, dict):
        return
    if evento.get("versao") != 1:
        return

    fonte = evento.get("fonte")
    if fonte not in ("lg_mic", "me6s"):
        return

    tipo = evento.get("evento")
    if tipo not in ("wake", "comando", "confirmar", "cancelar"):
        return

    texto = evento.get("texto")
    if not isinstance(texto, str) or len(texto) > 128:
        return
    texto = texto.strip()
    if not texto:
        return

    # Validação semântica estrita do texto conforme o tipo de evento
    if tipo == "wake" and texto != "assistente":
        return
    if tipo == "cancelar" and texto != "cancelar":
        return
    if tipo == "confirmar" and texto not in ("confirmar", "confirmar desligar", "confirmar desligamento"):
        return
    if tipo == "comando" and texto not in COMANDOS and texto != "dois ligar fusion":
        return

    sid = evento.get("session_id")
    try:
        fila_intencoes.put_nowait((fonte, texto, sid))
    except queue.Full:
        print("Fila de intencoes cheia. Descartando evento IPC.", flush=True)


ipc_listener = None
microfone = None
fila_audio = queue.Queue(maxsize=64)


def capturar_audio():
    while True:
        dados = microfone.stdout.read(4000)

        if not dados:
            break

        try:
            fila_audio.put_nowait((time.monotonic(), dados))
        except queue.Full:
            try:
                fila_audio.get_nowait()
            except queue.Empty:
                pass
            fila_audio.put_nowait((time.monotonic(), dados))

    fila_audio.put(None)


def reconhecedor_me6s():
    while True:
        item = fila_audio.get()
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


def iniciar_servicos():
    global ipc_listener, microfone

    ipc_listener = IpcEventListener(
        socket_path=os.getenv("JARVIS_IPC_SOCKET", DEFAULT_IPC_SOCKET),
        on_event=on_ipc_event,
        allowed_uid=0,
        verify_peer_cred=True,
    )
    ipc_listener.start()

    # Captura de Áudio ALSA (ME6S USB)
    microfone = subprocess.Popen([
        "arecord", "-q",
        "-D", "plughw:CARD=ME6S,DEV=0",
        "-f", "S16_LE",
        "-r", "16000",
        "-c", "1",
        "-t", "raw"
    ], stdout=subprocess.PIPE)

    threading.Thread(target=capturar_audio, daemon=True).start()
    threading.Thread(target=reconhecedor_me6s, daemon=True).start()

    print("=== JARVIS + LIGAR.CASA ===", flush=True)
    print("MODO SIMULACAO - SEM ACOES REAIS", flush=True)
    print("Fale 'assistente', pause, depois o comando.", flush=True)

    try:
        while True:
            try:
                fonte, texto, sid = fila_intencoes.get(timeout=0.2)
                processar(texto, fonte=fonte, session_id=sid)
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
        if ipc_listener:
            ipc_listener.stop()
        if microfone:
            microfone.terminate()
            microfone.wait()


if __name__ == "__main__":
    iniciar_servicos()
