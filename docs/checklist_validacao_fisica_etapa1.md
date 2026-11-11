# Checklist de Validação Física de Bancada — Etapa 1: Simulação Obrigatória (Classe B)

> **Projeto:** Jarvis Voice — Microfone Duplo (LG-X230DSV + Proxmox ME6S USB)  
> **Branch:** `feat/dual-mic-secure-integration`  
> **Pull Request:** #4  
> **Ambiente:** Proxmox LXC 110 (`erp-poder` / `192.168.0.126`) e Bancada Física  
> **Condição de Execução:** **EXECUTAR EXCLUSIVAMENTE APÓS AUTORIZAÇÃO FORMAL DO OPERADOR PARA A ETAPA 1.**  
> **Regra Fundamental de Segurança:** **NENHUM COMPUTADOR SERÁ LIGADO OU DESLIGADO NESTA ETAPA.**

---

## 1. Objetivo e Princípios da Validação Física (Classe B)

Este documento define o protocolo oficial de bancada para a homologação física dos dois microfones em tempo real após a aplicação supervisionada do patch e reinício dos serviços em modo simulado.

### Princípios:
1. **Comandos 100% Simulados:** `JARVIS_WOL_REAL=0` e `JARVIS_SSH_REAL=0` ativos e confirmados em memória.
2. **Entrada Vocal Real:** Falar diretamente nos microfones físicos (Fifine ME6S USB e LG-X230DSV). Nenhuma injeção sintética de API nesta bateria.
3. **Validação Multissensorial:** Resposta visual imediata no display do LG, chime sonoro audível, log sanitizado no journal e persistência segura em `/var/lib/jarvis/acoes_recentes.json`.
4. **Resiliência e Tolerância a Falhas:** Comprovação em bancada de recuperação após perda de USB e perda de rede Wi-Fi.

---

## 2. Pré-Requisitos e Estado Inicial da Bancada

- [ ] **Auditoria Pré-Voo Aprovada:** Script `/opt/jarvis-voice/scripts/validar_pre_implantacao.sh` executado com 8/8 condições OK.
- [ ] **Serviço Operacional Ativo:** `jarvis-voice.service` ativo e auditado via `/opt/jarvis-voice/scripts/validar_pos_inicializacao.sh`.
- [ ] **Gateway TLS Ativo:** `jarvis-audio-gateway.service` ativo, ouvindo nas portas TCP 8088 (TLS) e HTTP 8089.
- [ ] **Display Server Ativo:** `jarvis-display.service` ativo na porta 8080 (`http://192.168.0.126:8080`).
- [ ] **Microfone USB Fifine ME6S:** Conectado à porta USB do Proxmox, LED indicador aceso, passthrough ativo para LXC 110.
- [ ] **Smartphone LG-X230DSV:** Conectado à rede Wi-Fi (`192.168.0.175`), app Jarvis Display em execução em tela cheia, exibindo estado verde `AGUARDANDO PALAVRA DE ATIVAÇÃO` e `Conectado (TLS)`.
- [ ] **Terminal de Acompanhamento Aberto:**
  ```bash
  ssh root@192.168.0.126 "journalctl -u jarvis-voice.service -u jarvis-audio-gateway.service -f --no-tail"
  ```

---

## 3. Roteiro de Testes Físicos de Bancada (8 Etapas)

### ETAPA 1 — Ativação e Comando pelo Microfone USB ME6S
* **Ação do Operador:** Posicionar-se a ~1-2 metros do microfone ME6S e falar: `"assistente"`.
  * **Critério de Aceite Visual:** Display do LG transiciona imediatamente para tela azul/cyan (`OUVINDO / AGUARDANDO COMANDO`).
  * **Critério de Aceite Sonoro:** Smartphone LG reproduz o chime sonoro de ativação.
  * **Critério de Aceite no Journal:** Log exibe `[Origem: me6s]` e `ATIVADO! Aguardando comando.`
* **Ação do Operador:** Falar claramente: `"ligar fusion"`.
  * **Critério de Aceite no Journal:** Log exibe `Computador: Fusion (Windows)` e `SIMULACAO: Wake-on-LAN.`
  * **Garantia de Segurança:** Nenhum pacote físico WoL é transmitido.
  * **Critério de Aceite Visual:** Display do LG exibe confirmação do comando e retorna para `IDLE`.
- [ ] **Etapa 1 Aprovada**

---

### ETAPA 2 — Ativação e Comando pelo Smartphone LG
* **Ação do Operador:** Falar próximo ao smartphone LG: `"assistente"`.
  * **Critério de Aceite Visual:** Display transiciona para estado `OUVINDO` com feedback luminoso.
  * **Critério de Aceite Sonoro:** Chime de ativação reproduzido pelo alto-falante do celular.
  * **Critério de Aceite no Journal:** Gateway registra transcrição `[Vosk-LG] Transcrição detectada: 'assistente'` e abre sessão vinculada a `lg_mic`.
