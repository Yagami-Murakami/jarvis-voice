# Jarvis Voice Display — Documentação Técnica e Operacional

Central visual inteligente integrada ao ecossistema **Jarvis Voice**, dedicada à execução em smartphone Android legado (**LG-X230DSV**) como terminal de cabeceira / bancada.

---

## 1. Visão Geral da Arquitetura

O sistema opera de forma desacoplada do motor de reconhecimento de voz e do painel `ligar.casa`. O Jarvis Display atua exclusivamente como **camada de apresentação em tempo real**, lendo eventos emitidos no log do systemd e retransmitindo-os instantaneamente via Server-Sent Events (SSE).

```text
       +---------------------------------------------+
       |             MICROFONE USB ME6S              |
       +---------------------------------------------+
                              |
                              v (ALSA / arecord 16kHz)
+-----------------------------------------------------------+
|                   CONTAINER PROXMOX LXC 110               |
|                                                           |
|  [jarvis-voice.service]                                   |
|   /opt/jarvis-voice/jarvis-operacional.py                 |
|   (Vosk offline + WoL + SSH seguro)                       |
|          |                                                |
|          v (stdout -> systemd journal)                    |
|                                                           |
|  [jarvis-display.service]                                 |
|   /opt/jarvis-voice/display/server.py                     |
|   - Monitor de eventos somente-leitura (journalctl -f)    |
|   - Servidor HTTP e SSE (Python Standard Library)         |
|   - Cache meteorológico Open-Meteo (20 min)               |
+-----------------------------------------------------------+
                              |
                              v (Wi-Fi 192.168.0.0/24 : 8080)
+-----------------------------------------------------------+
|                TERMINAL ANDROID (LG-X230DSV)              |
|                                                           |
|  [Jarvis Voice Display Kiosk App] (com.jarvis.display)    |
|   - Launcher padrão do sistema (substitui Pegasus)        |
|   - Imersão total em tela cheia (sem barras de sistema)   |
|   - Tela sempre ligada (FLAG_KEEP_SCREEN_ON)              |
|   - Prevenção de burn-in por Pixel Shifting               |
|   - Interface Sci-Fi Dark (60 FPS contínuos)              |
|   - Síntese de áudio local Web Audio API                  |
+-----------------------------------------------------------+
```

---

## 2. Diagnóstico de Hardware: LG-X230DSV

| Parâmetro | Valor Identificado | Observações |
| :--- | :--- | :--- |
| **Modelo** | LG-X230 (K4 2017 LTE / DSV) | MediaTek MT6737M (Quad-Core Cortex-A53) |
| **Arquitetura** | `armeabi-v7a` (32-bit ARM) | Compatível com WebView / Android 6 |
| **Versão Android** | 6.0 (Marshmallow) | API Level 23 |
| **Resolução de Tela** | 480 x 854 pixels | Densidade física 240 dpi, override 160 dpi |
| **Memória RAM** | ~1 GB (956 MB total) | ~516 MB disponíveis em repouso |
| **Armazenamento** | 3.9 GB em `/data` | 1.3 GB livres |
| **Motor Web** | Chromium 55 / 106 | Suporte nativo a HTML5, CSS3 e Web Audio |
| **Launcher Anterior** | Pegasus Frontend | `org.pegasus_frontend.android` |
| **Launcher de Sistema** | `com.android.launcher3` | Preservado em `/system/priv-app` |

---

## 3. Componentes Implementados

### 3.1 Servidor (`display/server.py`)
- **Autossuficiente:** desenvolvido utilizando a biblioteca padrão do Python (`http.server`, `threading`, `queue`, `urllib`). Não requer instalação de pacotes externos no container ou host.
- **Eventos em Tempo Real:** stream SSE em `/api/events` com comentários de *heartbeat* periódicos (15s) para evitar encerramento de conexão por inatividade.
- **Resiliência e Fallback:** endpoint de polling `/api/status` e `/api/weather` para navegadores que não sustentam conexões SSE contínuas.
- **Monitor Passivo:** lê linhas do journal sem travar o processo principal:
  - `ATIVADO! Aguardando comando.` -> Estado de escuta com animação ativa.
  - `Ouvi: ligar fusion` -> Estado de processamento e identificação do host.
  - `Pacote Wake-on-LAN enviado.` -> Confirmação de inicialização.
  - `Confirme desligamento de [host]...` -> Estado de confirmação com destaque amarelo.
  - `Cancelado.` / `Tempo esgotado.` -> Finalização com retorno ao repouso.
- **Modo Demonstração (`--demo`):** ativa o simulador local sem depender do microfone ou serviços de produção.

### 3.2 Frontend (`display/static/`)
- **Identidade Visual:** estética HUD / Arc Reactor, paleta azul-marinho profundo (`#050b14`), ciano elétrico (`#00e5ff`) e acentos de estado (verde `#00ffaa`, âmbar `#ffb703`, carmim `#ff3366`).
- **Relógio e Clima:** relógio digital com precisão ao segundo, data completa em português brasileiro e previsão meteorológica pública (Open-Meteo) com coordenadas de Santa Cruz da Serra, Duque de Caxias, RJ.
- **Efeitos Sonoros:** sintetizador leve via Web Audio API com tons polifônicos agradáveis (ativação, sucesso, confirmação, cancelamento e erro).
- **Proteção de Painel (Burn-in Prevention):** translação cíclica em coordenadas submilimétricas (`translate3d`) a cada 60 segundos para evitar retenção de imagem em painéis LCD/OLED.

