# Jarvis Voice Mobile — Documentação Técnica & Guia Operacional

**Projeto:** Jarvis Voice Dual-Mic & PC Control V1  
**Módulo:** `mobile/` — Jarvis Voice Mobile (APK Android Nativo em Kotlin)  
**Branch de Trabalho:** `feat/jarvis-voice-mobile`  
**Versão:** 1.0.0  
**Ambiente:** Debian 13 (Desenvolvimento) / Proxmox LXC 110 (`192.168.0.126`, Produção)  

---

## 1. Visão Geral

O **Jarvis Voice Mobile** é um aplicativo Android nativo desenvolvido em **Kotlin** para transformar qualquer smartphone compatível conectado à rede local em um terminal inteligente de comando de voz para o ecossistema Jarvis.

O aplicativo opera como cliente seguro e desacoplado:
1. **Captura de Áudio em Tempo Real:** Captura áudio pelo microfone do smartphone em PCM Linear 16.000 Hz, 16 bits, Mono via `AudioRecord`.
2. **Streaming Autenticado via TCP/TLS:** Transmite blocos de áudio de 1.280 bytes (40 ms) diretamente para o `audio_gateway.py` (porta TCP 8088) utilizando o protocolo binário autenticado (`JMIC` / `DATA`).
3. **Reconhecimento Centralizado no Servidor:** Reutiliza o motor de reconhecimento **Vosk PT-BR** central no LXC 110, sem necessidade de carregar modelos pesados no smartphone ou consumir excessivamente a bateria do aparelho.
4. **Interface Gráfica Estilo Jarvis Display:** Tema escuro HUD/Arc Reactor (`#030712`, `#00e5ff`, `#0f172a`), com reator central animado, barra de RMS em tempo real, botão Push-to-Talk (PTT), modo de escuta por voz ("Assistente") em primeiro plano e cards dedicados para telemetria do Ananuke (CPU, RAM, Uptime) e feedback do último comando executado.
5. **Telemetria e Feedback via SSE:** Conecta-se ao endpoint `/api/events` do `display/server.py` via Server-Sent Events (SSE) para refletir instantaneamente confirmações pendentes, status de execução e métricas de máquinas.

---

## 2. Especificação dos Protocolos de Rede

### 2.1 Protocolo Binário de Streaming de Áudio (`audio_gateway.py`: TCP 8088)

O streaming é estabelecido via Socket TCP puro ou túnel criptografado TLS 1.2+ na porta 8088.

#### Handshake Versão 2 (Multi-Dispositivo)
Enviado imediatamente após a abertura do socket pelo terminal móvel:

| Campo | Tipo | Tamanho | Valor / Descrição |
| :--- | :--- | :--- | :--- |
| **Magic** | `bytes` | 4 bytes | `b"JMIC"` (`0x4A 0x4D 0x49 0x43`) |
| **Versão** | `uint8` | 1 byte | `2` (`0x02` para Handshake com device_id explícito) |
| **Device ID Length** | `uint8` | 1 byte | Comprimento $L_{dev}$ do identificador (1 a 64 bytes) |
| **Device ID** | `string` | $L_{dev}$ bytes | Identificador UTF-8 do aparelho (ex: `pixel_7_sala`) |
| **Token Length** | `uint16` | 2 bytes (Big Endian) | Comprimento $L_{tok}$ do token de pareamento (32 a 256 bytes) |
| **Token Bytes** | `string` | $L_{tok}$ bytes | Token individual seguro do smartphone |
| **Sample Rate** | `uint32` | 4 bytes (Big Endian) | `16000` (16 kHz) |
| **Channels** | `uint8` | 1 byte | `1` (Mono) |
| **Bit Depth** | `uint8` | 1 byte | `16` (16 bits PCM) |

*Nota de Retrocompatibilidade:* O servidor mantém suporte integral à **Versão 1** (utilizada pelo smartphone legado LG-X230DSV), onde o campo de versão é `1` e o `token_bytes` é enviado imediatamente após o tamanho do token, sem o campo de `device_id`.

#### Estrutura dos Frames de Áudio (`DATA`)
Após o handshake, o cliente transmite blocos periódicos de áudio a cada 40 ms:

