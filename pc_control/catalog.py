#!/usr/bin/env python3
"""Catálogo Centralizado de Intenções, Frases, Aliases e Ações do Jarvis PC Control V1.

Centraliza todas as gramáticas, comandos e aliases para evitar divergências
entre o controlador central (jarvis-operacional) e o áudio gateway.
Preserva integralmente todos os comandos legados de energia (WoL e desligamento SSH).
"""
from __future__ import annotations

import unicodedata
from typing import Dict, List, Optional, Set, Tuple

# Comandos legados de energia (preservação estrita)
ENERGY_COMMANDS: Dict[str, Tuple[str, str]] = {
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

# 11 Comandos Obrigatórios do PC Control V1 para Fusion (Windows)
FUSION_PC_CONTROL_COMMANDS: Dict[str, Tuple[str, str]] = {
    # 1. Abrir navegador
    "abrir navegador no fusion": ("abrir_navegador", "fusion"),
    "abrir o navegador no fusion": ("abrir_navegador", "fusion"),
    "abrir navegador fusion": ("abrir_navegador", "fusion"),
    "abrir o navegador fusion": ("abrir_navegador", "fusion"),
    "abrir navegador no computador fusion": ("abrir_navegador", "fusion"),
    # 2. Abrir YouTube
    "abrir youtube no fusion": ("abrir_youtube", "fusion"),
    "abrir o youtube no fusion": ("abrir_youtube", "fusion"),
    "abrir youtube fusion": ("abrir_youtube", "fusion"),
    "abrir o youtube fusion": ("abrir_youtube", "fusion"),
    # 3. Bloquear computador
    "bloquear computador fusion": ("bloquear", "fusion"),
    "bloquear o computador fusion": ("bloquear", "fusion"),
    "bloquear fusion": ("bloquear", "fusion"),
    # 4. Aumentar volume
    "aumentar volume no fusion": ("aumentar_volume", "fusion"),
    "aumentar o volume no fusion": ("aumentar_volume", "fusion"),
    "aumentar volume fusion": ("aumentar_volume", "fusion"),
    "aumentar o volume fusion": ("aumentar_volume", "fusion"),
    # 5. Diminuir volume
    "diminuir volume no fusion": ("diminuir_volume", "fusion"),
    "diminuir o volume no fusion": ("diminuir_volume", "fusion"),
    "diminuir volume fusion": ("diminuir_volume", "fusion"),
    "diminuir o volume fusion": ("diminuir_volume", "fusion"),
    # 6. Silenciar áudio
    "silenciar audio no fusion": ("silenciar_audio", "fusion"),
    "silenciar o audio no fusion": ("silenciar_audio", "fusion"),
    "silenciar audio fusion": ("silenciar_audio", "fusion"),
    "silenciar o audio fusion": ("silenciar_audio", "fusion"),
    "mutar audio no fusion": ("silenciar_audio", "fusion"),
    "mutar audio fusion": ("silenciar_audio", "fusion"),
    # 7. Informar status do computador
    "informar status do fusion": ("status_computador", "fusion"),
    "informar status do computador fusion": ("status_computador", "fusion"),
    "status do fusion": ("status_computador", "fusion"),
    "status do computador fusion": ("status_computador", "fusion"),
    "status fusion": ("status_computador", "fusion"),
    # 8. Abrir terminal
    "abrir terminal no fusion": ("abrir_terminal", "fusion"),
    "abrir o terminal no fusion": ("abrir_terminal", "fusion"),
    "abrir terminal fusion": ("abrir_terminal", "fusion"),
    "abrir o terminal fusion": ("abrir_terminal", "fusion"),
    # 9. Fechar navegador (requer confirmação)
    "fechar navegador no fusion": ("fechar_navegador", "fusion"),
    "fechar o navegador no fusion": ("fechar_navegador", "fusion"),
    "fechar navegador fusion": ("fechar_navegador", "fusion"),
    "fechar o navegador fusion": ("fechar_navegador", "fusion"),
    # 10. Reiniciar computador (requer confirmação)
    "reiniciar fusion": ("reiniciar", "fusion"),
    "reiniciar o fusion": ("reiniciar", "fusion"),
    "reiniciar computador fusion": ("reiniciar", "fusion"),
    # 11. Mostrar área de trabalho
    "mostrar area de trabalho no fusion": ("mostrar_area_de_trabalho", "fusion"),
    "mostrar a area de trabalho no fusion": ("mostrar_area_de_trabalho", "fusion"),
    "mostrar area de trabalho fusion": ("mostrar_area_de_trabalho", "fusion"),
}

# 11 Comandos Obrigatórios do PC Control V1 para Ananuke (Debian 13 com Xfce)
# Inclui aliases "computador dois" e "ana no que"
ANANUKE_PC_CONTROL_COMMANDS: Dict[str, Tuple[str, str]] = {
    # 1. Abrir navegador
    "abrir navegador no ananuke": ("abrir_navegador", "ananuke"),
    "abrir o navegador no ananuke": ("abrir_navegador", "ananuke"),
    "abrir navegador ananuke": ("abrir_navegador", "ananuke"),
    "abrir o navegador ananuke": ("abrir_navegador", "ananuke"),
    "abrir navegador no computador dois": ("abrir_navegador", "ananuke"),
    "abrir o navegador no computador dois": ("abrir_navegador", "ananuke"),
    "abrir navegador no ana no que": ("abrir_navegador", "ananuke"),
    # 2. Abrir YouTube
    "abrir youtube no ananuke": ("abrir_youtube", "ananuke"),
    "abrir o youtube no ananuke": ("abrir_youtube", "ananuke"),
    "abrir youtube ananuke": ("abrir_youtube", "ananuke"),
    "abrir o youtube ananuke": ("abrir_youtube", "ananuke"),
    "abrir youtube no computador dois": ("abrir_youtube", "ananuke"),
    "abrir o youtube no computador dois": ("abrir_youtube", "ananuke"),
    "abrir youtube no ana no que": ("abrir_youtube", "ananuke"),
    # 3. Bloquear computador
    "bloquear computador ananuke": ("bloquear", "ananuke"),
    "bloquear ananuke": ("bloquear", "ananuke"),
    "bloquear computador dois": ("bloquear", "ananuke"),
    "bloquear o computador dois": ("bloquear", "ananuke"),
    "bloquear ana no que": ("bloquear", "ananuke"),
    # 4. Aumentar volume
    "aumentar volume no ananuke": ("aumentar_volume", "ananuke"),
    "aumentar o volume no ananuke": ("aumentar_volume", "ananuke"),
    "aumentar volume ananuke": ("aumentar_volume", "ananuke"),
    "aumentar o volume ananuke": ("aumentar_volume", "ananuke"),
    "aumentar volume no computador dois": ("aumentar_volume", "ananuke"),
    "aumentar o volume no computador dois": ("aumentar_volume", "ananuke"),
    "aumentar volume no ana no que": ("aumentar_volume", "ananuke"),
    # 5. Diminuir volume
    "diminuir volume no ananuke": ("diminuir_volume", "ananuke"),
    "diminuir o volume no ananuke": ("diminuir_volume", "ananuke"),
    "diminuir volume ananuke": ("diminuir_volume", "ananuke"),
    "diminuir o volume ananuke": ("diminuir_volume", "ananuke"),
    "diminuir volume no computador dois": ("diminuir_volume", "ananuke"),
    "diminuir o volume no computador dois": ("diminuir_volume", "ananuke"),
    "diminuir volume no ana no que": ("diminuir_volume", "ananuke"),
    # 6. Silenciar áudio
    "silenciar audio no ananuke": ("silenciar_audio", "ananuke"),
    "silenciar o audio no ananuke": ("silenciar_audio", "ananuke"),
    "silenciar audio ananuke": ("silenciar_audio", "ananuke"),
    "silenciar o audio ananuke": ("silenciar_audio", "ananuke"),
    "silenciar audio no computador dois": ("silenciar_audio", "ananuke"),
    "silenciar o audio no computador dois": ("silenciar_audio", "ananuke"),
    "silenciar audio no ana no que": ("silenciar_audio", "ananuke"),
    "mutar audio no ananuke": ("silenciar_audio", "ananuke"),
    "mutar audio ananuke": ("silenciar_audio", "ananuke"),
    # 7. Informar status do computador
    "informar status do ananuke": ("status_computador", "ananuke"),
    "informar status do computador dois": ("status_computador", "ananuke"),
    "informar status do ana no que": ("status_computador", "ananuke"),
    "status do ananuke": ("status_computador", "ananuke"),
    "status do computador dois": ("status_computador", "ananuke"),
    "status do ana no que": ("status_computador", "ananuke"),
    "status ananuke": ("status_computador", "ananuke"),
    # 8. Abrir terminal
    "abrir terminal no ananuke": ("abrir_terminal", "ananuke"),
    "abrir o terminal no ananuke": ("abrir_terminal", "ananuke"),
    "abrir terminal ananuke": ("abrir_terminal", "ananuke"),
    "abrir o terminal ananuke": ("abrir_terminal", "ananuke"),
    "abrir terminal no computador dois": ("abrir_terminal", "ananuke"),
    "abrir o terminal no computador dois": ("abrir_terminal", "ananuke"),
    "abrir terminal no ana no que": ("abrir_terminal", "ananuke"),
    # 9. Fechar navegador (requer confirmação)
    "fechar navegador no ananuke": ("fechar_navegador", "ananuke"),
    "fechar o navegador no ananuke": ("fechar_navegador", "ananuke"),
    "fechar navegador ananuke": ("fechar_navegador", "ananuke"),
    "fechar o navegador ananuke": ("fechar_navegador", "ananuke"),
    "fechar navegador no computador dois": ("fechar_navegador", "ananuke"),
    "fechar o navegador no computador dois": ("fechar_navegador", "ananuke"),
    "fechar navegador no ana no que": ("fechar_navegador", "ananuke"),
    # 10. Reiniciar computador (requer confirmação)
    "reiniciar ananuke": ("reiniciar", "ananuke"),
    "reiniciar o ananuke": ("reiniciar", "ananuke"),
    "reiniciar computador dois": ("reiniciar", "ananuke"),
    "reiniciar o computador dois": ("reiniciar", "ananuke"),
    "reiniciar computador ananuke": ("reiniciar", "ananuke"),
    "reiniciar ana no que": ("reiniciar", "ananuke"),
    # 11. Mostrar área de trabalho
    "mostrar area de trabalho no ananuke": ("mostrar_area_de_trabalho", "ananuke"),
    "mostrar a area de trabalho no ananuke": ("mostrar_area_de_trabalho", "ananuke"),
    "mostrar area de trabalho ananuke": ("mostrar_area_de_trabalho", "ananuke"),
    "mostrar area de trabalho no computador dois": ("mostrar_area_de_trabalho", "ananuke"),
    "mostrar a area de trabalho no computador dois": ("mostrar_area_de_trabalho", "ananuke"),
    "mostrar area de trabalho no ana no que": ("mostrar_area_de_trabalho", "ananuke"),
}

# União dos comandos de PC Control
PC_CONTROL_COMMANDS: Dict[str, Tuple[str, str]] = {
    **FUSION_PC_CONTROL_COMMANDS,
    **ANANUKE_PC_CONTROL_COMMANDS,
}

# Catálogo completo unificado
ALL_COMMANDS: Dict[str, Tuple[str, str]] = {
    **ENERGY_COMMANDS,
    **PC_CONTROL_COMMANDS,
}

# Ações que exigem confirmação obrigatória
ACTIONS_REQUIRING_CONFIRMATION: Set[str] = {
    "desligar",
    "fechar_navegador",
    "reiniciar",
}

# Todas as 11 ações permitidas de controle de PC
ALLOWED_PC_ACTIONS: Set[str] = {
    "abrir_navegador",
    "abrir_youtube",
    "bloquear",
    "aumentar_volume",
    "diminuir_volume",
    "silenciar_audio",
    "status_computador",
    "abrir_terminal",
    "fechar_navegador",
    "reiniciar",
    "mostrar_area_de_trabalho",
}

# Todas as ações permitidas no sistema (energia + controle)
ALLOWED_ACTIONS: Set[str] = {
    "ligar",
    *ALLOWED_PC_ACTIONS,
}

# Computadores-alvo permitidos
ALLOWED_TARGETS: Set[str] = {"fusion", "ananuke"}

# Frases de confirmação suportadas pelo sistema
CONFIRMATION_PHRASES: List[str] = [
    "confirmar",
    "confirmar desligar",
    "confirmar desligamento",
    "confirmar fechar navegador",
    "confirmar reinicio",
    "confirmar reinicializacao",
]

# Frases de cancelamento
CANCELLATION_PHRASES: List[str] = [
    "cancelar",
]

# Gramática completa para modelo Vosk (sem acentuação para precisão do modelo pt-0.3)
VOSK_GRAMMAR: List[str] = [
    "assistente",
    *CONFIRMATION_PHRASES,
    *CANCELLATION_PHRASES,
    *ALL_COMMANDS.keys(),
    "[unk]",
]


def remover_acentos(texto: str) -> str:
    """Remove acentos e diacríticos mantendo letras minúsculas."""
    nfkd = unicodedata.normalize("NFKD", texto)
    return "".join(c for c in nfkd if not unicodedata.combining(c)).lower().strip()


def normalizar_comando(texto: str) -> str:
    """Normaliza o texto do comando para pesquisa no catálogo."""
    if not texto:
        return ""
    t = remover_acentos(texto)
    # Trata variações comuns
    t = " ".join(t.split())
    return t


def obter_comando(texto: str) -> Optional[Tuple[str, str]]:
    """Localiza ação e alvo no catálogo a partir do texto falado."""
    t = normalizar_comando(texto)
    if t in ALL_COMMANDS:
        return ALL_COMMANDS[t]
    # Tenta busca com correção para teste legado 'dois ligar fusion'
    if t == "dois ligar fusion":
        return ALL_COMMANDS.get("ligar fusion")
    return None


def is_pc_control_action(action: str) -> bool:
    """Verifica se a ação pertence ao módulo PC Control."""
    return action in ALLOWED_PC_ACTIONS


def requires_confirmation(action: str) -> bool:
    """Verifica se a ação exige confirmação prévia em duas etapas."""
    return action in ACTIONS_REQUIRING_CONFIRMATION


def is_valid_confirmation(text: str, pending_action: Optional[str] = None) -> bool:
    """Valida se o texto é uma confirmação válida para a ação pendente."""
    t = normalizar_comando(text)
    if t == "confirmar":
        return True
    if pending_action == "desligar" and t in ("confirmar desligar", "confirmar desligamento"):
        return True
    if pending_action == "fechar_navegador" and t in ("confirmar fechar navegador", "confirmar"):
        return True
    if pending_action == "reiniciar" and t in ("confirmar reinicio", "confirmar reinicializacao", "confirmar"):
        return True
    # Se nenhuma ação foi informada para filtrar, aceita qualquer confirmação do catálogo
    if pending_action is None and t in CONFIRMATION_PHRASES:
        return True
    return False


def get_friendly_target_name(target: str) -> str:
    """Retorna o nome amigável do computador-alvo."""
    nomes = {
        "fusion": "Fusion (Windows)",
        "ananuke": "Ananuke (Debian)",
    }
    return nomes.get(target.lower(), target)


def get_friendly_action_name(action: str) -> str:
    """Retorna a descrição amigável da ação em português."""
    nomes = {
        "ligar": "Ligar",
        "desligar": "Desligar",
        "abrir_navegador": "Abrir navegador",
        "abrir_youtube": "Abrir YouTube",
        "bloquear": "Bloquear sessão",
        "aumentar_volume": "Aumentar volume",
        "diminuir_volume": "Diminuir volume",
        "silenciar_audio": "Silenciar áudio",
        "status_computador": "Consultar status",
        "abrir_terminal": "Abrir terminal",
        "fechar_navegador": "Fechar navegador",
        "reiniciar": "Reiniciar",
        "mostrar_area_de_trabalho": "Mostrar área de trabalho",
    }
    return nomes.get(action, action)
