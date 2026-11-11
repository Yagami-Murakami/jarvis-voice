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
1. **Simulação Obrigatória:** `JARVIS_WOL_REAL=0` e `JARVIS_SSH_REAL=0` prevalecem em nível de serviço systemd.
2. **Durabilidade e Idempotência:** `JARVIS_REQUIRE_PERSISTENCE=1` garante que falhas de gravação em `/var/lib/jarvis/acoes_recentes.json` bloqueiem execuções de hardware (fail-closed).
3. **Backup Duplo Imutável:** Backup original mantido na pasta do projeto e em `/var/backups/jarvis/jarvis-operacional.py.backup_original` (somente leitura `0444`, fora da árvore atualizável).
4. **Rollback Seguro:** O procedimento de rollback valida o hash do backup **ANTES** de copiar e **NUNCA reativa hardware real automaticamente**.
5. **Preservação de Serviços:** O painel `ligar.casa` (`homelab-panel`), a interface `jarvis-display.service` e a configuração do LG são estritamente preservados.
6. **Execução Supervisionada:** A aplicação do patch e o reinício de serviços dependem de autorização explícita do operador.

---

## 2. Pré-Requisitos e Condições de Segurança Pré-Voo (Pre-Flight Fail-Closed)

Antes de qualquer alteração, o script automatizado `scripts/validar_pre_implantacao.sh` verifica 8 condições críticas, interrompendo o processo imediatamente (`exit 1`) caso ocorra qualquer divergência:

| # | Condição de Segurança | Verificação Técnica | Critério de Sucesso |
|---|---|---|---|
| 1 | **Integridade do Arquivo Original e Backups** | `sha256sum` do arquivo de produção, backup primário e backup externo imutável (`/var/backups/jarvis/`) | SHA-256 = `c51d58e6d3a19003eac55a827212e5e084d583fea43389ec0ea5466080ee2d54` em todos |
| 2 | **Ausência de Mocks e Sintaxe** | `grep -iE "MagicMock"` e `py_compile` | 0 ocorrências de mocks; sintaxe Python 3.11 limpa |
| 3 | **Disponibilidade e Dry-Run do Patch** | Existência de `patches/jarvis-operacional-dual.patch` e `patch --dry-run -p1` | Código de saída 0 (sem rejeições/hunks) |
| 4 | **Reversibilidade Garantida** | `patch -R --dry-run` sobre cópia temporária | Código de saída 0; restauração idêntica |
| 5 | **Simulação no Systemd** | Drop-in `zz-simulacao-etapa1.conf` presente; `producao.conf` ausente; `systemctl show -p Environment` | `JARVIS_WOL_REAL=0`, `JARVIS_SSH_REAL=0`, `JARVIS_REQUIRE_PERSISTENCE=1` ativos; flags reais bloqueadas |
| 6 | **Diretório Persistente** | `ls -ld /var/lib/jarvis` e teste de escrita por `tuninho` | Permissões `0750`, proprietário `tuninho:tuninho`, escrita validada |
| 7 | **Serviços Auxiliares Ativos** | `systemctl is-active` para Gateway e Display | Ambos retornam `active` |
| 8 | **Dependências de Runtime** | Importação de Vosk, Kaldi e `app.SERVIDORES` (`homelab-panel`) | Módulos carregados com sucesso |

---

## 3. Procedimento de Aplicação (Etapa 1 — Simulação)

### Passo 3.1: Validação Prévia Automatizada (Fail-Closed)
```bash
# Executado como root no LXC 110
/opt/jarvis-voice/scripts/validar_pre_implantacao.sh
```
*Se qualquer verificação falhar, abortar imediatamente e reportar ao operador.*

### Passo 3.2: Aplicação do Patch Operacional
```bash
cd /opt/jarvis-voice
patch -p1 < patches/jarvis-operacional-dual.patch
/opt/jarvis-voice/venv/bin/python3 -m py_compile /opt/jarvis-voice/jarvis-operacional.py
```

### Passo 3.3: Reinício Controlado dos Serviços
```bash
systemctl restart jarvis-voice.service
systemctl restart jarvis-audio-gateway.service
```

