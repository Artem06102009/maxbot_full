import csv
import io
import re
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import Answer, ClassSettings, Question, User

CLASS_SUBJECTS = ("Точные науки", "Гуманитарные", "Учёба")
LEVELS = [
    {"level": 1, "title": "Новичок", "xp": 0},
    {"level": 2, "title": "Стажёр", "xp": 50},
    {"level": 3, "title": "Помощник", "xp": 150},
    {"level": 4, "title": "Знаток", "xp": 350},
    {"level": 5, "title": "Мастер", "xp": 700},
    {"level": 6, "title": "Наставник", "xp": 1200},
    {"level": 7, "title": "Легенда школы", "xp": 2000},
]


def calculate_level(xp: int) -> dict:
    current = LEVELS[0]
    nxt = None
    for i, lvl in enumerate(LEVELS):
        if xp >= lvl["xp"]:
            current = lvl
            nxt = LEVELS[i + 1] if i + 1 < len(LEVELS) else None
        else:
            break
    if nxt:
        needed = nxt["xp"] - current["xp"]
        gained = xp - current["xp"]
        progress = max(0, min(100, int((gained / needed) * 100))) if needed else 100
        next_xp = nxt["xp"]
    else:
        progress, next_xp = 100, current["xp"]
    return {
        "level": current["level"],
        "title": current["title"],
        "current_xp": xp,
        "next_xp": next_xp,
        "progress_percent": progress,
    }


def apply_pending_rewards(user: User) -> bool:
    from app.services.anti_cheat import AntiCheatService
    if AntiCheatService.is_curfew_active():
        return False
    if not user.pending_xp and not user.pending_coins:
        return False
    user.xp += user.pending_xp
    user.balance_spasibki += user.pending_coins
    user.pending_xp = 0
    user.pending_coins = 0
    from app.services.gamification import GamificationService
    user.rank = GamificationService.calculate_rank(user.xp)
    return True
PHOTO_FALLBACK = "Помогите разобрать задачу с фото."
SUBJECT_ALIASES = {
    "физика": "Точные науки",
    "математика": "Точные науки",
    "матем": "Точные науки",
    "алгебра": "Точные науки",
    "геометрия": "Точные науки",
    "химия": "Точные науки",
    "информатика": "Точные науки",
    "русский": "Гуманитарные",
    "литература": "Гуманитарные",
    "история": "Гуманитарные",
    "обществознание": "Гуманитарные",
}


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def parse_ask_text(raw: str) -> tuple[str, str]:
    text = (raw or "").strip()
    if not text:
        return "Учёба", ""
    parts = text.split(maxsplit=1)
    alias = SUBJECT_ALIASES.get(parts[0].lower().strip(".,:"))
    if alias:
        return alias, parts[1].strip() if len(parts) > 1 else ""
    lowered = text.lower()
    if any(w in lowered for w in ("матем", "физик", "хими", "геометр", "алгебр", "уравнен")):
        return "Точные науки", text
    if any(w in lowered for w in ("литерат", "русск", "истори", "сочинен")):
        return "Гуманитарные", text
    return "Учёба", text


def resolve_subject_name(raw: str) -> Optional[str]:
    key = (raw or "").lower().strip(".,:")
    if key in ("все", "all", "on", "вкл"):
        return "*"
    if key in CLASS_SUBJECTS:
        return key
    if "точн" in key or "физик" in key or "матем" in key:
        return "Точные науки"
    if "гуман" in key or "русск" in key or "лит" in key:
        return "Гуманитарные"
    return SUBJECT_ALIASES.get(key)


def normalize_question_text(text: str) -> str:
    value = (text or "").lower().replace("ё", "е")
    value = re.sub(r"[^\wа-я]+", " ", value, flags=re.IGNORECASE)
    return re.sub(r"\s+", " ", value).strip()


def decode_strict_subjects(settings: Optional[ClassSettings]) -> list[str]:
    if not settings:
        return []
    if settings.strict_mode:
        return ["*"]
    raw = (settings.strict_subjects or "").strip()
    if not raw:
        return []
    if raw == "*":
        return ["*"]
    return [part.strip() for part in raw.split(",") if part.strip()]


def encode_strict_subjects(subjects: list[str]) -> tuple[bool, Optional[str]]:
    cleaned = []
    for item in subjects:
        if item == "*":
            return True, None
        if item in CLASS_SUBJECTS and item not in cleaned:
            cleaned.append(item)
    return False, (",".join(cleaned) if cleaned else None)


def is_exam_active(settings: Optional[ClassSettings]) -> bool:
    if not settings or not settings.exam_until:
        return False
    return settings.exam_until >= msk_today()


def decode_exam_subjects(settings: Optional[ClassSettings]) -> list[str]:
    if not is_exam_active(settings):
        return []
    raw = (settings.exam_subjects or "*").strip() or "*"
    if raw == "*":
        return ["*"]
    return [part.strip() for part in raw.split(",") if part.strip()]


def exam_blocks_shop(settings: Optional[ClassSettings]) -> bool:
    return is_exam_active(settings)


def exam_label(settings: Optional[ClassSettings]) -> str:
    if not is_exam_active(settings):
        return ""
    until = settings.exam_until or msk_today()
    try:
        shown = datetime.strptime(until, "%Y-%m-%d").strftime("%d.%m")
    except ValueError:
        shown = until
    subjects = decode_exam_subjects(settings)
    if "*" in subjects:
        topics = "все предметы"
    else:
        topics = ", ".join(subjects) or "все предметы"
    return f"Контрольная до {shown}: {topics}. ГигаЧат и лавка выключены, вопросы одноклассникам можно."


