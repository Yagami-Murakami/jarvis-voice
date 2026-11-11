# Runbook de Implantação Supervisionada — Etapa 1: Simulação Obrigatória

> **Projeto:** Jarvis Voice — Microfone Duplo (LG-X230DSV + Proxmox ME6S USB)  
> **Branch:** `feat/dual-mic-secure-integration`  
> **Pull Request:** #4  
> **Ambiente:** Proxmox LXC 110 (`erp-poder` / `192.168.0.126`)  
> **Gate:** Gate 1 (Simulação Supervisionada Obrigatória)  
> **Regra Fundamental de Segurança:** **NENHUM COMPUTADOR SERÁ LIGADO OU DESLIGADO NESTA ETAPA.**

---

## 1. Objetivo e Princípios do Gate 1

O objetivo desta etapa é validar a integração operacional entre o microfone USB Fifine ME6S (conectado diretamente ao Proxmox via passthrough) e o microfone do smartphone LG-X230DSV (conectado via TLS ao Gateway de Áudio e barramento IPC UNIX Domain Socket).

### Princípios Inegociáveis:
1. **Simulação Obrigatória:** `JARVIS_WOL_REAL=0` e `JARVIS_SSH_REAL=0` prevalecem em nível de serviço systemd antes e depois de qualquer reinício.
2. **Durabilidade e Idempotência:** `JARVIS_REQUIRE_PERSISTENCE=1` garante que falhas de gravação em `/var/lib/jarvis/acoes_recentes.json` bloqueiem execuções de hardware (fail-closed).
3. **Cadeia de Custódia e Backups:**
   - **Backup Original Imutável (`c51d58e6...`):** Preservado em `/opt/jarvis-voice/jarvis-operacional.py.backup_original` e `/var/backups/jarvis/` (`0440`). Base imutável para geração de patches.
   - **Backup da Versão Simulada v1 (`f6c72731...`):** Preservado em `/opt/jarvis-voice/jarvis-operacional.py.backup_simulacao_v1` e `/var/backups/jarvis/` (`0440`).
   - **Nova Versão Proposta v2 (`810aca6e...`):** Desacoplamento assíncrono, eliminação de sondagem de rede em simulação e deduplicação de comandos.
4. **Substituição Verdadeiramente Atômica:** Nunca grava in-place ou trunca o arquivo operacional. Cria um arquivo temporário no mesmo diretório, valida permissões `0755` e o SHA-256 e executa `mv -f` (POSIX `rename`).
5. **Pre-flight e Pós-Auditoria Obrigatórios:** O upgrade aborta imediatamente se o pre-flight falhar e aciona rollback automático para v1 se a auditoria pós-inicialização falhar.
6. **Rollback Granular e Seguro:** Suporta retorno a `v1` (`f6c727...`) ou `original` (`c51d58...`), validando o SHA-256 antes da cópia e **NUNCA reativando hardware real**.
7. **Preservação de Serviços:** O painel `ligar.casa` (`homelab-panel`), a interface `jarvis-display.service` e a configuração do LG são estritamente preservados.
8. **Execução Supervisionada:** A aplicação do patch e o reinício de serviços dependem de autorização explícita do operador.

---

## 2. Matriz de Hashes SHA-256 e Rastreabilidade de Versões

| Versão / Papel | Arquivo no LXC 110 | Hash SHA-256 | Função no Procedimento |
| :--- | :--- | :--- | :--- |
| **Original de Fábrica** | `/opt/jarvis-voice/jarvis-operacional.py.backup_original`<br>`/var/backups/jarvis/jarvis-operacional.py.backup_original` | `c51d58e6d3a19003eac55a827212e5e084d583fea43389ec0ea5466080ee2d54` | **Base imutável para geração do patch** e fallback de fábrica. |
| **Simulação v1 (Atualmente em Execução)** | `/opt/jarvis-voice/jarvis-operacional.py`<br>`/opt/jarvis-voice/jarvis-operacional.py.backup_simulacao_v1`<br>`/var/backups/jarvis/jarvis-operacional.py.backup_simulacao_v1` | `f6c7273116aea4c4b34c0c6007d335b7925356ecb174e7066781716f2f604ed8` | Versão ativa na bancada. Ponto de restauração para rollback granular. |
| **Simulação v2 Proposta (Atual)** | `/opt/jarvis-voice/scripts/jarvis_operacional_corrigido.py`<br>`/tmp/jarvis-build-XXXXXX.py` | `810aca6e25897f6b95aaf297ca8fdff7d9659270bf079a8c398c4ee21df93b42` | Nova versão: latência zero em simulação, worker assíncrono e deduplicação. |
| **Patch Unificado Oficial** | `/opt/jarvis-voice/patches/jarvis-operacional-dual.patch` | *Diff unificado contra backup original* | Aplica sobre `c51d58e6...` gerando `810aca6e...` com código de retorno 0. |

---

## 3. Pré-Requisitos e Condições de Segurança Pré-Voo (Pre-Flight Fail-Closed)