### Passo 3.4: Auditoria em Runtime do Processo Ativo
```bash
/opt/jarvis-voice/scripts/validar_pos_inicializacao.sh
```
*Validações executadas pelo script:*
- Status do serviço ativo;
- Leitura de `/proc/$PID/environ` confirmando `JARVIS_WOL_REAL=0`, `JARVIS_SSH_REAL=0`, `JARVIS_REQUIRE_PERSISTENCE=1` (sem expor segredos);
- Presença de `MODO SIMULACAO - SEM ACOES REAIS` no journal de inicialização;
- Existência e permissões `0600` do socket IPC `/run/jarvis/voice-event.sock`.

---

## 4. Plano de Testes Operacionais Cruzados em Simulação

Executar a suíte de testes cruzados com coleta contínua e sanitizada de logs:
```bash
/opt/jarvis-voice/scripts/testes_cruzados_etapa1_simulacao.sh
```

### Cenários Cobertos:
1. **Ativação pelo ME6S e Comando pelo LG:**
   - Pronunciar "assistente" próximo ao microfone ME6S (verificar log `[ME6S] Ouvi: assistente` e Jarvis Display).
   - Pronunciar comando pelo LG (ex.: "ligar fusion").
   - Verificar no log: `SIMULACAO: Wake-on-LAN.` (Nenhum pacote transmitido).

2. **Ativação pelo LG e Comando pelo ME6S:**
   - Pronunciar "assistente" no LG (verificar transição de tela para Ativo e som de chime).
   - Pronunciar comando pelo ME6S (ex.: "desligar fusion").
   - Verificar solicitação de confirmação: `CONFIRMAR desligamento de Fusion (Windows) ? Diga confirmar ou cancelar.`

3. **Reconhecimento Simultâneo ("assistente"):**
   - Falar "assistente" em ambos os microfones.
   - Verificar deduplicação pelo árbitro: uma única sessão é iniciada, sem concorrência de `session_id`.

4. **Confirmação Cruzada de Desligamento Simulado:**
   - ME6S inicia o desligamento; LG envia a confirmação ("confirmar").
   - Verificar log: `SIMULACAO SSH para Fusion (Windows)`.
   - Confirmar que o computador permanece ligado e o estado de conclusão foi gravado em `/var/lib/jarvis/acoes_recentes.json`.

5. **Recuperação de Desconexão do ME6S (USB):**
   - Simular desconexão temporária do microfone Fifine ME6S (ou interrupção do stream ALSA).
   - Reconectar o microfone.
   - Verificar reabertura automática pelo `arecord` e auto-recovery do monitor.

6. **Reconexão de Rede do LG:**
   - Reconexão TLS automática na porta 8088 com handshake validado.

7. **Validação do Jarvis Display:**
   - Sincronização visual de estados no aplicativo LG (`IDLE` -> `OUVINDO` -> `CONFIRMANDO` -> `IDLE`).
   - Som de chime de ativação reproduzido.

---

## 5. Procedimento de Rollback de Emergência Seguro

Caso ocorra qualquer comportamento anômalo durante a Etapa 1:

```bash
/opt/jarvis-voice/scripts/rollback_emergencia.sh
```

### O que o script de rollback executa compulsoriamente:
1. **Validação prévia do hash SHA-256 do backup ANTES de copiar:** Se o hash do backup em `/opt/jarvis-voice/` ou `/var/backups/jarvis/` divergir de `c51d58e6d3a19003eac55a827212e5e084d583fea43389ec0ea5466080ee2d54`, o rollback aborta imediatamente (fail-closed) sem tocar no arquivo operacional.
2. **Garante simulação ativa no systemd:** Mantém `JARVIS_WOL_REAL=0`, `JARVIS_SSH_REAL=0` e `JARVIS_REQUIRE_PERSISTENCE=1` no drop-in `zz-simulacao-etapa1.conf`. Se `producao.conf` existir, é renomeado para `.disabled`.
3. **Restauração a partir do backup verificado:** Copia o backup sobre o arquivo de produção e valida o SHA-256 novamente.
4. **Reinício seguro:** Executa `systemctl daemon-reload` e reinicia `jarvis-voice.service`.
5. **Auditoria em memória:** Inspeciona `/proc/$PID/environ` do novo processo e confirma que as flags de simulação estão presentes na memória física do processo restaurado.

**Garantia de Segurança:** O rollback NUNCA reativa hardware real de forma automática.
