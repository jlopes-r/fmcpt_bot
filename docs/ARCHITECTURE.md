# Arquitetura do pipeline social

O `super_bot.py` e o adaptador do Telegram: valida chat/usuario, aplica as
politicas de uso e entrega a solicitacao ao pipeline social. Ele nao implementa
extracao, download ou montagem de albuns.

## Fluxo

1. O adaptador cria um job `queued` no SQLite; a chave por chat e URL impede
   duas execucoes simultaneas da mesma solicitacao.
2. Ao obter uma vaga no semaforo, o worker reivindica o job como `downloading`
   e mantem um heartbeat ate o fim do processamento.
3. `ExtractorRegistry` escolhe exatamente um extrator para a URL.
4. O extrator devolve um `MediaBundle` ordenado com itens `MediaItem`.
5. `SocialMediaPipeline` traduz a legenda e muda o job para `uploading` antes
   de entregar o pacote ao `MediaSender`.
6. `MediaSender` baixa fontes remotas, valida, converte e envia ao Telegram.
7. O adaptador finaliza o job como `completed` ou `failed` e apresenta erros
   especificos ao usuario.

Na inicializacao, leases deixados por um processo interrompido voltam para
`queued`. O bot recupera a mensagem original pelo `chat_id`/`message_id` e
agenda novamente o trabalho. O rate limit tambem usa uma janela SQLite, por
isso reiniciar o processo nao zera o limite.

## Limites dos modulos

- `extractors/`: entende URLs e metadados de cada rede; nao envia ao Telegram.
- `services/download_manager.py`: aplica politicas e isola o `yt-dlp`.
- `services/media_sender.py`: prepara arquivos e conhece os limites de upload.
- `services/job_runtime.py`: adapta fila, heartbeat, recuperacao e rate limit
  persistentes ao event loop sem bloquear o Telegram.
- `handlers/social.py`: compoe registro, extratores, downloader e sender.
- `handlers/twitter.py`: comportamento adicional de tweets citados.
- `super_bot.py`: comandos, perfis, moderacao e adaptacao ao Telegram.

`ExtractionContext` transporta apenas dados de uma solicitacao, como status,
cancelamento, limite de duracao e tamanho de playlist. Assim os extratores nao
dependem de variaveis globais do bot.

## Adicionando uma rede

1. Implemente `SocialExtractor.supports()` e `extract()`.
2. Converta a resposta para `MediaBundle`, mantendo o indice original.
3. Registre o extrator em `build_default_registry()` antes do extrator generico.
4. Adicione testes de URL, identidade, ordem das midias e fallback.

Perfis sociais ficam fora do registro porque produzem cards/resumos, nao um
pacote de midia de uma publicacao.
