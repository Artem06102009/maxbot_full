import logging
import time
import uuid
from typing import Any, Dict, List, Optional
import httpx

from app.config import get_settings

logger = logging.getLogger(__name__)

# Try importing the official gigachat library
try:
    from gigachat import GigaChat
    from gigachat.models import Chat, Messages, MessagesRole
    HAS_OFFICIAL_SDK = True
except ImportError:
    HAS_OFFICIAL_SDK = False

_token: Optional[str] = None
_token_exp: float = 0

SOCRATIC_MENTOR_SYSTEM = """Ты школьный наставник в мессенджере MAX (5–11 класс).
Ученик обратился к тебе в личном диалоге за помощью с учебной задачей.
Правила:
- Никогда не давай готовое решение, итоговый численный ответ или готовое сочинение!
- Используй сократический метод: наводящие вопросы, напоминание формулы, определение понятий.
- Помоги ученику сделать первый шаг самостоятельно.
- Отвечай кратко, дружелюбно и по делу (4–6 предложений).
"""

CHEATSHEET_EXEMPLAR_SYSTEM = """Ты опытный школьный методист.
Твоя задача — превратить ответ ученика на задачу в ёмкую формулировку для шпаргалки класса:
- Выдели ключевое правило, формулу или тезис (1-2 предложения).
- Сделай так, чтобы одноклассники могли быстро вспомнить тему перед уроком.
- Без лишней воды.
"""

PARENT_SUMMARY_SYSTEM = """Ты доброжелательный школьный педагог-психолог.
Напиши короткий, тёплый отчёт для родителей (3-5 предложений) об учебных успехах ребёнка за неделю.
Подчеркни сильные стороны: отзывчивость, помощь одноклассникам, инициативность и стремление разобраться в сложных темах.
"""

GIGACHAT_TUTOR_SYSTEM = """Ты ГигаЧат — умный школьный наставник и репетитор в сервисе «Лига Помощников» (мессенджер MAX).
Твоя цель — помогать школьникам (5–11 классы) понимать школьные предметы, готовиться к урокам, разбирать сложные понятия и задачи.

Правила общения:
1. Объясняй доступно, структурированно, понятно и доброжелательно.
2. При разборе задач показывай логику и метод решения шаг за шагом, поясняя смысл формул.
3. Не используй эмодзи (отвечай строго, с ясным форматированием).
4. Если ученик просит готовое сочинение или доклад, помоги составить подробный план, аргументы и тезисы, стимулируя самостоятельную работу.
5. Используй списки, выделения и чёткие шаги решения.
"""


def is_configured() -> bool:
    key = get_settings().GIGACHAT_CREDENTIALS
    return bool(key and key.strip() not in ("", "YOUR_GIGACHAT_KEY"))


async def _call_gigachat_api(messages: List[Dict[str, str]], temperature: float = 0.5) -> str:
    settings = get_settings()
    key = settings.GIGACHAT_CREDENTIALS.strip()

    # 1. Try official GigaChat SDK if available
    if HAS_OFFICIAL_SDK and is_configured():
        try:
            scope = settings.GIGACHAT_SCOPE or "GIGACHAT_API_PERS"
            role_map = {
                "system": MessagesRole.SYSTEM,
                "user": MessagesRole.USER,
                "assistant": MessagesRole.ASSISTANT,
            }
            chat_messages = [
                Messages(role=role_map.get(m["role"], MessagesRole.USER), content=m["content"])
                for m in messages
            ]
            async with GigaChat(
                credentials=key,
                scope=scope,
                model=settings.GIGACHAT_MODEL,
                verify_ssl_certs=False,
                temperature=temperature,
            ) as giga:
                response = await giga.achat(Chat(messages=chat_messages))
                if response.choices:
                    return response.choices[0].message.content.strip()
        except Exception as exc:
            logger.warning("official gigachat sdk call failed, trying HTTP fallback: %s", exc)

    # 2. HTTP Fallback via direct REST API
    async with httpx.AsyncClient(verify=False, timeout=25) as client:
        token = await _get_token(client)
        resp = await client.post(
            f"{settings.GIGACHAT_API_URL.rstrip('/')}/chat/completions",
            headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
            json={
                "model": settings.GIGACHAT_MODEL,
                "messages": messages,
                "temperature": temperature,
            },
        )
        resp.raise_for_status()
        data = resp.json()
        return data["choices"][0]["message"]["content"].strip()


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


