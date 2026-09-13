# Arquitetura do pipeline social

O `super_bot.py` e o adaptador do Telegram: valida chat/usuario, aplica as
politicas de uso e entrega a solicitacao ao pipeline social. Ele nao implementa
extracao, download ou montagem de albuns.

## Fluxo

1. `ExtractorRegistry` escolhe exatamente um extrator para a URL.
2. O extrator devolve um `MediaBundle` ordenado com itens `MediaItem`.
3. `SocialMediaPipeline` traduz a legenda e entrega o pacote ao `MediaSender`.
4. `MediaSender` baixa fontes remotas, valida, converte e envia ao Telegram.
5. O entrypoint registra o resultado e apresenta erros especificos ao usuario.

## Limites dos modulos

- `extractors/`: entende URLs e metadados de cada rede; nao envia ao Telegram.
- `services/download_manager.py`: aplica politicas e isola o `yt-dlp`.
- `services/media_sender.py`: prepara arquivos e conhece os limites de upload.
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
