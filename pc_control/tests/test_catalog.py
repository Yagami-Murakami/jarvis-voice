#!/usr/bin/env python3
"""Testes Unitários do Catálogo Centralizado (pc_control/catalog.py).

Valida:
- As 22 combinações básicas obrigatórias (11 comandos × 2 computadores).
- Resolução correta de aliases ('computador dois', 'ana no que', etc.).
- Prevenção rigorosa de ambiguidade de alvo.
- Preservação intacta de todos os comandos de energia.
- Integridade da gramática Vosk.
"""
from __future__ import annotations

import unittest

from pc_control.catalog import (
    ALL_COMMANDS,
    ALLOWED_PC_ACTIONS,
    ALLOWED_TARGETS,
    ANANUKE_PC_CONTROL_COMMANDS,
    CONFIRMATION_PHRASES,
    ENERGY_COMMANDS,
    FUSION_PC_CONTROL_COMMANDS,
    PC_CONTROL_COMMANDS,
    VOSK_GRAMMAR,
    is_pc_control_action,
    is_valid_confirmation,
    normalizar_comando,
    obter_comando,
    remover_acentos,
    requires_confirmation,
)

COMANDOS_OBRIGATORIOS_11 = [
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
]


class TestPcControlCatalog(unittest.TestCase):
    """Bateria de testes para o catálogo centralizado de intenções."""

    def test_01_all_11_commands_exist_for_both_computers(self):
        """Valida que todos os 11 comandos obrigatórios existem para Fusion e Ananuke (22 combinações)."""
        fusion_actions = {acao for (acao, alvo) in FUSION_PC_CONTROL_COMMANDS.values()}
        ananuke_actions = {acao for (acao, alvo) in ANANUKE_PC_CONTROL_COMMANDS.values()}

        for cmd in COMANDOS_OBRIGATORIOS_11:
            self.assertIn(cmd, fusion_actions, f"Comando '{cmd}' ausente no catálogo do Fusion.")
            self.assertIn(cmd, ananuke_actions, f"Comando '{cmd}' ausente no catálogo do Ananuke.")

        self.assertEqual(len(fusion_actions), 11)
        self.assertEqual(len(ananuke_actions), 11)

    def test_02_total_22_combinations_resolvable(self):
        """Valida a resolução direta das 22 combinações básicas."""
        combinacoes = [
            ("abrir navegador no fusion", "abrir_navegador", "fusion"),
            ("abrir youtube no fusion", "abrir_youtube", "fusion"),
            ("bloquear computador fusion", "bloquear", "fusion"),
            ("aumentar volume no fusion", "aumentar_volume", "fusion"),
            ("diminuir volume no fusion", "diminuir_volume", "fusion"),
            ("silenciar audio no fusion", "silenciar_audio", "fusion"),
            ("informar status do fusion", "status_computador", "fusion"),
            ("abrir terminal no fusion", "abrir_terminal", "fusion"),
            ("fechar navegador no fusion", "fechar_navegador", "fusion"),
            ("reiniciar fusion", "reiniciar", "fusion"),
            ("mostrar area de trabalho no fusion", "mostrar_area_de_trabalho", "fusion"),
            ("abrir navegador no ananuke", "abrir_navegador", "ananuke"),
            ("abrir youtube no ananuke", "abrir_youtube", "ananuke"),
            ("bloquear computador ananuke", "bloquear", "ananuke"),
            ("aumentar volume no ananuke", "aumentar_volume", "ananuke"),
            ("diminuir volume no ananuke", "diminuir_volume", "ananuke"),
            ("silenciar audio no ananuke", "silenciar_audio", "ananuke"),
            ("informar status do ananuke", "status_computador", "ananuke"),
            ("abrir terminal no ananuke", "abrir_terminal", "ananuke"),
            ("fechar navegador no ananuke", "fechar_navegador", "ananuke"),
            ("reiniciar ananuke", "reiniciar", "ananuke"),
            ("mostrar area de trabalho no ananuke", "mostrar_area_de_trabalho", "ananuke"),
        ]

        self.assertEqual(len(combinacoes), 22)
        for frase, acao_esp, alvo_esp in combinacoes:
            res = obter_comando(frase)
            self.assertIsNotNone(res, f"Frase obrigatória não reconhecida: '{frase}'")
            acao, alvo = res
            self.assertEqual(acao, acao_esp, f"Ação divergente para '{frase}': {acao} != {acao_esp}")
            self.assertEqual(alvo, alvo_esp, f"Alvo divergente para '{frase}': {alvo} != {alvo_esp}")

    def test_03_aliases_ananuke_computador_dois_and_ana_no_que(self):
        """Valida que todos os aliases de Ananuke ('computador dois', 'ana no que') funcionam."""
        frases_alias = [
            ("abrir navegador no computador dois", "abrir_navegador", "ananuke"),
            ("abrir navegador no ana no que", "abrir_navegador", "ananuke"),
            ("abrir youtube no computador dois", "abrir_youtube", "ananuke"),
            ("abrir youtube no ana no que", "abrir_youtube", "ananuke"),
            ("bloquear computador dois", "bloquear", "ananuke"),
            ("bloquear ana no que", "bloquear", "ananuke"),
            ("aumentar volume no computador dois", "aumentar_volume", "ananuke"),
            ("aumentar volume no ana no que", "aumentar_volume", "ananuke"),
            ("diminuir volume no computador dois", "diminuir_volume", "ananuke"),
            ("diminuir volume no ana no que", "diminuir_volume", "ananuke"),
            ("silenciar audio no computador dois", "silenciar_audio", "ananuke"),
            ("silenciar audio no ana no que", "silenciar_audio", "ananuke"),
            ("informar status do computador dois", "status_computador", "ananuke"),
            ("informar status do ana no que", "status_computador", "ananuke"),
            ("abrir terminal no computador dois", "abrir_terminal", "ananuke"),
            ("abrir terminal no ana no que", "abrir_terminal", "ananuke"),
            ("fechar navegador no computador dois", "fechar_navegador", "ananuke"),
            ("fechar navegador no ana no que", "fechar_navegador", "ananuke"),
            ("reiniciar computador dois", "reiniciar", "ananuke"),
            ("reiniciar ana no que", "reiniciar", "ananuke"),
            ("mostrar area de trabalho no computador dois", "mostrar_area_de_trabalho", "ananuke"),
            ("mostrar area de trabalho no ana no que", "mostrar_area_de_trabalho", "ananuke"),
        ]

        for frase, acao_esp, alvo_esp in frases_alias:
            res = obter_comando(frase)
            self.assertIsNotNone(res, f"Alias de Ananuke não reconhecido: '{frase}'")
            acao, alvo = res
            self.assertEqual(acao, acao_esp)
            self.assertEqual(alvo, alvo_esp)

    def test_04_no_ambiguous_phrases_without_target(self):
        """Garante que frases sem computador-alvo explícito nunca são aceitas."""
        frases_ambiguas = [
            "abrir navegador",
            "abrir youtube",
            "bloquear",
            "aumentar volume",
            "diminuir volume",
            "silenciar audio",
            "status",
            "abrir terminal",
            "fechar navegador",
            "reiniciar",
            "mostrar area de trabalho",
        ]
        for f in frases_ambiguas:
            self.assertIsNone(obter_comando(f), f"Frase ambígua '{f}' não deveria resolver para nenhum computador.")

    def test_05_legacy_energy_commands_strictly_preserved(self):
        """Valida que todos os comandos legados de WoL e desligamento SSH continuam intactos."""
        self.assertEqual(len(ENERGY_COMMANDS), 10)
        for frase, (acao, alvo) in ENERGY_COMMANDS.items():
            res = obter_comando(frase)
            self.assertIsNotNone(res, f"Comando legado '{frase}' sumiu do catálogo!")
            self.assertEqual(res, (acao, alvo))

    def test_06_confirmation_requirements(self):
        """Valida que exatamente desligar, fechar_navegador e reiniciar exigem confirmação."""
        self.assertTrue(requires_confirmation("desligar"))
        self.assertTrue(requires_confirmation("fechar_navegador"))
        self.assertTrue(requires_confirmation("reiniciar"))

        acoes_sem_confirmacao = [
            "ligar",
            "abrir_navegador",
            "abrir_youtube",
            "bloquear",
            "aumentar_volume",
            "diminuir_volume",
            "silenciar_audio",
            "status_computador",
            "abrir_terminal",
            "mostrar_area_de_trabalho",
        ]
        for acao in acoes_sem_confirmacao:
            self.assertFalse(requires_confirmation(acao), f"Ação {acao} não deveria exigir confirmação.")

    def test_07_is_valid_confirmation_logic(self):
        """Valida correspondência das frases de confirmação por ação pendente."""
        self.assertTrue(is_valid_confirmation("confirmar", "fechar_navegador"))
        self.assertTrue(is_valid_confirmation("confirmar fechar navegador", "fechar_navegador"))
        self.assertTrue(is_valid_confirmation("confirmar", "reiniciar"))
        self.assertTrue(is_valid_confirmation("confirmar reinicio", "reiniciar"))
        self.assertTrue(is_valid_confirmation("confirmar reinicializacao", "reiniciar"))
        self.assertTrue(is_valid_confirmation("confirmar desligamento", "desligar"))

        # Incompatibilidades
        self.assertFalse(is_valid_confirmation("confirmar desligamento", "reiniciar"))
        self.assertFalse(is_valid_confirmation("confirmar reinicio", "desligar"))
        self.assertFalse(is_valid_confirmation("confirmar fechar navegador", "reiniciar"))

    def test_08_vosk_grammar_integrity(self):
        """Valida que a gramática Vosk contém todas as frases necessárias sem acentos."""
        self.assertIn("assistente", VOSK_GRAMMAR)
        self.assertIn("cancelar", VOSK_GRAMMAR)
        self.assertIn("[unk]", VOSK_GRAMMAR)

        for frase in ALL_COMMANDS.keys():
            self.assertIn(frase, VOSK_GRAMMAR, f"Frase '{frase}' ausente em VOSK_GRAMMAR.")
            # Nenhuma palavra deve ter acento na gramática Vosk pt-0.3
            self.assertEqual(frase, remover_acentos(frase), f"Frase '{frase}' contém diacríticos!")


if __name__ == "__main__":
    unittest.main()