| Campo | Tipo | Tamanho | Valor / Descrição |
| :--- | :--- | :--- | :--- |
| **Magic** | `bytes` | 4 bytes | `b"DATA"` (`0x44 0x41 0x54 0x41`) |
| **Sequence Number** | `uint32` | 4 bytes (Big Endian) | Contador sequencial crescente (inicia em 0) |
| **Timestamp** | `uint64` | 8 bytes (Big Endian) | Milissegundos (`SystemClock.elapsedRealtime()`) |
| **Flags** | `uint8` | 1 byte | `0x00` (Normal) ou `0x01` (Echo Guard ativo / Mudo) |
| **Payload Length** | `uint16` | 2 bytes (Big Endian) | `1280` (exatamente 640 amostras de 16 bits) |
| **Payload** | `bytes` | 1280 bytes | Amostras brutas de áudio PCM Mono Little-Endian |

---

### 2.2 Protocolo de Eventos e Telemetria (`display/server.py`: HTTP 8080)

O smartphone estabelece conexão persistente HTTP Server-Sent Events (SSE) em `http://<server>:8080/api/events`.

#### Formato do Evento SSE:
```json
{
  "id": 42,
  "status": "success",
  "titulo": "JARVIS VOICE",
  "mensagem": "Ananuke: CPU 12% | RAM 45% | Ativo há 3h 25m.",
  "detalhe": "Jarvis PC Control V1",
  "alvo": "Ananuke",
  "acao": "status",
  "timestamp": 1794526173.45,
  "auto_reset_s": 6.0
}
```

O aplicativo executa parsing automático de expressões regulares para preencher os medidores visuais de CPU, RAM e Uptime do Ananuke sem necessitar de endpoints auxiliares.

---

## 3. Segurança e Pareamento

1. **Isolamento Estrito de Segredos:** O APK Android não embute tokens HMAC dos computadores (Fusion/Ananuke), chaves privadas SSH, senhas ou segredos mestres. O aplicativo armazena apenas o token de pareamento do terminal móvel em `PreferencesManager` privado.
2. **Transporte Criptografado (TLS 1.2+):** O tráfego de áudio suporta SSL/TLS com verificação de fingerprint SHA-256 do certificado do Gateway (`certFingerprint`), garantindo proteção contra ataques de *Man-in-the-Middle* na rede local.
3. **Autenticação Individual por Dispositivo:** No servidor, o `audio_gateway.py` aceita tokens individuais via variável `JARVIS_AUDIO_TOKENS` no formato JSON (`{"token_pixel": "pixel_7", "token_galaxy": "galaxy_quarto"}`), validando em tempo constante (`hmac.compare_digest`).
4. **Restrição à Rede Local:** O aplicativo valida a conexão apenas na sub-rede autorizada (`192.168.0.0/24`). Tentativas de comunicação externa fora da LAN são rejeitadas.

---

## 4. Instruções de Instalação e Pareamento

### 4.1 Pré-Requisitos no Smartphone
- Aparelho com Android 6.0 (API 23) ou superior (testado até Android 14 / API 34).
- Conexão Wi-Fi ativa na mesma rede local do Jarvis (`192.168.0.x`).
- Permissão de Microfone concedida ao aplicativo.

### 4.2 Instalação via ADB ou Arquivo Direto
O arquivo APK pronto para instalação está localizado em:
`mobile/bin/JarvisVoiceMobile-v1.0.0.apk`

Para instalar via cabo USB ou ADB Wi-Fi:
```bash
adb install -r mobile/bin/JarvisVoiceMobile-v1.0.0.apk
```
*Ou copie o arquivo `JarvisVoiceMobile-v1.0.0.apk` para a memória do celular e abra-o pelo gerenciador de arquivos (habilitando "Instalar apps desconhecidos").*

### 4.3 Procedimento de Pareamento
1. Abra o aplicativo **Jarvis Voice Mobile**.
2. Conceda a permissão de gravação de áudio quando solicitada.
3. Toque no ícone de **Configurações** (engrenagem no canto superior direito).
4. Configure os parâmetros de rede:
   - **IP do Servidor:** `192.168.0.126` (padrão)
   - **Porta de Áudio:** `8088` (padrão)
   - **Porta do Display:** `8080` (padrão)
   - **Device ID:** Nome amigável do celular (ex: `celular_sala`, `pixel7`)
   - **Token de Pareamento:** Token individual configurado no servidor
   - **TLS 1.2+:** Ative se o gateway estiver com certificados TLS habilitados
5. Toque em **Testar Conexão** para validar a acessibilidade das portas 8088 e 8080.
6. Toque em **Salvar Configurações**. O badge no topo mudará para verde (**Conectado**).

---

