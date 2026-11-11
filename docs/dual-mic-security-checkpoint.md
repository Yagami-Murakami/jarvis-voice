# Microfone duplo — checkpoint de segurança (WIP, NÃO IMPLANTAR)

**Branch:** `feat/dual-mic-secure-integration` — issue #3.  
**Status:** alterações preparatórias no código, **sem execução de testes locais nem deploy**.  
**Base:** `feat/dual-microphone-lg` (homologação em simulação).

## O que já mudou nesta branch

1. `gateway/audio_gateway.py`: removeu token fixo de demonstração; requer `JARVIS_AUDIO_TOKEN` de 32 a 256 caracteres na inicialização e rejeita token público antigo; compara bytes com `hmac.compare_digest`; valida versão e formato PCM 16 kHz/mono/16 bit e quadros de 1280 bytes.
2. `POST /api/test/inject`: somente habilitado quando a variável `JARVIS_GATEWAY_TEST_MODE=1` **e** a conexão chega por loopback. Removido cabeçalho CORS irrestrito.
3. `systemd/jarvis-audio-gateway.service`: lê `EnvironmentFile=/etc/jarvis/audio-gateway.env` (deve ser local, fora do GitHub, permissão estrita).
4. `LgMicCapture.java`: elimina token fixo do APK, lê token salvo nas preferências do aplicativo e não transmite se estiver sem token válido.

## ALERTA: o que falta antes de qualquer teste real/deploy

- **O áudio ainda trafega em TCP sem criptografia.** Isso não é seguro para um serviço operacional. Implementar TLS com certificado verificado/pinning no Android 6, servidor TLS no gateway e proteção adequada contra replay/unauthorized sources. Não assumir que `JARVIS_AUDIO_TOKEN` sozinho resolve a privacidade.
- **Ainda não há UI de pareamento segura no LG** para provisionar/rotacionar credencial; SharedPreferences simples não garante criptografia. O agente deve implementar armazenamento mais seguro compatível com API 23, oferecer configuração local segura e não expor segredos em logs/ADB desnecessariamente.
- O endpoint GET de diagnóstico (porta 8089) ainda precisa restringir acesso e retirar dados sensíveis. **Não expor a internet**. Endpoint POST de teste permanece destinado exclusivamente a desenvolvimento local.
- Há mudanças na validação de quadros, e os testes antigos ainda podem precisar ser atualizados.
- O **token anterior foi público**: jamais reutilizá-lo. A rotação exige atualizar gateway e aparelho no mesmo procedimento.
- Os serviços de voz e painel em produção **não foram modificados** nesta branch. Não copiar esses arquivos diretamente para o LXC sem concluir as fases de segurança e teste, pois a nova versão falha fechada sem pareamento.

## Roteiro para o agente no Ananuke

1. Ler issue #3; fazer pull da branch, analisar diff contra `feat/dual-microphone-lg`.
2. Criar testes em bancada para token ausente/inválido, quadros inválidos, API de injeção bloqueada, compatibilidade do protocolo, TLS e reconexão.
3. Implementar credenciais individuais fora do repositório, transporte criptografado e configuração privada no Android; nada de token fixo em código, APK, comando de linha, logs ou screenshots.
4. Ajustar teste de latência e CPU do LG e LXC e proteger consulta de status.
5. Realizar teste de duas fontes simultâneas somente em **simulação**, sem WoL/SSH. Confirmar uma única intenção por sessão (dedup precisa ocorrer na **execução final**, não somente no wake).
6. Propor contrato IPC interno com idempotência, expiração e confirmação para futura integração de ações reais.
7. Preparar backup/reversão e PR para revisão. **Não mudar** `jarvis-operacional.py`, `jarvis-voice.service`, recuperação do ME6S, `ligar.casa`, `jarvis-display.service`, nem redes/UFw sem autorização específica.

## Pré-validações mínimas de revisão

```bash
python3 -m compileall -q gateway
python3 -m unittest discover -s gateway/tests -v
grep -R 'jarvis-dual-mic-2026' gateway display/android/src  # Deve aparecer no máximo como valor explicitamente rejeitado
```

As verificações acima **ainda não foram executadas** nesta sessão e precisam ser executadas pelo agente em ambiente de desenvolvimento.