### 3.3 Aplicativo Kiosk Android (`display/android/`)
- **Pacote:** `com.jarvis.display` (apenas 29 KB).
- **Home Launcher:** configurado como aplicativo padrão de tela inicial do Android.
- **Imersão Sticky:** esconde as barras de navegação e de status do Android; restaura automaticamente se o usuário tocar nas bordas.
- **Reprodução Sonora Nativa (SoundManager):** síntese de áudio PCM direta em memória via `AudioTrack` (16-bit Mono @ 44.1 kHz). Contorna 100% as restrições de autoplay e suspensão de áudio do Android WebView, permitindo reprodução instantânea pelo alto-falante físico do aparelho mesmo logo após a inicialização, sem requerer toque na tela.
- **Proteção Anti-Repetição (Debounce):** trava de 1,5s para evitar que múltiplos reconhecimentos concorrentes disparem o som repetidamente.
- **Comandos de Teste via ADB:**
  ```bash
  # Tocar som de ativação diretamente
  adb shell am broadcast -a com.jarvis.display.ACTION_PLAY_SOUND --es type wake

  # Testar ajuste de volume (0.0 a 1.0)
  adb shell am broadcast -a com.jarvis.display.ACTION_PLAY_SOUND --ef volume 0.85

  # Silenciar ou reativar áudio
  adb shell am broadcast -a com.jarvis.display.ACTION_PLAY_SOUND --ez mute true
  ```
- **Configuração Fácil:** um toque longo de 2 segundos na tela abre o diálogo para alterar a URL do servidor sem precisar conectar o celular ao computador.


---

## 4. Procedimento de Instalação no Container LXC 110

> [!IMPORTANT]
> A instalação deve ser realizada **sem interromper** o serviço `jarvis-voice.service`.

### Passo 1: Copiar arquivos para o container
No computador de desenvolvimento (Ananuke):
```bash
# Copia os arquivos do display para o container LXC 110
scp -r display/ root@<IP_LXC_110>:/opt/jarvis-voice/display/
scp systemd/jarvis-display.service root@<IP_LXC_110>:/etc/systemd/system/
```

### Passo 2: Ajustar permissões e iniciar o serviço
Acessando o terminal do container LXC:
```bash
# Recarrega a lista de serviços do systemd
systemctl daemon-reload

# Habilita a inicialização automática no boot
systemctl enable jarvis-display.service

# Inicia o servidor visual independente
systemctl start jarvis-display.service

# Verifica o status
systemctl status jarvis-display.service
```

### Passo 3: Testar acesso local
No navegador ou via terminal:
```bash
curl -s http://<IP_LXC_110>:8080/api/status
```

---

## 5. Gerenciamento do Dispositivo Android (LG-X230)

### 5.1 Procedimento de Restauração do Pegasus (Reversibilidade)
O APK do Pegasus foi salvo em:
`backups/pegasus_frontend.apk` (SHA-256: `f5d5bcbfc1f99e8ec790c388722de22961fb30495cc69696c7617fb6c7bb729e`)

Para restaurar o Pegasus a qualquer momento:
```bash
adb install -r backups/pegasus_frontend.apk
adb shell am start -a android.settings.HOME_SETTINGS
# Selecione o Pegasus na tela inicial
```

### 5.2 Compilar e Atualizar o APK Jarvis Display
Para recompilar o aplicativo Android com alterações:
```bash
cd display/android
./build_apk.sh
adb install -r bin/JarvisDisplay.apk
```

---

## 6. Checklist de Validação Realizada

- [x] **ADB e Hardware:** conexão estabelecida, depuração USB autorizada, tela 480x854 e 1GB de RAM diagnosticados.
- [x] **Backup do Pegasus:** extração e verificação de integridade do APK original.
- [x] **Remoção Segura do Pegasus:** desinstalação controlada mantendo dados (`-k`), mantendo `Launcher3` intacto.
- [x] **Registro de Home Launcher:** `com.jarvis.display` definido como Home padrão permanente via Android Settings.
- [x] **Desempenho da Interface:** renderização estável a **60 FPS** no SurfaceFlinger do dispositivo.
- [x] **Relógio e Clima:** sincronizados em tempo real com Open-Meteo para Santa Cruz da Serra, Duque de Caxias.
- [x] **Respostas Visuais a Comandos:** validação de "Assistente", "Ligar Fusion", "Desligar Ananuke", "Confirmar", "Cancelar" e "Tempo Esgotado".
- [x] **Resiliência e Reconexão:** stream SSE com recuperação transparente de falhas e fallback para polling.
- [x] **Preservação Operacional:** nenhum script ou serviço de produção existente foi alterado ou reiniciado.