## 5. Proposta Técnica: Escuta Contínua em Segundo Plano (Fase Futura)

Conforme a diretriz do projeto, o MVP atual prioriza a **operação estável em primeiro plano** com botão Push-to-Talk (PTT) e ativação por "Assistente" com a tela ativa.

Para a futura homologação da escuta contínua em segundo plano, os seguintes desafios de arquitetura do Android devem ser tratados:

### 5.1 Restrições do Android Moderno (API 26+)
- **Background Execution Limits:** Desde o Android 8.0 (API 26), serviços em segundo plano são finalizados pelo sistema após poucos minutos para economizar recursos.
- **Background Microphone Access:** A partir do Android 9.0 (API 28), aplicativos em background são silenciados pelo sistema operacional ao tentar ler o microfone (`AudioRecord.read()` retorna silêncio/zeros).
- **Doze Mode e Standby de Bateria:** Dispositivos móveis entram em sono profundo quando a tela apaga, cortando conexões de rede e reduzindo a frequência de clock da CPU.

### 5.2 Arquitetura Proposta para a Versão 2.0
1. **Foreground Service com Notificação Persistente:**
   - Implementar `JarvisBackgroundService` como serviço em primeiro plano com tipo `android:foregroundServiceType="microphone"` (exigido no Android 10+ / 14).
   - Exibir notificação persistente na barra de status informando: *"Jarvis Voice ativo — Monitorando ativação por voz"*.
2. **Partial WakeLock Gerenciado:**
   - Adquirir `PowerManager.PARTIAL_WAKE_LOCK` durante períodos ativos de monitoramento para evitar que a CPU entre em suspensão profunda.
3. **VAD (Voice Activity Detection) Local Leve:**
   - Para evitar streaming contínuo de 24 horas que esgotaria a bateria em poucas horas e sobrecarregaria a rede Wi-Fi, utilizar um pré-classificador VAD leve no aparelho (ex: WebRTC VAD ou medição de energia por bandas).
   - O socket TCP de áudio com o servidor só transmite pacotes quando houver fala humana detectada.
4. **Isenção de Otimização de Bateria:**
   - Solicitar ao usuário a permissão `ACTION_REQUEST_IGNORE_BATTERY_OPTIMIZATIONS` para evitar o encerramento do processo pelo fabricante do aparelho (Xiaomi MIUI, Samsung OneUI, etc.).

---

## 6. Registro de Alterações no Backend (`audio_gateway.py`)

Para viabilizar o suporte a múltiplos smartphones em simultâneo com total estabilidade:
1. **Atendimento Multi-Thread Concorrente:** O método `accept()` do socket TCP agora delega cada nova conexão a uma thread dedicada (`AudioClient-<ip>:<port>`), liberando o loop principal para atender conexões adicionais imediatamente.
2. **Reconhecedor Vosk Dedicado por Stream:** Cada smartphone conectado instancia seu próprio `KaldiRecognizer(self.vosk_model, 16000)` a partir do modelo compartilhado em memória, impedindo que fluxos de áudio concorrentes se misturem no decodificador.
3. **Autenticação Multi-Token com `allowed_tokens`:** O `LgMicServer` agora suporta dicionário de tokens permitidos e a variável de ambiente `JARVIS_AUDIO_TOKENS`, associando cada token ao respectivo `device_id`.
4. **Deduplicação Inteligente no `SessionArbitrator`:** Preservada a janela de idempotência e deduplicação de comandos simultâneos, evitando execuções duplicadas caso dois celulares escutem o mesmo comando ao mesmo tempo no ambiente.

---

## 7. Registro de Validação Automatizada

| Componente | Testes Executados | Resultado |
| :--- | :--- | :--- |
| **`gateway/tests/test_audio_gateway.py`** | 16 testes unitários e de protocolo | **100% Aprovado** (OK) |
| **`gateway/tests/` (Suíte Completa)** | 46 testes de integração e IPC | **100% Aprovado** (OK) |
| **`pc_control/tests/`** | 119 testes de agentes e comandos | **100% Aprovado** (OK) |
| **`display/tests/`** | 8 testes de display e eventos | **100% Aprovado** (OK) |
| **`scripts/test_mobile_gateway_integration.py`** | 5 testes de integração mobile multi-cliente | **100% Aprovado** (OK) |
| **Build do APK Android (`mobile/build_apk.sh`)** | Compilação Kotlin, DEX, AAPT2, Apksigner | **100% Aprovado** (APK 6.2 MB) |
