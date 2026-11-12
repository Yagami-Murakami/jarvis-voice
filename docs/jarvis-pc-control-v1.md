# Jarvis PC Control V1 — Arquitetura, Segurança e Manual Operacional

**Projeto:** Jarvis Voice Dual-Mic (`Yagami-Murakami/jarvis-voice`)  
**Módulo:** `pc_control` (Jarvis PC Control V1)  
**Versão:** 1.0.0  
**Data:** 12 de novembro de 2026  
**Status:** Implementado, testado e validado em ambiente isolado (Feature Flag padrão `JARVIS_PC_CONTROL_ENABLED=0`).

---

## 1. Visão Geral e Arquitetura

O **Jarvis PC Control V1** expande as capacidades do ecossistema de voz Jarvis, permitindo que comandos de controle operacional e multimídia sejam executados diretamente em duas estações de trabalho de bancada a partir dos microfones **Fifine ME6S** (microfone USB principal) e **LG-X230DSV** (microfone secundário sem fio via TCP):

1. **Fusion (Windows 10/11 - IP `192.168.0.128`):** Computador principal para desenvolvimento e produtividade.
2. **Ananuke (Debian 13 / Xfce - IP `192.168.0.131`):** Computador local para testes, bancada e instrumentação eletrônica.

### 1.1 Diagrama de Arquitetura

```text
[ Fifine ME6S (USB) ]      [ LG-X230DSV (TCP:8090) ]
         \                            /
          v                          v
   +---------------------------------------+
   |   Gateway de Áudio / Árbitro de Voz   |
   |   (Deduplicação de Sessão 1.5s)       |
   +---------------------------------------+
                      |
           (UNIX Domain Socket IPC)
                      v
   +---------------------------------------+      +---------------------------+
   |  Jarvis Controlador Operacional       | ---> | Jarvis Display Server     |
   |  (jarvis_operacional_corrigido.py)    |      | (Status, Alertas e SSE)   |
   +---------------------------------------+      +---------------------------+
                      |
              (PcControlDispatcher)
            [JARVIS_PC_CONTROL_ENABLED]
                      |
                      v
           (PcControlClient HTTP)
        [HMAC-SHA256 + Nonce + Drift]
          /                       \
         / (Porta 8092)            \ (Porta 8092)
        v                           v
+-----------------------+   +-----------------------+
|  Fusion (Windows)     |   |  Ananuke (Debian 13)  |
|  WindowsPcAgent       |   |  LinuxPcAgent         |
|  (Sessão Interativa)  |   |  (Sessão Xfce Usuário)|
+-----------------------+   +-----------------------+
```

---

## 2. Princípios de Segurança e Isolamento

O módulo foi projetado sob rigoroso padrão de segurança defensiva:

