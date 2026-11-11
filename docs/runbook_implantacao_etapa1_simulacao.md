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
3. **Rollback Seguro:** O procedimento de rollback restaura o backup verificado por SHA-256 e **NUNCA reativa hardware real automaticamente**.
4. **Preservação de Serviços:** O painel `ligar.casa` (`homelab-panel`), a interface `jarvis-display.service` e a configuração do LG são estritamente preservados.
5. **Execução Supervisionada:** A aplicação do patch e o reinício de serviços dependem de autorização explícita do operador.

---

## 2. Pré-Requisitos e Condições de Segurança Pré-Voo (Pre-Flight)

Antes de qualquer alteração, o script automatizado `scripts/validar_pre_implantacao.sh` verifica 8 condições críticas, interrompendo o processo imediatamente (`exit 1`) caso ocorra qualquer divergência:

| # | Condição de Segurança | Verificação Técnica | Critério de Sucesso |
|---|---|---|---|
| 1 | **Integridade do Arquivo Original e Backup** | `sha256sum /opt/jarvis-voice/jarvis-operacional.py` e `.backup_original` | SHA-256 = `c51d58e6d3a19003eac55a827212e5e084d583fea43389ec0ea5466080ee2d54` |
| 2 | **Ausência de Mocks e Sintaxe** | `grep -iE "MagicMock"` e `py_compile` | 0 ocorrências de mocks; sintaxe Python 3.11 limpa |
| 3 | **Patch Dry-Run** | `patch --dry-run -p1 < patches/jarvis-operacional-dual.patch` | Código de saída 0 (sem rejeições/hunks) |
| 4 | **Reversibilidade Garantida** | `patch -R --dry-run` sobre cópia temporária | Código de saída 0; restauração idêntica |
| 5 | **Simulação no Systemd** | `systemctl show -p Environment jarvis-voice.service` | `JARVIS_WOL_REAL=0`, `JARVIS_SSH_REAL=0`, `JARVIS_REQUIRE_PERSISTENCE=1` ativos; `producao.conf` desabilitado |
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

### Passo 3.4: Auditoria em Runtime do Novo Processo
```bash
NEW_PID=$(systemctl show -p MainPID --value jarvis-voice.service)
echo "Novo PID: $NEW_PID"
# Confirmar que o processo em execução NÃO possui permissão para comandos reais:
strings /proc/$NEW_PID/environ | grep -E "JARVIS_WOL_REAL|JARVIS_SSH_REAL|JARVIS_REQUIRE_PERSISTENCE"
```
*Saída obrigatória:*
```text
JARVIS_WOL_REAL=0
JARVIS_SSH_REAL=0
JARVIS_REQUIRE_PERSISTENCE=1
```

---

## 4. Plano de Testes Operacionais em Simulação

Com o sistema rodando em modo simulado, executar os seguintes cenários de bancada:

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
   - Desconectar temporariamente o cabo USB do microfone Fifine ME6S (ou suspender processo de captura).
   - Reconectar o cabo.
   - Verificar log do monitor: reabertura do stream de áudio pelo `arecord` e recuperação sem falha.

6. **Reconexão de Rede do LG:**
   - Alternar brevemente Wi-Fi no LG.
   - Verificar reconexão TLS automática ao gateway na porta 8088.

7. **Validação do Jarvis Display:**
   - Verificar sincronização de estados no aplicativo LG (`IDLE` -> `OUVINDO` -> `CONFIRMANDO` -> `IDLE`).
   - Capturar screenshot do display para evidência.

---

## 5. Procedimento de Rollback de Emergência

Caso ocorra qualquer comportamento anômalo durante a Etapa 1:

```bash
/opt/jarvis-voice/scripts/rollback_emergencia.sh
```

### O que o script de rollback executa:
1. Garante que `zz-simulacao-etapa1.conf` permaneça ativo (`JARVIS_WOL_REAL=0`, `JARVIS_SSH_REAL=0`, `JARVIS_REQUIRE_PERSISTENCE=1`).
2. Garante que `producao.conf` permaneça desabilitado (`producao.conf.disabled`).
3. Restaura o arquivo original `/opt/jarvis-voice/jarvis-operacional.py` a partir do backup `/opt/jarvis-voice/jarvis-operacional.py.backup_original`.
4. Valida a integridade por SHA-256 (`c51d58e6d3a19003eac55a827212e5e084d583fea43389ec0ea5466080ee2d54`).
5. Executa `systemctl daemon-reload` e reinicia `jarvis-voice.service`.

**Garantia de Segurança:** O rollback NUNCA reativa hardware real de forma automática.
