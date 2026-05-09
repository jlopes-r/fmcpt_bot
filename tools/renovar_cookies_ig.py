#!/usr/bin/env python3
"""
Ferramenta para renovar cookies do Instagram para o bot.

Uso:
    python renovar_cookies_ig.py

Vai pedir usuário e senha da conta do Instagram dedicada ao bot,
fazer login via Instaloader, e salvar os cookies no formato
Netscape (compatível com yt-dlp e o bot).
"""
import os
import sys
import http.cookiejar
import getpass

# Adiciona o raiz do projeto ao path
RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
COOKIE_PATH = os.path.join(RAIZ, "data", "instagram_cookies.txt")

try:
    import instaloader
except ImportError:
    print("❌ Instaloader não instalado. Rode: pip install instaloader")
    sys.exit(1)


def verificar_cookies_atuais():
    """Verifica se os cookies atuais estão válidos."""
    if not os.path.exists(COOKIE_PATH):
        print(f"⚠️  Arquivo de cookies não encontrado: {COOKIE_PATH}")
        return False

    try:
        cj = http.cookiejar.MozillaCookieJar(COOKIE_PATH)
        cj.load(ignore_discard=True, ignore_expires=True)

        cookies_encontrados = {}
        for cookie in cj:
            cookies_encontrados[cookie.name] = cookie.value[:20] + "..." if len(cookie.value) > 20 else cookie.value

        print("\n📋 Cookies atuais:")
        cookies_essenciais = ["sessionid", "ds_user_id", "csrftoken", "mid", "ig_did"]
        for nome in cookies_essenciais:
            if nome in cookies_encontrados:
                print(f"  ✅ {nome}: {cookies_encontrados[nome]}")
            else:
                print(f"  ❌ {nome}: AUSENTE")

        if "sessionid" not in cookies_encontrados:
            print("\n⚠️  PROBLEMA: 'sessionid' está AUSENTE!")
            print("   Sem esse cookie, o Instagram trata todas as requisições como não-autenticadas.")
            print("   Isso causa o erro '403 Forbidden' na API GraphQL.\n")
            return False

        print("\n✅ Cookies parecem completos!")
        return True

    except Exception as e:
        print(f"❌ Erro ao ler cookies: {e}")
        return False


def renovar_via_instaloader():
    """Faz login via Instaloader e exporta cookies."""
    print("\n" + "=" * 50)
    print("🔐 Login no Instagram via Instaloader")
    print("=" * 50)

    username = input("\n👤 Usuário Instagram: ").strip()
    if not username:
        print("❌ Usuário não pode ser vazio.")
        return False

    password = getpass.getpass("🔑 Senha: ")
    if not password:
        print("❌ Senha não pode ser vazia.")
        return False

    L = instaloader.Instaloader()

    try:
        print(f"\n⏳ Fazendo login como '{username}'...")
        L.login(username, password)
        print("✅ Login bem-sucedido!")

        # Extrai cookies da sessão do Instaloader
        session = L.context._session

        # Salva no formato Netscape
        os.makedirs(os.path.dirname(COOKIE_PATH), exist_ok=True)

        cj = http.cookiejar.MozillaCookieJar(COOKIE_PATH)

        for cookie in session.cookies:
            cj.set_cookie(cookie)

        cj.save(ignore_discard=True, ignore_expires=True)

        print(f"💾 Cookies salvos em: {COOKIE_PATH}")

        # Verifica o resultado
        return verificar_cookies_atuais()

    except instaloader.exceptions.BadCredentialsException:
        print("❌ Usuário ou senha incorretos!")
        return False
    except instaloader.exceptions.TwoFactorAuthRequiredException:
        print("\n⚠️  Autenticação de dois fatores (2FA) necessária!")
        code = input("🔢 Código 2FA: ").strip()
        try:
            L.two_factor_login(code)
            print("✅ Login com 2FA bem-sucedido!")

            session = L.context._session
            os.makedirs(os.path.dirname(COOKIE_PATH), exist_ok=True)
            cj = http.cookiejar.MozillaCookieJar(COOKIE_PATH)
            for cookie in session.cookies:
                cj.set_cookie(cookie)
            cj.save(ignore_discard=True, ignore_expires=True)
            print(f"💾 Cookies salvos em: {COOKIE_PATH}")
            return verificar_cookies_atuais()
        except Exception as e:
            print(f"❌ Erro no 2FA: {e}")
            return False
    except instaloader.exceptions.ConnectionException as e:
        print(f"❌ Erro de conexão: {e}")
        print("   Possíveis causas: IP bloqueado, muitas tentativas, ou Instagram fora do ar.")
        return False
    except Exception as e:
        print(f"❌ Erro inesperado: {e}")
        return False


def renovar_via_browser_manual():
    """Instrui o usuário a exportar cookies manualmente do navegador."""
    print("\n" + "=" * 50)
    print("🌐 Exportação Manual de Cookies do Navegador")
    print("=" * 50)
    print("""
Se o login via Instaloader não funcionar (2FA complexo, captcha, etc.),
você pode exportar os cookies manualmente do navegador:

1. Instale a extensão "Get cookies.txt LOCALLY" no Chrome/Firefox
   Chrome: https://chromewebstore.google.com/detail/get-cookiestxt-locally/cclelndahbckbenkjhflpdbgdldlbecc
   Firefox: https://addons.mozilla.org/en-US/firefox/addon/cookies-txt/

2. Faça login no Instagram (instagram.com) com a conta do bot

3. Na extensão, clique em "Export" e salve como cookies.txt

4. Copie o arquivo para:
   """ + COOKIE_PATH + """

5. Reinicie o bot

IMPORTANTE: O arquivo deve conter pelo menos estes cookies:
  - sessionid (ESSENCIAL!)
  - ds_user_id  
  - csrftoken
  - mid
""")


def main():
    print("🍪 Ferramenta de Renovação de Cookies Instagram")
    print("=" * 50)

    # Verifica estado atual
    cookies_ok = verificar_cookies_atuais()

    if cookies_ok:
        resposta = input("\nCookies parecem OK. Deseja renovar mesmo assim? (s/n): ").strip().lower()
        if resposta != "s":
            print("👋 Saindo sem alterações.")
            return

    print("\nEscolha o método de renovação:")
    print("  1. Login via Instaloader (recomendado)")
    print("  2. Instruções para exportar do navegador")
    print("  3. Sair")

    escolha = input("\nOpção (1/2/3): ").strip()

    if escolha == "1":
        sucesso = renovar_via_instaloader()
        if sucesso:
            print("\n🎉 Cookies renovados com sucesso!")
            print("   Agora reinicie o bot para usar os novos cookies.")
        else:
            print("\n⚠️  Se o login direto não funcionou, tente a opção 2 (exportação manual do navegador).")
    elif escolha == "2":
        renovar_via_browser_manual()
    else:
        print("👋 Saindo.")


if __name__ == "__main__":
    main()