1. **Ausência de Shell Arbitrário:** Nem o cliente nem os agentes aceitam comandos de shell arbitrários. Toda ação é despachada exclusivamente através de funções fixas em uma allowlist estrita (`ALLOWED_PC_ACTIONS`).
2. **Autenticação HMAC-SHA256 sem Transmissão de Chaves:** Cada requisição HTTP enviada aos agentes é assinada digitalmente com token seguro individual ou compartilhado (`JARVIS_PC_FUSION_TOKEN`, `JARVIS_PC_ANANUKE_TOKEN` ou `JARVIS_PC_AGENT_TOKEN`). A assinatura engloba timestamp, nonce, target, action e o corpo da requisição. O cabeçalho `X-Jarvis-Token` não é transmitido (impedindo vazamento de segredos em trânsito) e tokens fracos/padrão são estritamente rejeitados em conformidade com fail-closed (mínimo de 16 caracteres).
3. **Prevenção contra Ataques de Replay (NonceCache):** Cada requisição possui um `nonce` criptográfico único gerado via `secrets.token_hex(16)`. Os agentes mantêm um cache thread-safe com expiração de 60 segundos; se um nonce já foi processado, a requisição é sumariamente rejeitada com HTTP 409 Conflict.
4. **Janela Temporal Restrita (Timestamp Drift):** O timestamp da requisição não pode divergir mais do que 5,0 segundos do relógio local do agente.
5. **Comunicação Segura TLS e Restrição de IP:** Os agentes suportam terminação TLS/HTTPS nativa (`--cert`/`--key` ou `JARVIS_PC_TLS_CERT`/`JARVIS_PC_TLS_KEY`) e validação de certificado de CA no cliente (`JARVIS_PC_CA_CERT`). Quando TLS está configurado, o agente exige certificados completos e válidos, falhando na inicialização caso estejam ausentes ou corrompidos, impedindo estritamente qualquer fallback silencioso para HTTP. Para o ambiente de produção, a comunicação opera em HTTPS com verificação estrita de certificados e sem bypass (proibido o uso de `JARVIS_PC_TLS_INSECURE_SKIP_VERIFY`). Adicionalmente, os agentes implementam filtragem de origem por IP (`--allowed-ips` ou `JARVIS_PC_ALLOWED_IPS`), rejeitando com 403 Forbidden qualquer chamada externa a IPs autorizados (ex: IP do controlador LXC 110 `192.168.0.126`).
6. **Confirmação em Duas Etapas e Correspondência Estrita:** Comandos destrutivos ou de interrupção operacional (`fechar_navegador` e `reiniciar`, assim como o já existente `desligar`) exigem obrigatoriamente confirmação explícita de voz do operador. A confirmação exige estrita correspondência de `session_id`, ação e computador-alvo; confirmações sem sessão válida ou órfãs são rejeitadas.
7. **Fechamento Gracioso de Navegadores:** O encerramento de navegadores no Windows e Linux é realizado de modo estritamente gracioso (`WM_CLOSE` no Windows via taskkill sem `/F`; `wmctrl -i -c` no Linux enviando `_NET_CLOSE_WINDOW`), preservando abas, sessões e trabalho do usuário sem perda de dados. No Linux, caso `wmctrl` não esteja disponível, o agente NÃO recorre a encerramento por sinal (`pkill`/`kill`) e retorna erro seguro fail-closed (`fechamento_gracioso_indisponivel`), garantindo integridade dos processos. Além disso, retorna falha segura caso nenhum navegador esteja em execução.
8. **Feature Flag Segura:** O recurso é governado pela variável de ambiente `JARVIS_PC_CONTROL_ENABLED`. O valor padrão é estritamente `0` (desativado). Em caso de ausência ou valor diferente de `1`, nenhum comando de PC Control é emitido.
9. **Isolamento de Produção:** Nenhuma modificação foi ou será realizada no container de produção LXC 110 (192.168.0.126) sem autorização expressa e checklist prévio.

---

## 3. Matriz dos 11 Comandos por Plataforma (22 Combinações)

| # | Ação | Computador Fusion (Windows) | Computador Ananuke (Debian 13 / Xfce) | Requer Confirmação? |
|---|------|-----------------------------|---------------------------------------|:-------------------:|
| 1 | **Abrir navegador** | Lança navegador padrão via `os.startfile("about:blank")` ou `webbrowser.open` | Lança navegador via `exo-open --launch WebBrowser` ou `xdg-open` | Não |
| 2 | **Abrir YouTube** | Abre URL fixa `https://www.youtube.com` no navegador padrão | Abre URL fixa `https://www.youtube.com` no navegador padrão | Não |
| 3 | **Bloquear** | Bloqueia a estação com `user32.LockWorkStation()` | Bloqueia sessão com `xflock4` ou `xdg-screensaver lock` | Não |
| 4 | **Aumentar volume** | Aumenta volume em +5% via `pycaw` (limitado rigidamente a 100%) | Aumenta volume em +5% via `wpctl`/`pactl`/`amixer` (teto 100%) | Não |
| 5 | **Diminuir volume** | Reduz volume em -5% via `pycaw` (piso delimitado em 0%) | Reduz volume em -5% via `wpctl`/`pactl`/`amixer` (piso 0%) | Não |
| 6 | **Silenciar áudio** | Muta o áudio (`mute = True`, estritamente idempotente, não inverte) | Muta o áudio (`mute 1`, estritamente idempotente, não inverte) | Não |
| 7 | **Status computador** | Coleta % CPU, % RAM e uptime via WMI / Win32 APIs | Coleta % CPU, % RAM e uptime via `/proc/stat`, `/proc/meminfo`, `/proc/uptime` | Não |
| 8 | **Abrir terminal** | Inicia `wt.exe` (Windows Terminal) ou `powershell.exe` na sessão gráfica | Inicia `xfce4-terminal` na sessão do usuário (`DISPLAY=:0`) | Não |
| 9 | **Fechar navegador** | Envia sinal normal de fechamento (`WM_CLOSE`) aos navegadores ativos | Encerra graciosamente via `wmctrl -c` navegadores com janela ativa | **Sim (2 etapas)** |
| 10 | **Reiniciar** | Executa reinicialização limpa via `shutdown.exe /r /t 5` | Executa reinicialização do sistema via `systemctl reboot` | **Sim (2 etapas)** |
| 11 | **Mostrar área de trabalho** | Minimiza janelas via COM `Shell.Application.MinimizeAll()` | Alterna área de trabalho via `wmctrl -k on` ou atalho Xfce | Não |

