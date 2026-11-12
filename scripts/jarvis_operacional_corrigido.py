import sys
import os
import json
import time
import tempfile
import subprocess
import threading
import queue

from vosk import Model, KaldiRecognizer, SetLogLevel

# Carregar funcoes do painel existente
sys.path.insert(0, "/home/tuninho/homelab-panel")
import app as painel

from gateway.ipc_bus import DEFAULT_IPC_SOCKET, IpcEventListener

# Suporte ao módulo Jarvis PC Control V1
_ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT_DIR not in sys.path:
    sys.path.insert(0, _ROOT_DIR)

try:
    from pc_control.catalog import (
        ALL_COMMANDS,
        CONFIRMATION_PHRASES,
        VOSK_GRAMMAR,
        is_pc_control_action,
        requires_confirmation,
        is_valid_confirmation,
        get_friendly_target_name,
        get_friendly_action_name,
    )
    from pc_control.config import is_pc_control_enabled
    from pc_control.dispatcher import PcControlDispatcher
    COMANDOS = ALL_COMMANDS
    gramatica = VOSK_GRAMMAR
    pc_dispatcher = PcControlDispatcher()
    _PC_CONTROL_AVAILABLE = True
except ImportError:
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
    def is_pc_control_action(a): return False
    def requires_confirmation(a): return a == "desligar"
    def is_valid_confirmation(t, a=None): return t in ("confirmar", "confirmar desligar", "confirmar desligamento")
    def is_pc_control_enabled(): return False
    def get_friendly_target_name(t): return t
    def get_friendly_action_name(a): return a
    pc_dispatcher = None
    _PC_CONTROL_AVAILABLE = False

SetLogLevel(-1)

MODELO = "/opt/jarvis-voice/vosk-model-small-pt-0.3"

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
pendente_acao = None
ultima_ativacao = 0.0
fonte_ativa = None
sessao_ativa_id = None

# Fila unificada de intenções para ambos os microfones (ME6S + LG)
fila_intencoes = queue.Queue(maxsize=32)
loop_principal_ativo = threading.Event()

# --- Persistência de Estado e Proteção contra Execuções Duplicadas (Mesmo após reinício) ---
lock_acoes = threading.Lock()
acoes_recentes = {}
registro_acoes = {}

DEFAULT_STATE_FILE = "/var/lib/jarvis/acoes_recentes.json"

# --- Proteção de Execução Única para SSH Real (One-Shot Token) ---
DEFAULT_ONE_SHOT_TOKEN_FILE = "/var/lib/jarvis/one_shot_ssh_token.json"
_ONE_SHOT_SSH_CONSUMED_IN_MEMORY = False
_LOCK_ONE_SHOT_TOKEN = threading.RLock()


def _obter_caminho_token_one_shot():
    caminho = os.getenv("JARVIS_ONE_SHOT_TOKEN_FILE")
    if caminho:
        return caminho
    return DEFAULT_ONE_SHOT_TOKEN_FILE


def _resetar_estado_token_memoria():
    """Auxiliar para testes e controle de ciclo de vida: reseta a trava em memória."""
    global _ONE_SHOT_SSH_CONSUMED_IN_MEMORY
    with _LOCK_ONE_SHOT_TOKEN:
        _ONE_SHOT_SSH_CONSUMED_IN_MEMORY = False


def token_one_shot_requerido():
    """Verifica se o token de execução única é estritamente obrigatório.
    Fail-closed: Obrigatório sempre que JARVIS_SSH_REAL=1 ou JARVIS_REQUIRE_ONE_SHOT_TOKEN=1.
    Apenas desativado se explicitamente configurado como '0' (ex: testes legados sem token).
    """
    if os.getenv("JARVIS_REQUIRE_ONE_SHOT_TOKEN") == "0":
        return False
    if os.getenv("JARVIS_SSH_REAL") == "1":
        return True
    if os.getenv("JARVIS_REQUIRE_ONE_SHOT_TOKEN") == "1":
        return True
    caminho = _obter_caminho_token_one_shot()
    if os.path.exists(caminho):
        return True
    return False


def verificar_token_one_shot(alvo, acao="desligar"):
    """Verifica se existe um token de execução única válido para o alvo e ação.
    Retorna (valido: bool, detalhe_ou_motivo: dict | str).
    """
    global _ONE_SHOT_SSH_CONSUMED_IN_MEMORY
    with _LOCK_ONE_SHOT_TOKEN:
        if _ONE_SHOT_SSH_CONSUMED_IN_MEMORY:
            return False, "token_ja_consumido_na_memoria_deste_processo"

        caminho = _obter_caminho_token_one_shot()
        if not os.path.exists(caminho):
            return False, "token_arquivo_inexistente"

        try:
            with open(caminho, "r", encoding="utf-8") as f:
                dados = json.load(f)
        except Exception as e:
            return False, f"token_corrompido: {e}"

        if not isinstance(dados, dict):
            return False, "token_formato_invalido"

        if dados.get("status") != "active":
            return False, f"token_status_inativo: {dados.get('status')}"

        if dados.get("consumed") is True:
            return False, "token_ja_consumido"

        alvo_token = str(dados.get("alvo", "")).strip().lower()
        alvo_esperado = str(alvo).strip().lower()
        if alvo_token != alvo_esperado:
            return False, f"token_alvo_incompativel: {alvo_token} != {alvo_esperado}"

        acao_token = str(dados.get("acao", "")).strip().lower()
        acao_esperada = str(acao).strip().lower()
        if acao_token != acao_esperada:
            return False, f"token_acao_incompativel: {acao_token} != {acao_esperada}"

        agora = time.time()
        expires_at = dados.get("expires_at", 0.0)
        if not isinstance(expires_at, (int, float)) or agora > expires_at:
            return False, f"token_expirado: {agora} > {expires_at}"

        bound_pid = dados.get("bound_service_pid")
        if bound_pid is not None and int(bound_pid) != os.getpid():
            return False, f"token_invalido_por_reinicio: bound_pid {bound_pid} != pid atual {os.getpid()}"

        return True, dados


