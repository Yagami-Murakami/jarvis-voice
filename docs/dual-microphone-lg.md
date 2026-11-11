# Jarvis Voice — Microfone duplo (ME6S + LG-X230DSV)

**Estado:** especificação de implementação; **não implantado**.  
**Tarefa:** [Issue #2](https://github.com/Yagami-Murakami/jarvis-voice/issues/2).  
**Base:** branch `feat/jarvis-voice-display`.

## Objetivo

Ampliar a **cobertura** de voz combinando duas fontes físicas de captação, sem somar diretamente amostras dos microfones:

- **ME6S USB**: captação no Proxmox/LXC 110 via ALSA; serviço atual continua sendo a referência de produção.
- **LG-X230DSV**: captação com API nativa `AudioRecord` (Android 6.0/API 23), transporte via Wi-Fi local até um gateway separado.
- **Árbitro**: selecionar uma única sessão de comando ativa, de maneira que uma fala recebida por ambas as fontes jamais gere duas execuções.

Não fazer alterações na voz operacional, no `ligar.casa` ou na interface visual existente durante a fase inicial.

## Diagrama lógico

```text
ME6S -- ALSA ----> Jarvis Voice (produção, inalterado) --> Wake-on-LAN / SSH
                       |
                       v (somente leitura)
                 Jarvis Display (tela)

LG -- AudioRecord --> gateway LAN autenticado --> diagnóstico do áudio
                                      |
                                      v
                               Vosk de teste
                                      |
                                      v
                           árbitro em SIMULAÇÃO
                                      |
                                      v
                           painel de diagnóstico

FASE FUTURA (após revisão):
árbitro --> API interna do controlador (somente uma ação por sessão,
             confirmação de desligamento preservada)
```

## Etapa 1 — Capturar e transmitir sem controlar computadores

1. Adicionar ao manifesto `RECORD_AUDIO` e obter consentimento em tempo de execução (API 23).
2. Em classe separada do `SoundManager`, implementar captura Java com `AudioRecord`, PCM 16-bit mono e frequência negociada (tentar 16 kHz; validar no hardware).
3. Exibir claramente `Microfone LG: ativo/desligado/sem permissão`, com botão de ativação, sem gravação persistente por padrão.
4. Capturar em thread separada, com filas limitadas; em congestionamento descartar blocos antigos em vez de aumentar o atraso.
5. Transportar por conexão autenticada na LAN (preferir TLS com certificado verificado). Não abrir endpoint de energia, não incorporar segredo no APK/repositório, nem alterar o firewall automaticamente.
6. No LXC, criar **serviço separado**, com bind e porta configuráveis, que apenas registra métricas de áudio e estado. O `jarvis-display.service` não deve ser usado para aceitar comandos remotos.
7. Testar sem WoL/SSH, com relatórios de latência, RMS/ruído, taxa de perda, permissões e recursos no LG/LXC.

## Etapa 2 — Reconhecimento e arbitragem em modo de teste

- Reconhecimento independente por fonte; medir CPU e memória antes de carregar Vosk adicional em LXC de recursos limitados.
- Ao detectar `assistente`, criar sessão `session_id` e `source_id`, fixar a origem com melhor qualidade **por sessão**, e descartar transcrições duplicadas da segunda origem.
- Prioridade não deve ser presumida por distância; aferir sinal/ruído e atraso reais. Nunca mesclar PCM bruto das fontes.
- Uma sessão tem tempo máximo de escuta, expiração, cancelamento e diagnóstico de perda da origem.
- Desligamento é **sempre** simulado até que um ponto de integração autenticado preserve as confirmações de segurança do Jarvis operacional.
- Quando o LG emitir tons no `SoundManager`, aplicar `echo_guard` temporário na fonte LG e manter a escuta ME6S. O objetivo é evitar realimentação acústica.
- Reinício do app, perda de Wi-Fi e desconexão de um microfone não podem afetar o serviço de voz existente.

## Étapa 3 — Integração com ações reais (ainda não autorizada para deployment)

O código público `jarvis_voice.py` é de referência e não corresponde necessariamente a `/opt/jarvis-voice/jarvis-operacional.py`. Inspecionar esse script somente em ambiente autorizado, obter diff/backup, e propor um protocolo local de intenção autenticada que impeça repetição e preserve **confirmação explícita de desligamento**.

**Não** acionar a lógica de energia por `subprocess` montado a partir de transcrições, nem expor comandos de energia via HTTP aberto.

## Critérios de validação

| Cenário | Esperado |
|---|---|
| ME6S sozinho | Funcionalidade atual intacta |
| LG sozinho | Áudio chega ao gateway e é reconhecido em simulação |
| Ambos ouvindo a mesma fala | Uma só sessão de intenção |
| LG sem permissão RECORD_AUDIO | Interface indica permissão ausente; ME6S permanece normal |
| Wi-Fi do LG desligado | Gateway indica desconexão e se recupera ao voltar |
| Som de ativação no LG | Não desencadeia nova escuta na própria fonte LG |
| LXC reinicia gateway | Serviço `jarvis-voice.service` não é afetado |
| Comando de desligamento | Nenhuma execução real em modo de teste |

## Evidências exigidas do agente no Ananuke

- APK construído e testado via ADB no LG real, com captura de tela do indicador do microfone.
- Logs sanitizados de conexão, sample rate, RMS/latência e reconexão.
- Testes unitários para validação de pacotes, autenticação, filas, expiração, deduplicação e simulador.
- Medições de CPU e memória do gateway, da captura Android e do Vosk adicional.
- Relatório indicando **o que foi implementado e o que ainda não foi colocado em produção**.

**Não usar o root do celular para captura:** `AudioRecord` é a API adequada. Root e ADB são ferramentas de diagnóstico, não pré-requisitos do app.
