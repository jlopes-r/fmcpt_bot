# Operacao e recuperacao

Este projeto nao depende de GitHub Actions. A atualizacao e iniciada manualmente
na VM e o deploy valida uma release isolada antes de trocar o processo ativo.

## Atualizacao normal

Conecte-se a VM e execute o alias instalado para o usuario de operacao:

```bash
gcloud compute ssh super-bot-gratis --zone=us-west1-a
atualizar_bot
```

O fluxo executa, nesta ordem:

1. busca `origin/main` sem interromper o bot atual;
2. cria ou reutiliza um ambiente virtual especifico para o commit;
3. verifica dependencias, compila o codigo e executa os testes;
4. avanca o repositorio somente por fast-forward;
5. troca o ambiente ativo e reinicia o servico;
6. valida processo, SQLite e conexao com o Telegram;
7. mantem apenas os dois ambientes mais recentes para rollback.

A tela mostra apenas as etapas, o resumo dos testes e o resultado do health
check. A saida completa fica em:

```text
/home/juanl/fmcpt_bot/data/logs/deploy/update-AAAAMMDDTHHMMSSZ-PID.log
```

Sao preservados os dez logs mais recentes. Esse limite pode ser alterado apenas
para uma execucao:

```bash
DEPLOY_LOG_KEEP=5 atualizar_bot
```

Nao use `--skip-tests` ou `--skip-dependencies` na rotina normal. Essas opcoes
existem para diagnostico consciente e reduzem as garantias do deploy.

## Confirmacao de saude

```bash
systemctl show superbot.service \
  --property=ActiveState,SubState,MainPID,ActiveEnterTimestamp --no-pager

cat /home/juanl/fmcpt_bot/.deploy/last-successful
readlink -f /home/juanl/fmcpt_bot/.deploy/current-venv

journalctl -u superbot.service --since "15 minutes ago" --no-pager
```

O relatorio persistente do pipeline ajuda a separar uma falha de plataforma de
um problema geral do bot:

```bash
cd /home/juanl/fmcpt_bot
.deploy/current-venv/bin/python scripts/observability_report.py --hours 24
```

## Rollback automatico

Depois que o codigo e o ambiente sao trocados, qualquer falha no health check
aciona automaticamente a restauracao do commit, do ambiente virtual, do
override do systemd e do changelog anteriores. O servico antigo e iniciado e
validado novamente.

Se o deploy falhar antes da troca, o processo que ja estava no ar permanece
intacto. Consulte o log detalhado indicado por `atualizar_bot`, corrija a causa
e execute a atualizacao novamente.

## Recuperacao depois de uma falha tardia

Uma falha descoberta depois de um deploy considerado saudavel deve ser
corrigida com historico preservado:

1. reverta ou corrija o commit na maquina de desenvolvimento;
2. execute `python tools/check_quality.py`;
3. envie o novo commit para `main`;
4. execute `atualizar_bot` novamente.

Evite `git reset --hard` e nao altere manualmente o link
`.deploy/current-venv`. Codigo e ambiente precisam pertencer ao mesmo commit;
trocar apenas um deles pode produzir uma recuperacao aparentemente saudavel,
mas inconsistente.

Se o servico parou fora de um deploy, primeiro preserve os logs e tente um
reinicio simples:

```bash
journalctl -u superbot.service --since "30 minutes ago" --no-pager
sudo systemctl restart superbot.service
systemctl is-active superbot.service
```

A fila e os limites de uso ficam no SQLite. Ao iniciar, o bot devolve jobs com
lease interrompido para a fila e tenta recuperar a mensagem original; reiniciar
o servico nao apaga esses estados.

## Espaco em disco

```bash
du -sh /home/juanl/fmcpt_bot/.deploy/venvs
du -sh /home/juanl/.cache/pip
df -h /home/juanl/fmcpt_bot
```

O deploy remove ambientes antigos somente depois de uma release saudavel e
nunca remove o ambiente ativo nem o imediatamente anterior. O cache padrao do
pip e compartilhado entre releases e deve ser preservado, pois evita novos
downloads e reduz o tempo de atualizacao.

## Testes reais das redes

Os contratos reais nao sao instalados nem ativados automaticamente pelo deploy.
Para uma verificacao manual e pontual, sem notificacao:

```bash
cd /home/juanl/fmcpt_bot
.deploy/current-venv/bin/python scripts/check_social_contracts.py \
  --require-config --no-notify --target instagram_reel --timeout 60
```

Em VM pequena, execute um alvo por vez e evite repeticoes proximas para nao
pressionar CPU, memoria ou limites das plataformas.