def consumir_token_one_shot(alvo, acao="desligar", event_id=None):
    """Consome atomicamente o token one-shot para permitir uma única tentativa SSH real.
    Garante que o token seja consumido antes do despacho e nunca reutilizado.
    """
    global _ONE_SHOT_SSH_CONSUMED_IN_MEMORY
    with _LOCK_ONE_SHOT_TOKEN:
        valido, res = verificar_token_one_shot(alvo, acao)
        if not valido:
            return False, res

        dados = res
        caminho = _obter_caminho_token_one_shot()
        agora = time.time()

        dados["consumed"] = True
        dados["consumed_at"] = agora
        dados["consumed_by_event"] = event_id
        dados["consumed_by_pid"] = os.getpid()
        dados["status"] = "consumed"

        try:
            diretorio = os.path.dirname(os.path.abspath(caminho)) or "."
            os.makedirs(diretorio, exist_ok=True)
            fd, tmp_file = tempfile.mkstemp(prefix="one_shot_token_", dir=diretorio)
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(dados, f, indent=2, sort_keys=True)
                f.flush()
                os.fsync(f.fileno())
            try:
                os.chmod(tmp_file, 0o600)
            except OSError:
                pass
            os.replace(tmp_file, caminho)
        except Exception as e:
            if "tmp_file" in locals() and os.path.exists(tmp_file):
                try:
                    os.unlink(tmp_file)
                except OSError:
                    pass
            return False, f"falha_persistencia_token: {e}"

        _ONE_SHOT_SSH_CONSUMED_IN_MEMORY = True
        return True, dados


def finalizar_token_one_shot(alvo=None, event_id=None, resultado=None, erro=None):
    """Atualiza o token one-shot após a conclusão da tentativa SSH, notificando o watchdog."""
    caminho = _obter_caminho_token_one_shot()
    if not os.path.exists(caminho):
        return
    with _LOCK_ONE_SHOT_TOKEN:
        try:
            with open(caminho, "r", encoding="utf-8") as f:
                dados = json.load(f)
            if isinstance(dados, dict):
                dados["finished"] = True
                dados["finished_at"] = time.time()
                dados["execution_result"] = resultado
                dados["execution_error"] = erro
                diretorio = os.path.dirname(os.path.abspath(caminho)) or "."
                fd, tmp_file = tempfile.mkstemp(prefix="one_shot_token_", dir=diretorio)
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    json.dump(dados, f, indent=2, sort_keys=True)
                    f.flush()
                    os.fsync(f.fileno())
                try:
                    os.chmod(tmp_file, 0o600)
                except OSError:
                    pass
                os.replace(tmp_file, caminho)
        except Exception as e:
            print(f"Aviso ao finalizar token one-shot: {e}", flush=True)


def _reconciliar_token_one_shot_inicializacao():
    """Invalida tokens one-shot pendentes na reinicialização do serviço (fail-closed)."""
    caminho = _obter_caminho_token_one_shot()
    if not os.path.exists(caminho):
        return
    try:
        with open(caminho, "r", encoding="utf-8") as f:
            dados = json.load(f)
        if isinstance(dados, dict) and dados.get("status") == "active" and not dados.get("consumed"):
            bound_pid = dados.get("bound_service_pid")
            if bound_pid is not None and int(bound_pid) != os.getpid():
                dados["status"] = "invalidated_by_restart"
                dados["invalidated_at"] = time.time()
                dados["invalidation_reason"] = f"service_restarted_pid_{os.getpid()}_replaces_{bound_pid}"
                diretorio = os.path.dirname(os.path.abspath(caminho)) or "."
                fd, tmp_file = tempfile.mkstemp(prefix="one_shot_token_", dir=diretorio)
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    json.dump(dados, f, indent=2, sort_keys=True)
                try:
                    os.chmod(tmp_file, 0o600)
                except OSError:
                    pass
                os.replace(tmp_file, caminho)
                print(
                    f"RECUPERACAO: Token one-shot ({dados.get('token_id')}) invalidado devido a reinicializacao do servico.",
                    flush=True,
                )
    except Exception as e:
        print(f"Aviso ao reconciliar token one-shot na inicializacao: {e}", flush=True)


def _obter_caminho_estado():
    # Permite configuração explícita via variável de ambiente (ex.: testes isolados)
    caminho = os.getenv("JARVIS_STATE_FILE")
    if caminho:
        return caminho
    return DEFAULT_STATE_FILE


def _salvar_estado_acoes():
    """Salva estado no arquivo persistente de forma atômica com permissões 0600.
    Retorna (True, None) em caso de sucesso, ou (False, mensagem_erro) em caso de falha.
    NÃO realiza fallback silencioso para /tmp.
    """
    caminho = _obter_caminho_estado()
    pasta = os.path.dirname(caminho)
    if pasta and not os.path.exists(pasta):
        try:
            os.makedirs(pasta, mode=0o750, exist_ok=True)
        except OSError as e:
            msg = f"Impossivel criar diretorio de persistencia '{pasta}': {e}"
            print(f"FALHA PERSISTENCIA: {msg}", flush=True)
            return False, msg

    dados = {
        "acoes_recentes": acoes_recentes,
        "registro_acoes": registro_acoes,
        "timestamp_atualizacao": time.time(),
    }
    tmp_path = f"{caminho}.tmp.{os.getpid()}_{int(time.time() * 1000)}"
    try:
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(dados, f, ensure_ascii=False)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, caminho)
        if pasta:
            try:
                dir_fd = os.open(pasta, os.O_RDONLY)
                try:
                    os.fsync(dir_fd)
                finally:
                    os.close(dir_fd)
            except OSError:
                pass
        try:
            os.chmod(caminho, 0o600)
        except OSError:
            pass
        return True, None
    except Exception as e:
        msg = f"Falha ao gravar estado persistente em '{caminho}': {e}"
        print(f"FALHA PERSISTENCIA: {msg}", flush=True)
        if os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except OSError:
                pass
        return False, msg


