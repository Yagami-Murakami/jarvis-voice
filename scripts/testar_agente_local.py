#!/usr/bin/env python3
"""Helper seguro para homologação física supervisionada do Jarvis PC Control V1.

Lê as credenciais exclusivamente de variáveis de ambiente:
  - JARVIS_PC_AGENT_TOKEN
  - JARVIS_PC_ANANUKE_TOKEN (para ananuke)
  - JARVIS_PC_FUSION_TOKEN (para fusion)

Nenhum segredo é passado por linha de comando ou exibido em logs.
Executa estritamente os comandos não destrutivos de homologação.
"""
from __future__ import annotations

import argparse
import os
import sys
import time

# Garante que o pacote pc_control seja encontrado a partir de qualquer diretório
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from pc_control.client import PcControlClient  # noqa: E402

COMANDOS_NAO_DESTRUTIVOS = [
    ("status_computador", {}),
    ("aumentar_volume", {}),
    ("diminuir_volume", {}),
    ("silenciar_audio", {}),
    ("mostrar_area_de_trabalho", {}),
    ("abrir_terminal", {}),
    ("abrir_navegador", {}),
    ("abrir_youtube", {}),
]


def main() -> int:
    parser = argparse.ArgumentParser(description="Teste interativo de agente PC Control local")
    parser.add_argument("--alvo", choices=["ananuke", "fusion"], default="ananuke", help="Computador alvo")
    parser.add_argument("--url", default="http://127.0.0.1:8092", help="URL do agente local")
    parser.add_argument("--bloquear", action="store_true", help="Executa especificamente o comando de bloquear sessão")
    parser.add_argument("--ca-cert", default=None, help="Caminho do certificado CA (para testes HTTPS)")
    args = parser.parse_args()

    alvo = args.alvo.lower()

    # Leitura estrita de credencial por ambiente
    token = (
        os.getenv(f"JARVIS_PC_{alvo.upper()}_TOKEN")
        or os.getenv("JARVIS_PC_AGENT_TOKEN")
        or os.getenv("JARVIS_PC_CONTROL_TOKEN")
    )

    if not token or len(token.strip()) < 16:
        print(f"ERRO: Variável JARVIS_PC_{alvo.upper()}_TOKEN ou JARVIS_PC_AGENT_TOKEN ausente ou fraca (<16 chars).", file=sys.stderr)
        print("Configure o token no ambiente antes de executar este teste.", file=sys.stderr)
        return 1

    client = PcControlClient(
        tokens_por_alvo={alvo: token.strip()},
        url_overrides={alvo: args.url},
        ca_cert=args.ca_cert,
    )

    if args.bloquear:
        print(f"\n[ATENÇÃO] Solicitando bloqueio de tela para '{alvo}'...")
        ev_id = f"homolog-lock-{int(time.time()*1000)}"
        res = client.enviar_comando(alvo, "bloquear", ev_id, "sess-homolog-lock")
        print(f"Resultado: status={res.get('status')} | {res.get('mensagem_amigavel')}")
        return 0 if res.get("sucesso") else 1

    print(f"\n=======================================================")
    print(f" HOMOLOGAÇÃO FÍSICA SUPERVISIONADA — {alvo.upper()}")
    print(f" Endpoint: {args.url}")
    print(f" Total de comandos não destrutivos na suíte: {len(COMANDOS_NAO_DESTRUTIVOS)}")
    print(f"=======================================================\n")

    sucessos = 0
    falhas = 0

    for i, (acao, params) in enumerate(COMANDOS_NAO_DESTRUTIVOS, 1):
        ev_id = f"homolog-{acao}-{int(time.time()*1000)}"
        print(f"[{i}/{len(COMANDOS_NAO_DESTRUTIVOS)}] Executando '{acao}'...")
        t0 = time.time()
        res = client.enviar_comando(alvo, acao, ev_id, f"sess-homolog-{alvo}", params=params)
        duracao = time.time() - t0

        if res.get("sucesso"):
            sucessos += 1
            print(f"    -> [OK] ({duracao:.2f}s) {res.get('mensagem_amigavel')}")
        else:
            falhas += 1
            print(f"    -> [FALHA] ({duracao:.2f}s) erro={res.get('erro')} | {res.get('mensagem_amigavel')}")

        time.sleep(1.0)

    print(f"\nResumo: {sucessos} aprovados, {falhas} falhas de {len(COMANDOS_NAO_DESTRUTIVOS)} comandos.")
    return 0 if falhas == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
