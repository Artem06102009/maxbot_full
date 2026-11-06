import logging
import time
import uuid
from typing import Optional

import httpx

from app.config import get_settings

logger = logging.getLogger(__name__)
_token: Optional[str] = None
_token_exp: float = 0

HINT_SYSTEM = """Ты школьный наставник в чате класса (5–11 класс, Россия).
Ученик застрял на домашнем задании. Дай КОРОТКУЮ подсказку, а не готовое решение.
Правила:
- не пиши итоговый ответ, численный результат или готовое сочинение;
- объясни, с чего начать, какую формулу или правило вспомнить;
- 4–8 коротких предложений;
- не обсуждай ничего, кроме учёбы.
"""


def is_configured() -> bool:
    key = get_settings().GIGACHAT_CREDENTIALS
    return bool(key and key not in ("", "YOUR_GIGACHAT_KEY"))


def fallback_hint(question: str) -> str:
    text = (question or "задачу").strip()[:180]
    return (
        f"Пока ключ ГигаЧат не задан, разберём «{text}» так:\n"
        "1) Что известно и что нужно получить?\n"
        "2) Какая формула или правило из этой темы?\n"
        "3) Сделай один шаг и сверься с одноклассником — за живое объяснение дают XP."
    )


async def make_hint(question: str) -> str:
    if not is_configured():
        return fallback_hint(question)
    settings = get_settings()
    try:
        async with httpx.AsyncClient(verify=False, timeout=25) as client:
            token = await _get_token(client)
            resp = await client.post(
                f"{settings.GIGACHAT_API_URL.rstrip('/')}/chat/completions",
                headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
                json={
                    "model": settings.GIGACHAT_MODEL,
                    "messages": [
                        {"role": "system", "content": HINT_SYSTEM},
                        {"role": "user", "content": question},
                    ],
                },
            )
            resp.raise_for_status()
            data = resp.json()
            return data["choices"][0]["message"]["content"].strip()
    except Exception:
        logger.exception("gigachat hint failed")
        return fallback_hint(question)


async def _get_token(client: httpx.AsyncClient) -> str:
    global _token, _token_exp
    if _token and time.time() < _token_exp - 60:
        return _token
    settings = get_settings()
    key = settings.GIGACHAT_CREDENTIALS.strip()
    auth = key if key.lower().startswith("basic ") else f"Basic {key}"
    resp = await client.post(
        settings.GIGACHAT_OAUTH_URL,
        headers={
            "Authorization": auth,
            "RqUID": str(uuid.uuid4()),
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json",
        },
        data={"scope": settings.GIGACHAT_SCOPE},
    )
    resp.raise_for_status()
    payload = resp.json()
    _token = payload["access_token"]
    _token_exp = time.time() + int(payload.get("expires_at") or payload.get("expires_in") or 1800)
    return _token