def _carregar_estado_acoes():
    """Carrega estado do disco na memória se existir."""
    global acoes_recentes, registro_acoes
    caminho = _obter_caminho_estado()
    if not os.path.exists(caminho):
        return
    try:
        with open(caminho, "r", encoding="utf-8") as f:
            dados = json.load(f)
        agora = time.time()
        for chave, ts in dados.get("acoes_recentes", {}).items():
            if isinstance(ts, (int, float)) and (agora - ts) <= 300.0:
                acoes_recentes[chave] = ts
        for ev_id, reg in dados.get("registro_acoes", {}).items():
            ts_inicio = reg.get("timestamp_inicio", 0)
            if isinstance(ts_inicio, (int, float)) and (agora - ts_inicio) <= 300.0:
                registro_acoes[ev_id] = reg
    except Exception as e:
        print(f"Aviso: falha ao carregar estado de acoes: {e}", flush=True)


def _recuperar_estado_na_inicializacao():
    """Executado na inicialização/reinício do controlador.
    Identifica operações de hardware (desligar/ligar) que estavam em andamento quando
    o processo anterior morreu e as transiciona para 'indeterminado', prevenindo
    qualquer repetição automática (Requisito 3).
    """
    global acoes_recentes, registro_acoes
    caminho = _obter_caminho_estado()
    if not os.path.exists(caminho):
        return
    with lock_acoes:
        _carregar_estado_acoes()
        agora = time.time()
        houve_interrupcao = False
        for ev_id, reg in list(registro_acoes.items()):
            if reg.get("status") in ("em_processamento", "aceito") and reg.get("acao") in ("desligar", "ligar"):
                reg["status"] = "indeterminado"
                reg["timestamp_conclusao"] = agora
                reg["erro"] = "operacao_interrompida_por_reinicio"
                houve_interrupcao = True
                acao = reg.get("acao")
                alvo = reg.get("alvo")
                print(
                    f"RECUPERACAO: Operacao '{acao}' para '{alvo}' (ID: {ev_id}) "
                    f"estava em andamento durante o reinicio. Marcada como INDETERMINADO.",
                    flush=True,
                )
                if acao and alvo:
                    acoes_recentes[f"{acao}:{alvo}"] = agora
        if houve_interrupcao:
            _salvar_estado_acoes()
    _reconciliar_token_one_shot_inicializacao()


def registrar_inicio_acao(event_id, acao, alvo, status="aceito"):
    ev_key = event_id or f"ev-{int(time.time() * 1000)}"
    with lock_acoes:
        registro_acoes[ev_key] = {
            "event_id": ev_key,
            "acao": acao,
            "alvo": alvo,
            "status": status,
            "timestamp_inicio": time.time(),
            "timestamp_conclusao": None,
            "resultado": None,
            "erro": None,
        }
        if status in ("concluido", "em_processamento"):
            chave = f"{acao}:{alvo}"
            acoes_recentes[chave] = time.time()
        ok, erro = _salvar_estado_acoes()
        return ok, erro


