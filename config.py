import os
from dotenv import load_dotenv

load_dotenv()

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")

# Прокси (нужен только при локальном запуске в России)
PROXY_URL = os.getenv("PROXY_URL", None)

# Ключи платёжной системы (добавишь позже)
PLATEGA_API_KEY = os.getenv("PLATEGA_API_KEY", "")
PLATEGA_SHOP_ID = os.getenv("PLATEGA_SHOP_ID", "")
PLATEGA_SECRET_KEY = os.getenv("PLATEGA_SECRET_KEY", "")

if not TELEGRAM_BOT_TOKEN:
    raise ValueError("TELEGRAM_BOT_TOKEN не найден. Проверь файл .env")
if not DEEPSEEK_API_KEY:
    raise ValueError("DEEPSEEK_API_KEY не найден. Проверь файл .env")
