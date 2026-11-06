import asyncio
import logging
from datetime import timedelta

from sqlmodel import Session, select, desc

from app.database import engine
from app.logic import ensure_aware, msk_today, utc_now
from app.models import ClassChat, Question, User, calculate_level
from config import UNANSWERED_MINUTES
from bot.main import bot

logger = logging.getLogger(__name__)


def _digest_text(session: Session, chat_id: int) -> str:
    week_ago = utc_now() - timedelta(days=7)
    students = session.exec(
        select(User).where(User.role == "student", User.chat_id == chat_id).order_by(desc(User.xp)).limit(5)
    ).all()
    questions = session.exec(select(Question).where(Question.chat_id == chat_id)).all()
    recent = [q for q in questions if ensure_aware(q.created_at) >= week_ago]
    open_q = [q for q in questions if q.status == "open"]
    subjects = {}
    for q in recent:
        subjects[q.subject] = subjects.get(q.subject, 0) + 1
    top_subject = max(subjects, key=subjects.get) if subjects else "пока тихо"

    lines = ["📬 **Недельный дайджест класса**\n"]
    if students:
        lines.append("Топ помощников:")
        medals = ["🥇", "🥈", "🥉", "4️⃣", "5️⃣"]
        for i, s in enumerate(students):
            lvl = calculate_level(s.xp)
            lines.append(f"{medals[i]} {s.name} — {s.xp} XP ({lvl['title']})")
    lines.append(f"\nВопросов за 7 дней: {len(recent)}")
    lines.append(f"Сейчас без ответа: {len(open_q)}")
    lines.append(f"Самая частая тема: {top_subject}")
    if open_q:
        lines.append("\nВисящие вопросы:")
        for q in open_q[:5]:
            lines.append(f"• {q.text[:80]}")
    return "\n".join(lines)


async def send_class_digest(chat_id: int) -> str:
    with Session(engine, expire_on_commit=False) as session:
        text = _digest_text(session, chat_id)
        chat = session.exec(select(ClassChat).where(ClassChat.max_chat_id == chat_id)).first()
        if chat:
            chat.last_digest_on = msk_today()
            session.add(chat)
            session.commit()
    try:
        await bot.send_message(chat_id=chat_id, text=text)
    except Exception as exc:
        logger.warning("digest send failed for %s: %s", chat_id, exc)
    return text


async def class_jobs_loop():
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
    threshold = utc_now() - timedelta(minutes=UNANSWERED_MINUTES)
    with Session(engine, expire_on_commit=False) as session:
        hanging = session.exec(
            select(Question).where(Question.status == "open", Question.nudge_sent_at == None)  # noqa: E711
        ).all()
        due = []
        for q in hanging:
            if ensure_aware(q.created_at) <= threshold and q.chat_id and q.chat_id != 1:
                due.append(q)
        for q in due:
            q.nudge_sent_at = utc_now()
            session.add(q)
        session.commit()
        payload = [(q.chat_id, q.id, q.text, q.subject) for q in due]

    for chat_id, q_id, text, subject in payload:
        try:
            await bot.send_message(
                chat_id=chat_id,
                text=(
                    f"⏳ Вопрос без ответа уже {UNANSWERED_MINUTES} мин.\n"
                    f"**{subject}:** «{text[:240]}»\n\n"
                    "Одноклассники, помогите в чате и нажмите «Я помог». "
                    "Если совсем тупик — кнопка «Подсказка ГигаЧат»."
                ),
            )
        except Exception as exc:
            logger.warning("nudge failed q=%s: %s", q_id, exc)


async def _tick_weekly_digest():
    local = utc_now() + timedelta(hours=3)
    if local.weekday() != 6 or local.hour != 18:
        return
    today = msk_today()
    with Session(engine, expire_on_commit=False) as session:
        chats = session.exec(select(ClassChat)).all()
        pending = [c.max_chat_id for c in chats if c.last_digest_on != today and c.max_chat_id != 1]
    for chat_id in pending:
        await send_class_digest(chat_id)