def atualizar_status_acao(event_id, status, acao=None, alvo=None, resultado=None, erro=None):
    if not event_id:
        return False, "event_id_invalido"
    with lock_acoes:
        _carregar_estado_acoes()
        if event_id not in registro_acoes:
            registro_acoes[event_id] = {
                "event_id": event_id,
                "acao": acao,
                "alvo": alvo,
                "status": status,
                "timestamp_inicio": time.time(),
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
            if status in ("concluido", "falha", "indeterminado"):
                reg["timestamp_conclusao"] = time.time()
        if acao and alvo and status in ("concluido", "em_processamento"):
            acoes_recentes[f"{acao}:{alvo}"] = time.time()
        ok, erro = _salvar_estado_acoes()
        return ok, erro


def obter_status_acao(event_id=None, acao=None, alvo=None):
    with lock_acoes:
        _carregar_estado_acoes()
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
        _carregar_estado_acoes()
        chave = f"{acao}:{alvo}"
        acoes_recentes[chave] = time.time()
        ok, erro = _salvar_estado_acoes()
        return ok, erro


def ja_executado_recentemente(acao, alvo, janela=3.5):
    with lock_acoes:
        _carregar_estado_acoes()
        agora = time.time()
        # 1. Bloqueia se há ação idêntica em andamento iniciada há menos de 30s
        for reg in registro_acoes.values():
            if reg.get("acao") == acao and reg.get("alvo") == alvo:
                st = reg.get("status")
                ts = reg.get("timestamp_inicio", 0)
                if st == "em_processamento" and (agora - ts) <= 30.0:
                    return True
                if st == "indeterminado" and (agora - ts) <= janela:
                    return True
        # 2. Bloqueia se a mesma ação foi concluída ou tentada dentro da janela de idempotência
        chave = f"{acao}:{alvo}"
        ultimo_ts = acoes_recentes.get(chave, 0.0)
        return (agora - ultimo_ts) <= janela


def alvo_real_permitido(alvo_nome):
    """Verifica se o alvo está expressamente autorizado na lista de controle seguro.
    Se JARVIS_ALLOWED_REAL_TARGETS não estiver definido ou vazio, nenhum alvo é autorizado (fail-closed).
    """
    if not alvo_nome:
        return False
    raw = os.getenv("JARVIS_ALLOWED_REAL_TARGETS", "")
    if not raw or not raw.strip():
        return False
    permitidos = [a.strip().strip('"\'').lower() for a in raw.split(",") if a.strip()]
    return str(alvo_nome).strip().strip('"\'').lower() in permitidos


_recuperar_estado_na_inicializacao()


def processar(texto, fonte="me6s", session_id=None, event_id=None):
    global estado, limite, pendente, pendente_acao, ultima_ativacao, fonte_ativa, sessao_ativa_id

    agora = time.monotonic()

    # Verificação de retransmissão de evento já registrado no histórico persistente
    if event_id and texto not in ("assistente", "cancelar"):
        reg_existente = obter_status_acao(event_id=event_id)
        if reg_existente and reg_existente.get("status") in ("concluido", "em_processamento", "indeterminado", "falha"):
            st = reg_existente.get("status")
            print(f"Retransmissao de acao recuperada do estado persistente ({event_id}, status: {st}).", flush=True)
            return {
                "sucesso": True if st == "concluido" else False,
                "event_id": event_id,
                "acao": reg_existente.get("acao"),
                "alvo": reg_existente.get("alvo"),
                "status": st,
                "erro": reg_existente.get("erro"),
                "active_session_id": sessao_ativa_id,
                "estado": estado,
                "processado": True,
                "retransmissao": True,
            }

    if estado != "espera" and agora > limite:
        print("Tempo esgotado.", flush=True)
        print("Tempo esgotado; pedido cancelado.", flush=True)
        estado = "espera"
        pendente = None
        pendente_acao = None
        fonte_ativa = None
        sessao_ativa_id = None

    # Preservação estrita do formato de log contratual para display/server.py e Me6sJournalMonitor
    if fonte != "me6s":
        print(f"[Origem: {fonte}]", flush=True)
    print("Ouvi:", texto, flush=True)

    if texto == "cancelar":
        estado = "espera"
        pendente = None
        pendente_acao = None
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
            "status": "cancelado",
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

        if is_pc_control_action(acao):
            if not is_pc_control_enabled():
                print(f"[PC-CONTROL-DESATIVADO] Comando '{acao}' para '{alvo}' ignorado pois JARVIS_PC_CONTROL_ENABLED=0.", flush=True)
                estado = "espera"
                fonte_ativa = None
                sessao_ativa_id = None
                pendente = None
                pendente_acao = None
                if event_id:
                    atualizar_status_acao(
                        event_id,
                        "cancelado",
                        acao=acao,
                        alvo=alvo,
                        erro="pc_control_desativado",
                    )
                return {
                    "sucesso": False,
                    "erro": "pc_control_desativado",
                    "event_id": event_id,
                    "acao": acao,
                    "alvo": alvo,
                    "active_session_id": None,
                    "estado": "espera",
                    "status": "cancelado",
                    "processado": True,
                }

            if requires_confirmation(acao):
                estado = "confirmacao"
                pendente = alvo
                pendente_acao = acao
                limite = time.monotonic() + 20
                if acao == "fechar_navegador":
                    msg_aviso = f'Confirme fechar navegador em {srv["nome"]}: diga "confirmar" ou "cancelar".'
                else:
                    msg_aviso = f'Confirme reinicialização de {srv["nome"]}: diga "confirmar" ou "cancelar".'
                print(msg_aviso, flush=True)
                if event_id:
                    atualizar_status_acao(
                        event_id,
                        "confirmacao_pendente",
                        acao=acao,
                        alvo=alvo,
                        resultado={"estado": "confirmacao"}
                    )
                return {
                    "sucesso": True,
                    "status": "confirmacao_pendente",
                    "event_id": event_id,
                    "acao": f"{acao}_pendente",
                    "alvo": alvo,
                    "active_session_id": sessao_ativa_id,
                    "estado": "confirmacao",
                    "processado": True,
                }

            # Ação de PC Control imediata
            if ja_executado_recentemente(acao, alvo):
                print(f"Acao '{acao}' para {alvo} descartada por idempotencia.", flush=True)
                estado = "espera"
                fonte_ativa = None
                sessao_ativa_id = None
                pendente = None
                pendente_acao = None
                return {
                    "sucesso": True,
                    "event_id": event_id,
                    "acao": acao,
                    "alvo": alvo,
                    "deduplicado": True,
                    "active_session_id": None,
                    "estado": "espera",
                    "status": "concluido",
                    "processado": True,
                }

            registrar_acao(acao, alvo)
            if event_id:
                registrar_inicio_acao(event_id, acao, alvo, status="em_processamento")

            salvo_sid = session_id or sessao_ativa_id
            estado = "espera"
            fonte_ativa = None
            sessao_ativa_id = None
            pendente = None
            pendente_acao = None

            res_disp = pc_dispatcher.processar_comando_ou_confirmacao(
                acao=acao,
                alvo=alvo,
                texto_falado=texto,
                session_id=salvo_sid,
                event_id=event_id or f"ev-pc-{int(time.time()*1000)}",
                fonte=fonte,
            ) if pc_dispatcher else {"sucesso": False, "status": "falha", "erro": "dispatcher_indisponivel"}

            print(f"PC Control: {res_disp.get('mensagem_amigavel', '')}", flush=True)
            if event_id:
                atualizar_status_acao(
                    event_id,
                    res_disp.get("status", "concluido"),
                    acao=acao,
                    alvo=alvo,
                    resultado=res_disp.get("resultado"),
                    erro=res_disp.get("erro"),
                )
            return {
                "sucesso": res_disp.get("sucesso", False),
                "event_id": event_id,
                "acao": acao,
                "alvo": alvo,
                "active_session_id": None,
                "estado": "espera",
                "status": res_disp.get("status", "concluido"),
                "resultado": res_disp.get("resultado"),
                "erro": res_disp.get("erro"),
                "processado": True,
            }

        if acao == "ligar":
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
                    "status": "concluido",
                    "processado": True,
                }

            registrar_acao("ligar", alvo)
            ev_wol = event_id or f"ev-wol-{int(time.time() * 1000)}"
            salvo_ok, erro_salvar = registrar_inicio_acao(ev_wol, "ligar", alvo, status="aceito")
            if not salvo_ok:
                print(f"FALHA NA PERSISTENCIA: {erro_salvar}", flush=True)
                if os.getenv("JARVIS_WOL_REAL") == "1" or os.getenv("JARVIS_REQUIRE_PERSISTENCE") == "1":
                    print("FALHA CRITICA: WoL real BLOQUEADO por seguranca (falha ao persistir estado).", flush=True)
                    estado = "espera"
                    fonte_ativa = None
                    sessao_ativa_id = None
                    return {
                        "sucesso": False,
                        "erro": "falha_persistencia_estado",
                        "event_id": event_id,
                        "acao": "ligar",
                        "alvo": alvo,
                        "active_session_id": None,
                        "estado": "espera",
                        "status": "falha",
                        "processado": True,
                    }

            alvo_autorizado = alvo_real_permitido(alvo)
            modo_real = (os.getenv("JARVIS_WOL_REAL") == "1") and alvo_autorizado
            atraso_wol = getattr(painel, "simular_atraso_wol_s", 0.0)

            estado = "espera"
            fonte_ativa = None
            sessao_ativa_id = None
            ultima_ativacao = 0.0

            def _executar_wol_worker(srv_info, ev_id, alvo_nome):
                try:
                    atualizar_status_acao(ev_id, "em_processamento", acao="ligar", alvo=alvo_nome)
                    t_ini_sond = time.monotonic()
                    alvo_worker_autorizado = alvo_real_permitido(alvo_nome)
                    modo_real_efetivo = (os.getenv("JARVIS_WOL_REAL") == "1") and alvo_worker_autorizado
                    if modo_real_efetivo:
                        acessivel = painel.host_ligado(srv_info)
                    else:
                        atraso = getattr(painel, "simular_atraso_wol_s", 0.0)
                        if isinstance(atraso, (int, float)) and atraso > 0:
                            time.sleep(atraso)
                        acessivel = False
                    duracao_sond = time.monotonic() - t_ini_sond
                    print(f"LATENCIA: sondagem_wol_worker={duracao_sond:.4f}s (real={modo_real_efetivo})", flush=True)

                    if acessivel:
                        print("Computador ja esta acessivel.", flush=True)
                        print("Computador já está acessível.", flush=True)
                        atualizar_status_acao(ev_id, "concluido", acao="ligar", alvo=alvo_nome, resultado={"ja_ligado": True})
                        return

                    if modo_real_efetivo:
                        if not srv_info.get("mac"):
                            raise ValueError("MAC nao configurado")

                        if srv_info.get("wol_python"):
                            painel.enviar_magic_packet(
                                srv_info["mac"],
                                broadcast=srv_info.get(
                                    "broadcast", "255.255.255.255"
                                )
                            )
                        else:
                            subprocess.run(
                                ["wakeonlan", srv_info["mac"]],
                                check=True,
                                timeout=10
                            )

                        print("Wake-on-LAN enviado.", flush=True)
                        print("Pacote Wake-on-LAN enviado.", flush=True)
                        atualizar_status_acao(ev_id, "concluido", acao="ligar", alvo=alvo_nome, resultado={"wol": True})
                    else:
                        print("SIMULAÇÃO: enviaria Wake-on-LAN.", flush=True)
                        print("SIMULACAO: Wake-on-LAN.", flush=True)
                        atualizar_status_acao(ev_id, "concluido", acao="ligar", alvo=alvo_nome, resultado={"simulacao": True})
                except Exception as erro:
                    print(f"Falha no Wake-on-LAN: {erro}", flush=True)
                    atualizar_status_acao(ev_id, "falha", acao="ligar", alvo=alvo_nome, erro=str(erro))

            if modo_real or atraso_wol > 0:
                threading.Thread(
                    target=_executar_wol_worker,
                    args=(srv, ev_wol, alvo),
                    daemon=True
                ).start()
                return {
                    "sucesso": True,
                    "event_id": event_id,
                    "acao": "ligar",
                    "alvo": alvo,
                    "active_session_id": None,
                    "estado": "espera",
                    "status": "aceito",
                    "processado": True,
                }
            else:
                # Simulação pura imediata sem I/O de rede desnecessário
                print("LATENCIA: sondagem_wol=0.0000s (simulacao=True)", flush=True)
                print("SIMULAÇÃO: enviaria Wake-on-LAN.", flush=True)
                print("SIMULACAO: Wake-on-LAN.", flush=True)
                atualizar_status_acao(ev_wol, "concluido", acao="ligar", alvo=alvo, resultado={"simulacao": True})
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

        # Tratamento de acao == "desligar"
        t_ini_sond = time.monotonic()
        alvo_autorizado_ssh = alvo_real_permitido(alvo)
        req_token = token_one_shot_requerido()
        if req_token:
            token_valido_sond, _ = verificar_token_one_shot(alvo, "desligar")
        else:
            token_valido_sond = True
        modo_ssh_real = (os.getenv("JARVIS_SSH_REAL") == "1") and alvo_autorizado_ssh and token_valido_sond
        if modo_ssh_real:
            acessivel = painel.host_ligado(srv)
        else:
            # Em modo de simulação, evita I/O de rede e latências de sondagem (ping/portas)
            acessivel = True
        duracao_sond = time.monotonic() - t_ini_sond
        print(f"LATENCIA: sondagem_desligar={duracao_sond:.4f}s (simulacao={not modo_ssh_real})", flush=True)

        if not acessivel:
            print(
                "DESLIGAMENTO BLOQUEADO:",
                "computador nao acessivel.",
                flush=True
            )
            print("Desligamento bloqueado: host não acessível.", flush=True)
            estado = "espera"
            fonte_ativa = None
            sessao_ativa_id = None
            return {
                "sucesso": False,
                "erro": "computador_inacessivel",
                "event_id": event_id,
                "active_session_id": None,
                "estado": "espera",
                "status": "falha",
                "processado": True,
            }

        estado = "confirmacao"
        pendente = alvo
        pendente_acao = "desligar"
        limite = time.monotonic() + 20
        print(
            f'Confirme desligamento de {srv["nome"]}: diga "confirmar desligamento" ou "cancelar".',
            flush=True
        )
        if event_id:
            atualizar_status_acao(
                event_id,
                "confirmacao_pendente",
                acao="desligar",
                alvo=alvo,
                resultado={"estado": "confirmacao"}
            )
        return {
            "sucesso": True,
            "status": "confirmacao_pendente",
            "event_id": event_id,
            "acao": "desligar_pendente",
            "alvo": alvo,
            "active_session_id": sessao_ativa_id,
            "estado": "confirmacao",
            "processado": True,
        }

    if estado == "confirmacao":
        if texto == "assistente":
            msg_aguardo = "desligamento"
            if pendente_acao == "fechar_navegador":
                msg_aguardo = "fechar navegador"
            elif pendente_acao == "reiniciar":
                msg_aguardo = "reinicialização"
            print(f"Aguardando confirmacao de {msg_aguardo}.", flush=True)
            return {
                "sucesso": True,
                "event_id": event_id,
                "active_session_id": sessao_ativa_id,
                "estado": "confirmacao",
                "deduplicado": True,
                "status": "confirmacao_pendente",
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
                "status": "falha",
                "processado": False,
            }

        # Confirmações específicas do PC Control (fechar navegador e reiniciar)
        if pendente_acao in ("fechar_navegador", "reiniciar"):
            if not is_valid_confirmation(texto, pendente_acao):
                print("Confirmacao insuficiente. Pedido cancelado.", flush=True)
                alvo_falha = pendente
                acao_falha = pendente_acao
                estado = "espera"
                pendente = None
                pendente_acao = None
                fonte_ativa = None
                sessao_ativa_id = None
                if event_id:
                    atualizar_status_acao(event_id, "falha", acao=acao_falha, alvo=alvo_falha, erro="confirmacao_insuficiente")
                return {
                    "sucesso": False,
                    "erro": "confirmacao_insuficiente",
                    "event_id": event_id,
                    "active_session_id": None,
                    "estado": "espera",
                    "status": "falha",
                    "processado": True,
                }

            alvo_confirmado = pendente
            acao_confirmada = pendente_acao
            estado = "espera"
            pendente = None
            pendente_acao = None
            fonte_ativa = None
            sessao_ativa_id = None

            registrar_acao(acao_confirmada, alvo_confirmado)
            if event_id:
                registrar_inicio_acao(event_id, acao_confirmada, alvo_confirmado, status="em_processamento")

            res_disp = pc_dispatcher.client.enviar_comando(
                alvo=alvo_confirmado,
                acao=acao_confirmada,
                event_id=event_id or f"ev-pc-{int(time.time()*1000)}",
                session_id=session_id,
                fonte=fonte,
            ) if pc_dispatcher else {"sucesso": False, "status": "falha", "erro": "dispatcher_indisponivel"}

            print(f"PC Control: {res_disp.get('mensagem_amigavel', '')}", flush=True)
            if event_id:
                atualizar_status_acao(
                    event_id,
                    res_disp.get("status", "concluido"),
                    acao=acao_confirmada,
                    alvo=alvo_confirmado,
                    resultado=res_disp.get("resultado"),
                    erro=res_disp.get("erro"),
                )
            return {
                "sucesso": res_disp.get("status") == "concluido",
                "event_id": event_id,
                "acao": acao_confirmada,
                "alvo": alvo_confirmado,
                "active_session_id": None,
                "estado": "espera",
                "status": res_disp.get("status", "concluido"),
                "resultado": res_disp.get("resultado"),
                "erro": res_disp.get("erro"),
                "processado": True,
            }

        # Confirmação de desligamento (ação de energia)
        if pendente_acao == "desligar" or pendente_acao is None:
            if not is_valid_confirmation(texto, "desligar"):
                print("Confirmacao insuficiente. Pedido cancelado.", flush=True)
                alvo_falha = pendente
                estado = "espera"
                pendente = None
                pendente_acao = None
                fonte_ativa = None
                sessao_ativa_id = None
                if event_id:
                    atualizar_status_acao(event_id, "falha", acao="desligar", alvo=alvo_falha, erro="confirmacao_insuficiente")
                return {
                    "sucesso": False,
                    "erro": "confirmacao_insuficiente",
                    "event_id": event_id,
                    "active_session_id": None,
                    "estado": "espera",
                    "status": "falha",
                    "processado": True,
                }

        if texto in ("confirmar", "confirmar desligar", "confirmar desligamento"):
            srv = painel.SERVIDORES[pendente]
            status_acao = "falha"
            t_ini_sond = time.monotonic()
            alvo_autorizado_ssh = alvo_real_permitido(pendente)
            req_token = token_one_shot_requerido()
            if req_token:
                token_valido_conf, _ = verificar_token_one_shot(pendente, "desligar")
            else:
                token_valido_conf = True
            modo_ssh_real = (os.getenv("JARVIS_SSH_REAL") == "1") and alvo_autorizado_ssh and token_valido_conf
            if modo_ssh_real:
                host_ativo = painel.host_ligado(srv)
            else:
                # Em modo de simulação, evita I/O de rede desnecessário
                host_ativo = True
            duracao_sond = time.monotonic() - t_ini_sond
            print(f"LATENCIA: sondagem_confirmar_desligar={duracao_sond:.4f}s (simulacao={not modo_ssh_real})", flush=True)

            if host_ativo:
                if modo_ssh_real:
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
                salvo_ok, erro_salvar = registrar_inicio_acao(ev_key, "desligar", alvo_confirmado, status="em_processamento")
                if not salvo_ok:
                    print(f"FALHA NA PERSISTENCIA: {erro_salvar}", flush=True)
                    if os.getenv("JARVIS_SSH_REAL") == "1" or os.getenv("JARVIS_REQUIRE_PERSISTENCE") == "1":
                        print("FALHA CRITICA: SSH real BLOQUEADO por seguranca (falha ao persistir estado).", flush=True)
                        estado = "espera"
                        pendente = None
                        fonte_ativa = None
                        sessao_ativa_id = None
                        return {
                            "sucesso": False,
                            "erro": "falha_persistencia_estado",
                            "event_id": event_id,
                            "acao": "desligar",
                            "alvo": alvo_confirmado,
                            "active_session_id": None,
                            "estado": "espera",
                            "status": "falha",
                            "processado": True,
                        }

                configuracao = dict(srv)
                if alvo_confirmado == "ananuke":
                    configuracao["cmd"] = "sudo -n /usr/sbin/poweroff"

                # Disparo da ação SSH em thread dedicada para não bloquear o IPC nem o loop principal
                def _executar_ssh_worker(srv_info, conf, ev_id, alvo_nome):
                    token_consumido = False
                    dados_token = None
                    try:
                        cmd = painel.montar_comando_ssh(conf) if hasattr(painel, "montar_comando_ssh") else None
                        alvo_worker_autorizado = alvo_real_permitido(alvo_nome)
                        modo_ssh_env = (os.getenv("JARVIS_SSH_REAL") == "1")
                        if modo_ssh_env and alvo_worker_autorizado:
                            req_token = token_one_shot_requerido()
                            if req_token:
                                token_ok, res_token = consumir_token_one_shot(alvo_nome, "desligar", ev_id)
                                if token_ok:
                                    token_consumido = True
                                    dados_token = res_token
                                    print(f"[ONE-SHOT] Token {dados_token.get('token_id')} consumido com sucesso para {alvo_nome} (event_id={ev_id}).", flush=True)
                                else:
                                    print(f"[ONE-SHOT-BLOQUEIO] SSH real bloqueado para {alvo_nome}: {res_token}. Executando em simulacao.", flush=True)
                            else:
                                token_consumido = True

                        modo_real_efetivo = modo_ssh_env and alvo_worker_autorizado and token_consumido
                        if modo_real_efetivo:
                            resultado = subprocess.run(
                                cmd,
                                capture_output=True,
                                text=True,
                                timeout=25
                            )
                            if resultado.returncode == 0:
                                print("SSH enviado com sucesso. Codigo: 0", flush=True)
                                print("SSH: código de retorno 0", flush=True)
                                print("Verifique se o computador desligou.", flush=True)
                                atualizar_status_acao(
                                    ev_id,
                                    "concluido",
                                    acao="desligar",
                                    alvo=alvo_nome,
                                    resultado={"codigo": 0, "token_id": dados_token.get("token_id") if dados_token else None}
                                )
                                finalizar_token_one_shot(alvo=alvo_nome, event_id=ev_id, resultado={"codigo": 0})
                            else:
                                print(f"Falha no SSH: codigo de retorno {resultado.returncode}.", flush=True)
                                if resultado.stderr:
                                    print("SSH:", resultado.stderr.strip(), flush=True)
                                atualizar_status_acao(
                                    ev_id,
                                    "falha",
                                    acao="desligar",
                                    alvo=alvo_nome,
                                    erro=f"ssh_returncode_{resultado.returncode}",
                                    resultado={"codigo": resultado.returncode, "token_id": dados_token.get("token_id") if dados_token else None}
                                )
                                finalizar_token_one_shot(
                                    alvo=alvo_nome,
                                    event_id=ev_id,
                                    resultado={"codigo": resultado.returncode},
                                    erro=f"ssh_returncode_{resultado.returncode}"
                                )
                        else:
                            codigo_simulado = getattr(painel, "simular_codigo_ssh", 0)
                            atraso = getattr(painel, "simular_atraso_ssh_s", 0.0)
                            if isinstance(atraso, (int, float)) and atraso > 0:
                                time.sleep(atraso)
                            if codigo_simulado == 0:
                                print("SIMULAÇÃO: enviaria desligamento SSH.", flush=True)
                                print("SIMULACAO SSH para", srv_info["nome"], flush=True)
                                atualizar_status_acao(
                                    ev_id,
                                    "concluido",
                                    acao="desligar",
                                    alvo=alvo_nome,
                                    resultado={"codigo": 0}
                                )
                            else:
                                print(f"SIMULACAO SSH com falha (codigo {codigo_simulado}) para", srv_info["nome"], flush=True)
                                atualizar_status_acao(
                                    ev_id,
                                    "falha",
                                    acao="desligar",
                                    alvo=alvo_nome,
                                    erro=f"ssh_returncode_{codigo_simulado}",
                                    resultado={"codigo": codigo_simulado}
                                )
                    except subprocess.TimeoutExpired as erro_timeout:
                        print("Erro no SSH: timeout de 25s excedido.", flush=True)
                        atualizar_status_acao(
                            ev_id,
                            "falha",
                            acao="desligar",
                            alvo=alvo_nome,
                            erro="ssh_timeout_25s"
                        )
                        if token_consumido:
                            finalizar_token_one_shot(alvo=alvo_nome, event_id=ev_id, erro="ssh_timeout_25s")
                    except Exception as erro:
                        print("Erro no SSH:", erro, flush=True)
                        atualizar_status_acao(
                            ev_id,
                            "falha",
                            acao="desligar",
                            alvo=alvo_nome,
                            erro=str(erro)
                        )
                        if token_consumido:
                            finalizar_token_one_shot(alvo=alvo_nome, event_id=ev_id, erro=str(erro))

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
                print("Desligamento bloqueado: host não acessível.", flush=True)
                status_acao = "falha"

            estado = "espera"
            alvo_confirmado = pendente
            pendente = None
            pendente_acao = None
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
    global estado, sessao_ativa_id, pendente, pendente_acao, limite, ultima_ativacao, fonte_ativa
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
    if tipo == "confirmar" and not is_valid_confirmation(texto):
        return {"sucesso": False, "erro": "confirmacao_invalida", "processado": False}
    if tipo == "comando" and texto not in COMANDOS and texto != "dois ligar fusion":
        return {"sucesso": False, "erro": "comando_desconhecido", "processado": False}

    sid = evento.get("session_id")
    event_id = evento.get("event_id") or evento.get("nonce")

    # Verificação de retransmissão de evento no histórico persistente
    if event_id and tipo in ("comando", "confirmar"):
        reg_existente = obter_status_acao(event_id=event_id)
        if reg_existente and reg_existente.get("status") in ("concluido", "em_processamento", "indeterminado", "falha"):
            st = reg_existente.get("status")
            print(f"IPC: Retransmissao de evento recuperada do historico persistente ({event_id}, status: {st}).", flush=True)
            return {
                "sucesso": True if st == "concluido" else False,
                "event_id": event_id,
                "acao": reg_existente.get("acao"),
                "alvo": reg_existente.get("alvo"),
                "status": st,
                "erro": reg_existente.get("erro"),
                "active_session_id": sessao_ativa_id,
                "estado": estado,
                "processado": True,
                "retransmissao": True,
            }

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
            # Deduplicação de comando reconhecido simultaneamente por ambos os microfones
            if texto in COMANDOS:
                acao_cand, alvo_cand = COMANDOS[texto]
                acao_pend_esp = pendente_acao or "desligar"
                if estado == "confirmacao" and acao_cand == acao_pend_esp and alvo_cand == pendente:
                    print(f"IPC: Comando '{texto}' deduplicado (confirmacao ja pendente para '{pendente}').", flush=True)
                    return {
                        "sucesso": True,
                        "event_id": event_id,
                        "acao": f"{acao_cand}_pendente",
                        "alvo": pendente,
                        "deduplicado": True,
                        "active_session_id": sessao_ativa_id,
                        "estado": "confirmacao",
                        "status": "confirmacao_pendente",
                        "processado": True,
                    }
                if ja_executado_recentemente(acao_cand, alvo_cand):
                    print(f"IPC: Comando '{texto}' deduplicado (acao '{acao_cand} {alvo_cand}' ja executada recentemente).", flush=True)
                    return {
                        "sucesso": True,
                        "event_id": event_id,
                        "acao": acao_cand,
                        "alvo": alvo_cand,
                        "deduplicado": True,
                        "active_session_id": None,
                        "estado": "espera",
                        "status": "concluido",
                        "processado": True,
                    }
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
            # Deduplicação de confirmação reconhecida simultaneamente por ambos os microfones
            acoes_a_checar = (pendente_acao,) if pendente_acao else ("desligar", "fechar_navegador", "reiniciar")
            for alvo_chk in ("fusion", "ananuke"):
                for acao_conf_chk in acoes_a_checar:
                    if ja_executado_recentemente(acao_conf_chk, alvo_chk):
                        print(f"IPC: Confirmacao de {acao_conf_chk} para '{alvo_chk}' deduplicada (ja executada recentemente).", flush=True)
                        return {
                            "sucesso": True,
                            "event_id": event_id,
                            "acao": acao_conf_chk,
                            "alvo": alvo_chk,
                            "deduplicado": True,
                            "active_session_id": None,
                            "estado": "espera",
                            "status": "concluido",
                            "processado": True,
                        }
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
            if tipo in ("comando", "confirmar"):
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
                    if resultado.get("estado") == "confirmacao" and (
                        "pendente" in str(resultado.get("acao"))
                        or resultado.get("acao") in ("desligar_pendente", "fechar_navegador_pendente", "reiniciar_pendente")
                    ):
                        resultado["status"] = "confirmacao_pendente"
                    elif resultado.get("acao") == "cancelar":
                        resultado["status"] = "cancelado"
                    elif resultado.get("sucesso"):
                        resultado["status"] = "concluido"
                    else:
                        resultado["status"] = "falha"
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
            if tipo in ("comando", "confirmar"):
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
        st_fallback = "aceito"
        if tipo == "comando" and any(k in texto for k in ["desligar", "fechar", "reiniciar"]):
            st_fallback = "confirmacao_pendente"
        elif tipo == "cancelar":
            st_fallback = "cancelado"
        return {
            "sucesso": True,
            "event_id": event_id,
            "active_session_id": sid if tipo == "wake" else sessao_ativa_id,
            "estado": estado,
            "status": st_fallback,
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
    if wol_real or ssh_real:
        alvos_permitidos = [a.strip().lower() for a in os.getenv("JARVIS_ALLOWED_REAL_TARGETS", "").split(",") if a.strip()]
        print(f"ALVOS REAIS AUTORIZADOS: {','.join(alvos_permitidos) if alvos_permitidos else 'NENHUM (fail-closed)'}", flush=True)
    if ssh_real:
        if token_one_shot_requerido():
            tok_ok, tok_info = verificar_token_one_shot("fusion", "desligar")
            if tok_ok:
                print(f"TOKEN ONE-SHOT SSH: ATIVO (id={tok_info.get('token_id')}, expira em {int(tok_info.get('expires_at', 0) - time.time())}s)", flush=True)
            else:
                print(f"TOKEN ONE-SHOT SSH: INATIVO/AUSENTE ({tok_info}) - SSH REAL BLOQUEADO", flush=True)
        else:
            print("SSH REAL DEFINITIVO (Token One-Shot desativado via JARVIS_REQUIRE_ONE_SHOT_TOKEN=0)", flush=True)
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
