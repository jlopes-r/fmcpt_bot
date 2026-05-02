import os
from dotenv import load_dotenv
import sys

# Adiciona o caminho do projeto para encontrar pacotes, se necessário
caminho_raiz_projeto = "/home/juanl/fmcpt_bot"
sys.path.append(caminho_raiz_projeto)

# Use o mesmo caminho absoluto que o bot está usando
caminho_env = os.path.join(caminho_raiz_projeto, "apps", "telegram_bot", ".env")

print("--- 诊断 .env 文件加载情况 ---")
print(f"正在尝试读取的路径: {caminho_env}")

# Verifica se o arquivo existe
if os.path.exists(caminho_env):
    print("✅ .env 文件已找到。")
    load_dotenv(dotenv_path=caminho_env)
    
    # Verifica a variável do Token
    token = os.getenv("BOT_TOKEN_COMANDOS")
    if token and len(token) > 10:
        print(f"✅ BOT_TOKEN_COMANDOS: 已找到。")
        print(f"   -> 令牌末尾为: ...{token[-4:]}")
    else:
        print(f"❌ BOT_TOKEN_COMANDOS: *** 未找到或无效 ***")
        
    # Verifica a variável dos Grupos
    grupos = os.getenv("GRUPOS_AUTORIZADOS")
    if grupos:
        print(f"✅ GRUPOS_AUTORIZADOS: 已找到。")
        print(f"   -> 值: {grupos}")
    else:
        print(f"❌ GRUPOS_AUTORIZADOS: *** 未找到 ***")

else:
    print(f"❌ .env 文件在此路径 *** 未找到 ***")

print("--- 测试结束 ---")