async def make_private_hint(question: str) -> str:
    """Private hint for a student when asking in DM (not in group chat!)."""
    if not is_configured():
        text = (question or "задачу").strip()[:180]
        return (
            f"💡 Разберём вопрос «{text}»:\n"
            "1. Что дано и что требуется найти?\n"
            "2. Какая главная формула или теорема связывает эти величины?\n"
            "3. Попробуй подставить известные данные, а если трудно — задай вопрос в чате класса через /ask!"
        )
    try:
        messages = [
            {"role": "system", "content": SOCRATIC_MENTOR_SYSTEM},
            {"role": "user", "content": f"Помоги с задачей: {question}"},
        ]
        return await _call_gigachat_api(messages, temperature=0.6)
    except Exception:
        logger.exception("gigachat private hint failed")
        return (
            "💡 Вспомни базовое определение темы и запиши краткое условие задачи. "
            "Сделай первый шаг и сверься с одноклассниками в чате класса!"
        )


async def explain_for_cheatsheet(question_text: str, answer_text: str) -> str:
    """Generates an exemplar summary for the class cheatsheet from question + answer."""
    if not is_configured():
        return f"Ключевая идея: {answer_text[:120]} (тема проверена учителем)"
    try:
        messages = [
            {"role": "system", "content": CHEATSHEET_EXEMPLAR_SYSTEM},
            {
                "role": "user",
                "content": f"Вопрос: {question_text}\nОтвет ученика: {answer_text}\nСформулируй краткий эталон для конспекта.",
            },
        ]
        return await _call_gigachat_api(messages, temperature=0.3)
    except Exception:
        logger.exception("gigachat cheatsheet exemplar failed")
        return f"Правило: {answer_text[:120]}"


async def generate_parent_summary(
    student_name: str, helped_count: int, questions_count: int, subjects: List[str]
) -> str:
    """Generates a warm psychological summary for parents based on week activity."""
    subj_str = ", ".join(subjects) if subjects else "учебным предметам"
    if not is_configured():
        if helped_count > 0:
            return (
                f"{student_name} на этой неделе проявил отличную активность, помогая одноклассникам "
                f"разобраться с заданиями по {subj_str} ({helped_count} раз). Умение объяснять сложные вещи "
                "своими словами помогает ещё глубже усваивать материал!"
            )
        return (
            f"{student_name} активно участвует в учебной жизни класса. Регулярная работа над вопросами "
            "помогает увереннее готовиться к проверочным работам."
        )
    try:
        prompt = (
            f"Ученик: {student_name}\n"
            f"Помог одноклассникам: {helped_count} раз\n"
            f"Задал вопросов: {questions_count}\n"
            f"Предметы активности: {subj_str}\n"
            "Напиши персональную характеристику за неделю для родителей."
        )
        messages = [
            {"role": "system", "content": PARENT_SUMMARY_SYSTEM},
            {"role": "user", "content": prompt},
        ]
        return await _call_gigachat_api(messages, temperature=0.7)
    except Exception:
        logger.exception("gigachat parent summary failed")
        return f"{student_name} успешно осваивает программу и активно взаимодействует с классом по теме: {subj_str}."


async def chat_with_gigachat(
    dialog_history: List[Dict[str, str]],
    student_name: Optional[str] = None
) -> str:
    """Conversational assistant using official gigachat library for the WebApp interface."""
    messages = [{"role": "system", "content": GIGACHAT_TUTOR_SYSTEM}]
    for msg in dialog_history[-10:]:
        role = msg.get("role", "user")
        content = (msg.get("content") or "").strip()
        if content and role in ("user", "assistant"):
            messages.append({"role": role, "content": content})

    if not is_configured():
        last_user = dialog_history[-1].get("content", "") if dialog_history else ""
        return (
            f"Я проанализировал ваш вопрос: «{last_user}».\n\n"
            "Рекомендация по решению:\n"
            "1. Запишите краткое условие: что известно и что требуется найти.\n"
            "2. Выберите базовый закон или формулу по данной теме.\n"
            "3. Выразите искомую величину в общем виде перед подстановкой числовых данных.\n\n"
            "Если нужно разобрать конкретный шаг подробнее, напишите, какой этап вызывает затруднение."
        )

    try:
        return await _call_gigachat_api(messages, temperature=0.7)
    except Exception:
        logger.exception("chat_with_gigachat failed")
        return (
            "Сейчас сервис временно перегружен. Пожалуйста, повторите запрос через минуту."
        )


# Legacy alias
async def make_hint(question: str) -> str:
    return await make_private_hint(question)
