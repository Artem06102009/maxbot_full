import logging
from datetime import timedelta

from sqlalchemy import select

from app.config import get_settings
from app.database.models import ClassSettings, Question, User
from app.database.session import async_session_factory
from app.services.class_tools import msk_today, utc_now, weekly_rows, weekly_summary
from app.services.max_client import get_bot_client

logger = logging.getLogger(__name__)


async def send_class_digest(chat_id: str) -> str:
    settings = get_settings()
    async with async_session_factory() as session:
        rows = await weekly_rows(session, str(chat_id) if chat_id not in ("1", 1) else None)
        summary = weekly_summary(rows)
        students = (await session.execute(
            select(User).where(User.role == "student").order_by(User.xp.desc()).limit(5)
        )).scalars().all()
        open_q = (await session.execute(select(Question).where(Question.status == "open"))).scalars().all()
        top_subject = summary["subjects"][0][0] if summary["subjects"] else "пока тихо"
        lines = ["📬 Недельный дайджест класса\n"]
        if students:
            lines.append("Топ помощников:")
            medals = ["🥇", "🥈", "🥉", "4️⃣", "5️⃣"]
            for i, s in enumerate(students):
                lines.append(f"{medals[i]} {s.display_name} — {s.xp} XP ({s.rank})")
        lines.append(f"\nВопросов за 7 дней: {summary['questions']}")
        lines.append(f"Сейчас без ответа: {len(open_q)}")
        lines.append(f"Самая частая тема: {top_subject}")
        if open_q:
            lines.append("\nВисящие вопросы:")
            for q in open_q[:5]:
                lines.append(f"• {q.text[:80]}")
        text = "\n".join(lines)
        row = (await session.execute(select(ClassSettings).where(ClassSettings.group_chat_id == str(chat_id)))).scalar_one_or_none()
        if row:
            row.last_digest_on = msk_today()
            await session.commit()
    try:
        client = get_bot_client()
        if str(chat_id) not in ("1", "0") and client.token:
            cid = int(chat_id) if str(chat_id).lstrip("-").isdigit() else None
            if cid:
                await client.send_message(chat_id=cid, text=text)
    except Exception as exc:
        logger.warning("digest send failed for %s: %s", chat_id, exc)
    return text


async def class_jobs_loop():
    import asyncio
    await asyncio.sleep(8)
    while True:
        try:
            await _tick_unanswered()
            await _tick_weekly_digest()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("class jobs failed")
        await asyncio.sleep(60)


async def _tick_unanswered():
    minutes = get_settings().UNANSWERED_MINUTES
    threshold = utc_now() - timedelta(minutes=minutes)
    async with async_session_factory() as session:
        hanging = (await session.execute(
            select(Question).where(Question.status == "open", Question.nudge_sent_at.is_(None))
        )).scalars().all()
        due = []
        for q in hanging:
            created = q.created_at
            if created and created.tzinfo is None:
                from datetime import timezone
                created = created.replace(tzinfo=timezone.utc)
            if created and created <= threshold and q.group_chat_id not in ("1", "0", ""):
                q.nudge_sent_at = utc_now()
                due.append(q)
        await session.commit()
        payload = [(q.group_chat_id, q.id, q.text, q.subject or "Учёба") for q in due]
    client = get_bot_client()
    for chat_id, q_id, text, subject in payload:
        try:
            cid = int(chat_id) if str(chat_id).lstrip("-").isdigit() else None
            if not cid:
                continue
            await client.send_message(
                chat_id=cid,
                text=(
                    f"⏳ Вопрос без ответа уже {minutes} мин.\n"
                    f"{subject}: «{text[:240]}»\n\n"
                    "Ответьте реплаем на карточку вопроса."
                ),
            )
        except Exception as exc:
            logger.warning("nudge failed q=%s: %s", q_id, exc)


async def _tick_weekly_digest():
    local = utc_now() + timedelta(hours=3)
    if local.weekday() != 6 or local.hour != 18:
        return
    today = msk_today()
    async with async_session_factory() as session:
        chats = (await session.execute(select(ClassSettings))).scalars().all()
        pending = [
            c.group_chat_id
            for c in chats
            if c.last_digest_on != today
            and c.group_chat_id not in ("1", "0")
            and getattr(c, "digest_enabled", True) is not False
        ]
    for chat_id in pending:
        await send_class_digest(chat_id)