* **Ação do Operador:** Falar: `"desligar fusion"`.
  * **Critério de Aceite Visual:** Display transiciona para estado de alerta laranja/amarelo (`CONFIRMAR desligamento de Fusion (Windows)? Diga confirmar ou cancelar`).
  * **Critério de Aceite no Journal:** Log registra solicitação de confirmação e inicia janela temporal de 20 segundos.
- [ ] **Etapa 2 Aprovada**

---

### ETAPA 3 — Reconhecimento Simultâneo ("assistente" ou "ligar fusion" em Ambos os Microfones)
* **Ação do Operador:** Pronunciar `"assistente"` simultaneamente próximo a ambos os microfones (<100ms de intervalo), ou proferir `"ligar fusion"` de modo audível a ambos os transdutores.
  * **Critério de Aceite:** O árbitro de sessão e o controlador identificam as ativações e comandos concorrentes, aplicando deduplicação bidirecional.
  * **Critério de Coerência:** Estritamente **UMA única sessão** é mantida e **UMA única ação** é registrada no histórico.
  * **Critério de Desempenho:** Latência nula de sondagem (<1ms) em modo simulado; nenhum timeout IPC de 2s.
  * **Critério de UX:** Apenas **UM chime sonoro** é emitido; nenhum erro de concorrência ou colisão no journal.
- [ ] **Etapa 3 Aprovada**

---

### ETAPA 4 — Confirmação Cruzada de Desligamento Simulado
* **Ação do Operador:**
  1. No smartphone LG, falar: `"assistente"` $\rightarrow$ `"desligar fusion"`. (Display entra em `CONFIRMANDO`).
  2. No microfone USB ME6S, falar claramente: `"confirmar desligamento"`.
  * **Critério de Aceite Cruzado:** O controlador aceita a confirmação recebida pelo canal ME6S para a sessão iniciada pelo LG.
  * **Critério de Aceite no Journal:** Log exibe com clareza: `SIMULACAO SSH para Fusion (Windows)`.
  * **Garantia de Segurança:** O computador Fusion permanece fisicamente **LIGADO** (nenhuma conexão SSH real é disparada).
  * **Critério de Persistência:** O arquivo `/var/lib/jarvis/acoes_recentes.json` registra a ação `desligar:fusion` com `status: "concluido"`.
- [ ] **Etapa 4 Aprovada**

---

### ETAPA 5 — Proteção Contra Retransmissão e Comandos Duplicados
* **Ação do Operador:** Logo após a confirmação da Etapa 4, falar novamente `"confirmar"`.
  * **Critério de Aceite:** O sistema ignora ou responde com `sem_confirmacao_pendente`.
  * **Garantia de Segurança:** Nenhuma segunda simulação de SSH é emitida; idempotência estrita respeitada.
- [ ] **Etapa 5 Aprovada**

---

### ETAPA 6 — Perda e Recuperação Física do Microfone USB ME6S
* **Ação do Operador:**
  1. Desconectar temporariamente o cabo USB do microfone Fifine ME6S da porta Proxmox.
  2. Aguardar 3 segundos e observar o log no terminal:
     * Log esperado: `[ME6S] Captura interrompida (dispositivo desconectado ou arecord terminou). Tentando reconectar...`
  3. Reconectar o cabo USB.
  4. Observar a reconexão automática:
     * Log esperado: `[ME6S] Microfone conectado e captura de audio iniciada.`
  5. Falar `"assistente"` no ME6S.
  6. **Critério de Aceite:** Reconhecimento vocal retoma imediatamente sem necessidade de reiniciar o serviço.
- [ ] **Etapa 6 Aprovada**

---

### ETAPA 7 — Reconexão Física de Rede do Smartphone LG
* **Ação do Operador:**
  1. No smartphone LG, desativar o Wi-Fi nas configurações do Android por 5 segundos.
  2. Observar o log no terminal:
     * Log esperado: `[JarvisGateway] Cliente LG desconectado`.
  3. Reativar o Wi-Fi no LG.
  4. Observar a recuperação:
     * Log esperado: `[JarvisGateway] Cliente LG conectado ... Handshake autenticado`.
     * Tela do celular: Status atualiza automaticamente para `Conectado (TLS)`.
  5. Falar `"assistente"` no celular e confirmar resposta normal.
- [ ] **Etapa 7 Aprovada**

---

### ETAPA 8 — Consolidação das Evidências e Fechamento do Gate 1
- [ ] **Auditoria de Hardware:** Confirmar que todos os computadores da rede mantiveram seus estados originais intactos.
- [ ] **Auditoria de Estado Persistente:** Inspecionar `/var/lib/jarvis/acoes_recentes.json` e validar permissões `0600` e registros simulados.
- [ ] **Captura de Tela:** Registrar screenshot da interface do LG para o relatório de evidências.
- [ ] **Sanitização de Logs:** Extrair o trecho do journal da sessão, aplicar máscara nos identificadores sensíveis e anexar ao relatório.
- [ ] **Parecer da Etapa 1:** Formalizar aprovação da simulação antes de solicitar abertura do Gate 2.