---

## 4. Frases e Gramática de Voz Reconhecidas

O catálogo suporta tanto o nome principal dos alvos (`fusion`, `ananuke`) quanto aliases fonéticos e numéricos mapeados para o Vosk:
- **Fusion:** `computador um`, `fusion`, `computador fusion`.
- **Ananuke:** `computador dois`, `ananuke`, `ana no que`.

### 4.1 Exemplos de Frases Suportadas
- *"Assistente ... abrir navegador no fusion"*
- *"Assistente ... abrir youtube no ananuke"*
- *"Assistente ... silenciar audio no computador um"*
- *"Assistente ... aumentar o volume no computador dois"*
- *"Assistente ... status do fusion"*
- *"Assistente ... mostrar area de trabalho no ana no que"*
- *"Assistente ... fechar navegador no fusion"* -> Jarvis solicita: *"Confirme fechar navegador em Fusion: diga confirmar ou cancelar."* -> Operador: *"Confirmar"*
- *"Assistente ... reiniciar computador ananuke"* -> Jarvis solicita: *"Confirme reinicialização de Ananuke: diga confirmar ou cancelar."* -> Operador: *"Confirmar"*

---

## 5. Guia de Instalação dos Agentes Locais

### 5.1 No Windows (Fusion - 192.168.0.128)

O agente deve rodar no contexto da **sessão interativa** do usuário logado (para poder interagir com a interface gráfica, abrir navegadores e controlar áudio):

1. **Pré-requisitos:**
   ```powershell
   pip install pycaw comtypes psutil
   ```
2. **Configuração de Variáveis (ou arquivo `.env` seguro):**
   ```powershell
   [System.Environment]::SetEnvironmentVariable('JARVIS_PC_AGENT_TOKEN', 'TOKEN_SEGURO_FUSION_MIN_16_CHARS', 'User')
   [System.Environment]::SetEnvironmentVariable('JARVIS_PC_ALLOWED_IPS', '192.168.0.126,127.0.0.1', 'User')
   ```
3. **Execução Automática no Logon (Inicializar ou Agendador de Tarefas):**
   Criar um atalho ou tarefa agendada configurada com:
   - *Executar apenas quando o usuário estiver conectado (sessão gráfica).*
   - *Diretório de trabalho:* `C:\Jarvis` (diretório raiz do projeto contendo o pacote `pc_control`)
   - *Programa:* `python.exe` (ou `pythonw.exe` para execução em segundo plano sem janela de console)
   - *Argumentos:* `-m pc_control.agents.windows_agent --port 8092 --allowed-ips 192.168.0.126,127.0.0.1`
   *(Nota: O agente também suporta execução direta como script através de resolução automática de `sys.path`).*

### 5.2 No Linux (Ananuke - Debian 13 / Xfce - 192.168.0.131)

O agente deve rodar como um serviço systemd no nível de usuário (`systemd --user`):

1. **Pré-requisitos:**
   ```bash
   sudo apt install -y wmctrl xdotool libnotify-bin
   ```
