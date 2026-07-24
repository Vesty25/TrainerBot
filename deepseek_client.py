import httpx
from openai import AsyncOpenAI
import config

# HTTP-клиент с прокси (если нужен)
# Если VPN работает системно — замени proxy=None
http_client = httpx.AsyncClient(
    proxy=None,  # ← замени на свой HTTP-порт или убери строку
    timeout=60.0,
)

client = AsyncOpenAI(
    api_key=config.DEEPSEEK_API_KEY,
    base_url="https://api.deepseek.com/v1",
    http_client=http_client,
)


async def get_ai_response(messages: list) -> str:
    """
    Отправляет историю сообщений в DeepSeek и возвращает ответ.
    """
    try:
        response = await client.chat.completions.create(
            model="deepseek-chat",
            messages=messages,
            temperature=0.8,
            max_tokens=2048,
        )
        return response.choices[0].message.content
    except Exception as e:
        return f"[Ошибка при обращении к DeepSeek API: {e}]"