def set_exam_mode(settings: ClassSettings, subjects: Optional[list[str]], days: int = 1) -> None:
    if not subjects:
        settings.exam_until = None
        settings.exam_subjects = None
        return
    days = max(1, min(14, int(days)))
    until = (utc_now() + timedelta(hours=3) + timedelta(days=days - 1)).strftime("%Y-%m-%d")
    if "*" in subjects:
        settings.exam_until = until
        settings.exam_subjects = "*"
        return
    cleaned = [item for item in subjects if item in CLASS_SUBJECTS]
    settings.exam_until = until if cleaned else None
    settings.exam_subjects = ",".join(cleaned) if cleaned else None


def is_ai_blocked(settings: Optional[ClassSettings], subject: Optional[str]) -> bool:
    if is_exam_active(settings):
        exam = decode_exam_subjects(settings)
        if "*" in exam:
            return True
        if subject and subject in exam:
            return True
    blocked = decode_strict_subjects(settings)
    if "*" in blocked:
        return True
    return bool(subject and subject in blocked)


async def primary_class_settings(session: AsyncSession) -> ClassSettings:
    q = (await session.execute(select(Question.group_chat_id).order_by(Question.id.desc()).limit(1))).first()
    group_id = q[0] if q else "1"
    return await get_class_settings(session, group_id)


async def get_class_settings(session: AsyncSession, group_chat_id: str) -> ClassSettings:
    res = await session.execute(select(ClassSettings).where(ClassSettings.group_chat_id == str(group_chat_id)))
    row = res.scalar_one_or_none()
    if row:
        return row
    row = ClassSettings(group_chat_id=str(group_chat_id), strict_mode=False)
    session.add(row)
    await session.flush()
    return row


async def find_hanging_duplicate(
    session: AsyncSession,
    group_chat_id: str,
    text: str,
    image_url: Optional[str] = None,
) -> Optional[Question]:
    since = utc_now() - timedelta(hours=1)
    norm = normalize_question_text(text)
    photo_norm = normalize_question_text(PHOTO_FALLBACK)
    res = await session.execute(
        select(Question).where(Question.group_chat_id == str(group_chat_id), Question.status == "open")
    )
    for q in res.scalars().all():
        created = q.created_at.replace(tzinfo=timezone.utc) if q.created_at and q.created_at.tzinfo is None else q.created_at
        if created and created < since:
            continue
        if image_url and q.image_url == image_url:
            return q
        if not norm or normalize_question_text(q.text) != norm:
            continue
        if norm == photo_norm and image_url != q.image_url:
            continue
        return q
    return None


def ensure_report_token(user: User) -> str:
    if user.report_token:
        return user.report_token
    user.report_token = secrets.token_urlsafe(10)
    return user.report_token


def msk_today() -> str:
    return (utc_now() + timedelta(hours=3)).strftime("%Y-%m-%d")


def consume_hint_quota(user: User, limit: int) -> tuple[bool, int]:
    today = msk_today()
    if user.hints_on != today:
        user.hints_on = today
        user.hints_used = 0
    if user.role != "teacher" and user.hints_used >= limit:
        return False, 0
    user.hints_used += 1
    return True, max(0, limit - user.hints_used)


async def weekly_rows(session: AsyncSession, group_chat_id: Optional[str] = None) -> list[dict]:
    since = utc_now() - timedelta(days=7)
    stmt = select(Question)
    if group_chat_id:
        stmt = stmt.where(Question.group_chat_id == str(group_chat_id))
    questions = (await session.execute(stmt)).scalars().all()
    users = {u.max_user_id: u for u in (await session.execute(select(User))).scalars().all()}
    rows = []
    for q in questions:
        created = q.created_at.replace(tzinfo=timezone.utc) if q.created_at and q.created_at.tzinfo is None else q.created_at
        if created and created < since:
            continue
        helper_id = None
        if q.resolved_answer_id:
            ans = (await session.execute(select(Answer).where(Answer.id == q.resolved_answer_id))).scalar_one_or_none()
            if ans:
                helper_id = ans.author_max_id
        author = users.get(q.author_max_id)
        helper = users.get(helper_id) if helper_id else None
        rows.append({
            "date": q.created_at.strftime("%d.%m.%Y %H:%M") if q.created_at else "",
            "subject": q.subject or "Учёба",
            "author": author.display_name if author else q.author_max_id,
            "helper": helper.display_name if helper else "",
            "text": q.text,
            "status": q.status,
            "exemplar": "да" if q.is_exemplar else "нет",
        })
    rows.sort(key=lambda item: item["date"], reverse=True)
    return rows


def weekly_csv(rows: list[dict]) -> str:
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=["date", "subject", "author", "helper", "text", "status", "exemplar"])
    writer.writeheader()
    writer.writerows(rows)
    return buf.getvalue()


def weekly_summary(rows: list[dict]) -> dict:
    subjects: dict[str, int] = {}
    helpers: dict[str, int] = {}
    askers: dict[str, int] = {}
    for row in rows:
        subjects[row["subject"]] = subjects.get(row["subject"], 0) + 1
        if row["author"]:
            askers[row["author"]] = askers.get(row["author"], 0) + 1
        if row["helper"]:
            helpers[row["helper"]] = helpers.get(row["helper"], 0) + 1
    return {
        "questions": len(rows),
        "answered": sum(1 for row in rows if row["status"] == "resolved"),
        "subjects": sorted(subjects.items(), key=lambda x: -x[1]),
        "top_helpers": sorted(helpers.items(), key=lambda x: -x[1])[:5],
        "top_askers": sorted(askers.items(), key=lambda x: -x[1])[:5],
        "rows": rows,
    }
