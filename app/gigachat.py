import logging
import ssl
import time
import uuid
from typing import Optional

import aiohttp

from config import (
    GIGACHAT_API_URL,
    GIGACHAT_CREDENTIALS,
    GIGACHAT_MODEL,
    GIGACHAT_OAUTH_URL,
    GIGACHAT_SCOPE,
)

logger = logging.getLogger(__name__)

_token: Optional[str] = None
_token_exp: float = 0

HINT_SYSTEM = """Ты школьный наставник в чате класса (5–11 класс, Россия).
Ученик застрял на домашнем задании. Дай КОРОТКУЮ подсказку, а не готовое решение.
Правила:
- не пиши итоговый ответ, численный результат или готовое сочинение;
- объясни, с чего начать, какую формулу/правило вспомнить, на что обратить внимание;
- 4–8 коротких предложений, простым языком;
- если приложено фото задачи — сначала аккуратно прочитай условие;
- не оскорбляй, не обсуждай ничего, кроме учёбы.
"""

STRICT_EXTRA = """Сейчас у класса строгий режим (контрольная/самостоятельная).
Только намёк: какая тема и какой первый шаг. Никаких промежуточных выкладок и ответов."""


def _ssl_ctx() -> ssl.SSLContext:
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


def is_configured() -> bool:
    return bool(GIGACHAT_CREDENTIALS and GIGACHAT_CREDENTIALS not in ("", "YOUR_GIGACHAT_KEY"))


def fallback_hint(question: str, strict: bool) -> str:
    if strict:
        return (
            "🔒 Строгий режим: ГигаЧат сейчас только намекает.\n"
            "Вспомни тему урока, выпиши «дано / найти» и сделай один маленький шаг. "
            "Готовое решение пусть подскажет одноклассник."
        )
    text = (question or "задачу").strip()[:180]
    return (
        f"🤖 Пока ключ ГигаЧат не задан, разберём «{text}» так:\n"
        "1) Что известно и что нужно получить?\n"
        "2) Какая формула или правило из этой темы?\n"
        "3) Сделай один шаг и сверься с одноклассником — за живое объяснение дают XP."
    )


async def _get_token(session: aiohttp.ClientSession) -> str:
    global _token, _token_exp
    if _token and time.time() < _token_exp - 60:
        return _token
    key = GIGACHAT_CREDENTIALS.strip()
    if key.lower().startswith("basic "):
        auth = key
    else:
        auth = f"Basic {key}"
    headers = {
        "Authorization": auth,
        "RqUID": str(uuid.uuid4()),
        "Content-Type": "application/x-www-form-urlencoded",
        "Accept": "application/json",
    }
    async with session.post(
        GIGACHAT_OAUTH_URL,
        headers=headers,
        data={"scope": GIGACHAT_SCOPE},
        ssl=_ssl_ctx(),
    ) as resp:
        payload = await resp.json(content_type=None)
        if resp.status >= 400:
            raise RuntimeError(payload.get("message") or f"OAuth {resp.status}")
        _token = payload["access_token"]
        exp = payload.get("expires_at")
        _token_exp = (exp / 1000 if exp and exp > 10_000_000_000 else exp) if exp else time.time() + 1700
        if _token_exp > 10_000_000_000:
            _token_exp = _token_exp / 1000
        return _token


async def _upload_image(session: aiohttp.ClientSession, token: str, image: bytes, filename: str) -> str:
    form = aiohttp.FormData()
    form.add_field("purpose", "general")
    form.add_field("file", image, filename=filename, content_type="application/octet-stream")
    async with session.post(
        f"{GIGACHAT_API_URL}/files",
        headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
        data=form,
        ssl=_ssl_ctx(),
    ) as resp:
        payload = await resp.json(content_type=None)
        if resp.status >= 400:
            raise RuntimeError(payload.get("message") or f"upload {resp.status}")
        return payload["id"]


async def make_hint(
    question: str,
    *,
    strict: bool = False,
    image: Optional[bytes] = None,
    image_name: str = "task.jpg",
) -> str:
    if not is_configured():
        return fallback_hint(question, strict)

    system = HINT_SYSTEM + ("\n" + STRICT_EXTRA if strict else "")
    user_text = f"Вопрос ученика:\n{question or 'Условие на фото.'}\n\nДай подсказку, не решение."

    async with aiohttp.ClientSession() as session:
        token = await _get_token(session)
        message: dict = {"role": "user", "content": user_text}
        if image:
            file_id = await _upload_image(session, token, image, image_name)
            message["attachments"] = [file_id]
        body = {
            "model": GIGACHAT_MODEL,
            "messages": [
                {"role": "system", "content": system},
                message,
            ],
            "temperature": 0.4,
        }
        async with session.post(
            f"{GIGACHAT_API_URL}/chat/completions",
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            json=body,
            ssl=_ssl_ctx(),
        ) as resp:
            payload = await resp.json(content_type=None)
            if resp.status >= 400:
                logger.error("GigaChat error: %s", payload)
                return fallback_hint(question, strict)
            try:
                return payload["choices"][0]["message"]["content"].strip()
            except (KeyError, IndexError, TypeError):
                return fallback_hint(question, strict)


async def download_url(url: str) -> tuple[bytes, str]:
    async with aiohttp.ClientSession() as session:
        async with session.get(url, ssl=_ssl_ctx()) as resp:
            resp.raise_for_status()
            data = await resp.read()
            name = "task.jpg"
            ctype = resp.headers.get("Content-Type", "")
            if "png" in ctype:
                name = "task.png"
            return data, name