2. **Criação da Unidade do Usuário (`~/.config/systemd/user/jarvis-pc-agent.service`):**
   ```ini
   [Unit]
   Description=Jarvis PC Control Local Agent (Ananuke)
   After=graphical-session.target

   [Service]
   Type=simple
   WorkingDirectory=/opt/jarvis-pc-control
   Environment="JARVIS_PC_AGENT_TOKEN=TOKEN_SEGURO_ANANUKE_MIN_16_CHARS"
   Environment="JARVIS_PC_ALLOWED_IPS=192.168.0.126,127.0.0.1"
   Environment="DISPLAY=:0"
   ExecStart=/usr/bin/python3 -m pc_control.agents.linux_agent --port 8092 --allowed-ips 192.168.0.126,127.0.0.1
   Restart=always
   RestartSec=3

   [Install]
   WantedBy=default.target
   ```
3. **Habilitar e Iniciar:**
   ```bash
   systemctl --user daemon-reload
   systemctl --user enable --now jarvis-pc-agent.service
   ```

---

## 6. Configuração no Controlador Central (LXC 110)

Para configurar o recurso no controlador central, ajuste as variáveis de ambiente no serviço `jarvis-voice.service`:

```ini
[Service]
# Feature flag geral (Padrão de segurança: 0 = desativado até autorização expressa)
Environment="JARVIS_PC_CONTROL_ENABLED=0"

# Credenciais fortes individuais por computador (mínimo 16 caracteres, recomendado):
Environment="JARVIS_PC_FUSION_TOKEN=TOKEN_SEGURO_FUSION_MIN_16_CHARS"
Environment="JARVIS_PC_ANANUKE_TOKEN=TOKEN_SEGURO_ANANUKE_MIN_16_CHARS"
# Ou credencial única compartilhada (fallback suportado):
# Environment="JARVIS_PC_AGENT_TOKEN=TOKEN_SEGURO_COMPARTILHADO_MIN_16_CHARS"

# Endpoints e timeouts dos agentes (modo HTTP para desenvolvimento/testes locais):
Environment="JARVIS_PC_FUSION_URL=http://192.168.0.128:8092"
Environment="JARVIS_PC_ANANUKE_URL=http://192.168.0.131:8092"
Environment="JARVIS_PC_HTTP_TIMEOUT=2.5"

# Configuração para futura produção com HTTPS validado (sem bypass):
# Para ambiente de produção com terminação TLS nos agentes:
# Environment="JARVIS_PC_FUSION_URL=https://192.168.0.128:8092"
# Environment="JARVIS_PC_ANANUKE_URL=https://192.168.0.131:8092"
# Environment="JARVIS_PC_CA_CERT=/etc/ssl/certs/jarvis-ca.pem"
# NOTA: Em produção, o uso de JARVIS_PC_TLS_INSECURE_SKIP_VERIFY é proibido;
# todos os certificados devem ser estritamente validados contra a autoridade certificadora (CA).
```

---

## 7. Procedimento de Rollback e Segurança

Se qualquer anomalia for observada durante testes ou operação:
1. **Desativação Rápida:**
   Definir `JARVIS_PC_CONTROL_ENABLED=0` na configuração do serviço `jarvis-voice.service` e aplicar reiniciando o serviço:
   ```bash
   systemctl restart jarvis-voice.service
   ```
   *Atenção operacional:* O comando `systemctl restart jarvis-voice.service` causa uma breve interrupção transitória (~2 a 4 segundos) no reconhecimento de voz enquanto o modelo Vosk e os fluxos de áudio são recarregados na memória. Portanto, a ação deve ser executada ciente dessa breve pausa operacional.
   Com a flag em `0` (`JARVIS_PC_CONTROL_ENABLED=0`), o controlador ignora imediatamente todas as chamadas de PC Control (fail-closed), preservando 100% intactas as funcionalidades energéticas de Wake-on-LAN e desligamento via SSH.
2. **Parada dos Agentes Locais:**
   - No Ananuke (Debian 13): `systemctl --user stop jarvis-pc-agent.service`
   - No Fusion (Windows): encerrar o processo `python.exe` / `pythonw.exe` associado à porta 8092.