O script automatizado `scripts/validar_pre_implantacao.sh` verifica 8 condições críticas, interrompendo o processo imediatamente (`exit 1`) caso ocorra qualquer divergência:

| # | Condição de Segurança | Verificação Técnica | Critério de Sucesso |
|---|---|---|---|
| 1 | **Integridade do Arquivo Operacional e Backups** | `sha256sum` do arquivo em execução e backups | Operacional = `f6c727...` (ou `c51d58...`); Backups = `c51d58...` e `f6c727...` |
| 2 | **Ausência de Mocks e Sintaxe** | `grep -iE "MagicMock"` e `py_compile` | 0 ocorrências de mocks; sintaxe Python limpa |
| 3 | **Geração Segura a Partir do Original** | Aplicação do patch sobre cópia do backup original | SHA-256 gerado = `810aca6e...` (código de saída 0) |
| 4 | **Reversibilidade Garantida** | `patch -R --dry-run` sobre a nova versão | Código de saída 0; reversibilidade 100% |
| 5 | **Simulação no Systemd** | Drop-in `zz-simulacao-etapa1.conf` presente; `systemctl show -p Environment` | `JARVIS_WOL_REAL=0`, `JARVIS_SSH_REAL=0`, `JARVIS_REQUIRE_PERSISTENCE=1` ativos; flags reais ausentes |
| 6 | **Diretório Persistente** | `ls -ld /var/lib/jarvis` e teste de escrita por `tuninho` | Permissões `0750`, proprietário `tuninho:tuninho`, escrita validada |
| 7 | **Serviços Auxiliares Ativos** | `systemctl is-active` para Gateway e Display | Ambos retornam `active` |
| 8 | **Dependências de Runtime** | Importação de Vosk, Kaldi e `app.SERVIDORES` (`homelab-panel`) | Módulos carregados com sucesso |

---

## 4. Procedimento de Aplicação (Etapa 1 — Simulação)

### Passo 4.1: Validação Prévia Automatizada (Pre-Flight Obrigatório)
O pre-flight é executado automaticamente pelo script de upgrade como Fase 1. Para executá-lo isoladamente:
```bash
# Executado como root no LXC 110
/opt/jarvis-voice/scripts/validar_pre_implantacao.sh
```

### Passo 4.2: Geração e Validação em Modo Dry-Run
```bash
# Executar validação completa e compilação em arquivo temporário (sem alterar produção):
/opt/jarvis-voice/scripts/upgrade_etapa1_simulacao.sh
```

### Passo 4.3: Aplicação Efetiva Supervisionada (Requer Autorização do Operador)
> **Aviso de Segurança Crítico:** A execução do comando `--apply` só deve ser realizada após autorização formal do operador.

```bash
/opt/jarvis-voice/scripts/upgrade_etapa1_simulacao.sh --apply
```

*Fluxo de execução do upgrade:*
1. **Fase 1 (Pre-Flight):** Executa `validar_pre_implantacao.sh` compulsoriamente (aborta em caso de falha).
2. **Fase 2 (Auditoria e Validação):**
   - Valida que o SHA do arquivo operacional atual é de versão homologada (`c51d58...` ou `f6c727...`). Hash desconhecido aborta imediatamente;
   - Valida os backups original e v1;
   - Valida configurações de simulação no systemd;
   - Gera a nova versão a partir do backup original e valida hash `810aca6e...`;
   - Valida sintaxe Python e reversibilidade do patch.
3. **Fase 3 (Substituição Atômica e Pós-Auditoria):**
   - Cria arquivo temporário no mesmo diretório (`.jarvis-operacional.py.tmp.XXXXXX`), copia, define `chmod 0755` e valida SHA;
   - Executa `mv -f` (substituição verdadeiramente atômica, sem escrita direta in-place);
   - Reinicia `jarvis-voice.service` e `jarvis-audio-gateway.service`;
   - Valida configurações de simulação no systemd pós-reinicialização;
   - Executa `validar_pos_inicializacao.sh`. **Se a auditoria pós-inicialização falhar, dispara rollback automático para v1.**

---

## 5. Procedimento de Rollback Granular

Se for necessária a reversão da Etapa 1, utilizar o script oficial de rollback:

```bash
# Reverter para a versão de simulação v1 anterior (f6c72731...)
/opt/jarvis-voice/scripts/rollback_etapa1_simulacao.sh v1

# Reverter para a versão de fábrica original (c51d58e6...)
/opt/jarvis-voice/scripts/rollback_etapa1_simulacao.sh original
```

*Garantias do Rollback:*
- Substituição atômica via arquivo temporário e `mv -f`;
- Validação prévia de hash SHA-256 do backup antes de restaurar;
- Preservação estrita das flags `JARVIS_WOL_REAL=0`, `JARVIS_SSH_REAL=0` e `JARVIS_REQUIRE_PERSISTENCE=1` no drop-in systemd;
- Auditoria de memória do processo restaurado (`/proc/$PID/environ`);
- **NUNCA reativa hardware real automaticamente.**
