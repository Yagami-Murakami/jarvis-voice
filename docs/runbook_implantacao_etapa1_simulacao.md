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

## 4. Separação de Testes: Automatizados (Classe A) vs. Físicos de Bancada (Classe B)

### Classe A: Testes Automatizados em Ambiente Isolado (Pré-Implantação)
Executados em processo isolado sem tocar no serviço de produção:
```bash
/opt/jarvis-voice/venv/bin/python3 /opt/jarvis-voice/scripts/testes_automatizados_simulacao.py
```
*Cobertura dos 9 Cenários Automatizados:*
1. **Diferenciação de Portas e Schemas:** Gateway HTTP API (porta 18089 / `/api/mics`) vs. Jarvis Display (porta 18080 / `/api/status`).
2. **TLS 1.2+ Estrito e Reconexão:** Verificação com certificado (`ssl.CERT_REQUIRED`, SAN `IP:127.0.0.1`), handshake `JMIC`, envio de frames PCM (1280 bytes), desconexão e reconexão limpa.
3. **Detecção e Recuperação ME6S:** Simulação de término de processo/pipe de áudio `arecord`, detecção de interrupção, auto-recovery e retorno da fila de áudio.
4. **Injeção de Eventos Cruzados via Endpoint Existente:** `POST /api/test/inject` com `fonte`, `evento`, `texto` (`me6s` wake -> `lg_mic` command `ligar fusion`), verificando WoL simulado (0 pacotes) e latência nula de sondagem de rede.
5. **Confirmação Cruzada de Desligamento Simulado:** `lg_mic` wake -> `me6s` command `desligar fusion` -> `lg_mic` confirm, verificando SSH simulado (0 conexões) e gravação atômica em `/var/lib/jarvis/acoes_recentes.json`.
6. **Proteção contra Retransmissão:** Deduplicação de confirmações repetidas com bloqueio de reexecução.
7. **Operações com Latência Elevada (6s a 10s) sem Bloqueio IPC:** Desacoplamento assíncrono com worker em segundo plano; retorno de ACK de aceitação imediato (<1.0s) para o Gateway enquanto o worker conclui a persistência.
8. **Deduplicação de Comandos Simultâneos (LG + ME6S):** Reconhecimento concorrente do mesmo comando ("ligar fusion") dentro da janela temporal de 3.5s; garante que estritamente UMA ação é executada e persistida.
9. **Sincronização Fiel de Confirmação do Gateway (Fail-Closed):** Gateway nunca anuncia sucesso local prematuramente; se o controlador rejeitar ou falhar na persistência, o Gateway reflete estritamente `sucesso=False` e o erro real.

---

### Classe B: Protocolo de Validação Física Supervisionada (Pós-Autorização)
Executado em bancada física com fala real exclusivamente após autorização da Etapa 1:
Consulte o checklist oficial em: [`docs/checklist_validacao_fisica_etapa1.md`](checklist_validacao_fisica_etapa1.md).

*Roteiro de 8 Etapas em Bancada:*
1. **Fala Real no ME6S USB:** Operador fala `"assistente"` -> `"ligar fusion"`. Valida display do LG, chime sonoro e log `SIMULACAO: Wake-on-LAN`.
2. **Fala Real no LG:** Operador fala `"assistente"` -> `"desligar fusion"`. Valida transição para estado de alerta de confirmação.
3. **Reconhecimento Simultâneo:** Fala simultânea nos dois microfones; valida sessão única (`session_id`) e chime único.
4. **Confirmação Cruzada:** LG inicia desligamento -> ME6S fala `"confirmar desligamento"`. Valida `SIMULACAO SSH para Fusion` (computador permanece ligado).
5. **Proteção de Idempotência:** Repetição imediata de `"confirmar"` rejeitada sem reexecução.
6. **Perda e Retorno Físico do ME6S USB:** Desconexão física do cabo USB do Proxmox por 5s, reconexão e validação de retomada da fala.
7. **Reconexão Física do Smartphone LG:** Desativação de Wi-Fi no LG por 5s, reativação, reconexão TLS automática e retomada de streaming.
8. **Fechamento e Auditoria:** Verificação de integridade dos equipamentos e exportação de logs sanitizados.

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
