# Configuração de Cookies do Instagram

O Instagram agora exige autenticação para baixar conteúdo. Para o bot funcionar com Instagram, você precisa adicionar cookies do seu navegador.

## Por que cookies?

O Instagram mudou a API e bloqueia downloads sem autenticação. O yt-dlp usa cookies para simular um navegador logado.

**Atenção:** Use uma conta secundária se possível. O Instagram pode detectar uso automatizado.

## Como exportar cookies

### Método 1: Extensão do navegador (Recomendado)

1. Instale a extensão **Get cookies.txt LOCALLY** no seu navegador:
   - Chrome: [Get cookies.txt LOCALLY](https://chrome.google.com/webstore/detail/get-cookiestxt-locally/cclelndahbckbenkjhflpdbgdldlbecc)
   - Firefox: [cookies.txt](https://addons.mozilla.org/en-US/firefox/addon/cookies-txt/)

2. Acesse [instagram.com](https://www.instagram.com) e faça login

3. Clique na extensão e exporte os cookies

4. Salve o arquivo como `instagram_cookies.txt`

5. Coloque o arquivo em: `D:\Juan\bot\data\instagram_cookies.txt`

### Método 2: Via yt-dlp (se tiver acesso ao navegador)

```bash
yt-dlp --cookies-from-browser chrome --cookies data/instagram_cookies.txt
```

Isso extrai os cookies do Chrome diretamente.

## Validade dos cookies

- Cookies duram **~30 dias** (depende do Instagram)
- Quando parar de funcionar, exporte novamente
- Não há risco de ban se você não usar login/senha direto no bot

## Estrutura do arquivo

O arquivo deve estar no formato Mozilla/Netscape:

```
# HTTP Cookie File
.instagram.com	TRUE	/	FALSE	0	sessionid	SEU_SESSION_ID_AQUI
.instagram.com	TRUE	/	FALSE	0	csrftoken	SEU_CSRF_TOKEN_AQUI
...
```

## Verificação

Quando o bot iniciar, ele mostrará:

```
✅ Cookies do Instagram encontrados. Download autenticado ativado.
```

Se não encontrar:

```
⚠️ Cookies do Instagram NAO encontrados. Veja COOKIES_SETUP.md
```

Neste caso, o bot tentará o fallback via embed endpoint (só funciona para posts públicos).

## Segurança

- O arquivo `.txt` contém cookies de TODOS os sites do seu navegador
- Mantenha em local seguro, não compartilhe
- O `.gitignore` já ignora `data/*.txt` para não subir acidentalmente
