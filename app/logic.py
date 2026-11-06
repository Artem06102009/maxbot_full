import re
from datetime import datetime, timedelta, timezone
from sqlmodel import Session, select

from app.models import Achievement, ClassChat, Question, User, UserAchievement


STEM_KEYWORDS = (
    "матем", "физик", "хими", "геометр", "алгебр", "уравнен", "формул",
    "информат", "програм", "числ", "степен", "теорем", "задач", "синус",
    "косинус", "вектор", "график",
)
HUMANITIES_KEYWORDS = (
    "литерат", "русск", "истори", "обществ", "сочинен", "стихотвор",
    "граммат", "изложен", "язык", "географ", "биограф",
)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def ensure_aware(dt: datetime | None) -> datetime:
    if dt is None:
        return utc_now()
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def is_night_mode() -> bool:
    msk_time = utc_now() + timedelta(hours=3)
    return msk_time.hour >= 22 or msk_time.hour < 7


SUBJECT_ALIASES = {
    "физика": "Точные науки",
    "математика": "Точные науки",
    "матем": "Точные науки",
    "алгебра": "Точные науки",
    "геометрия": "Точные науки",
    "химия": "Точные науки",
    "информатика": "Точные науки",
    "информат": "Точные науки",
    "русский": "Гуманитарные",
    "литература": "Гуманитарные",
    "лит-ра": "Гуманитарные",
    "история": "Гуманитарные",
    "обществознание": "Гуманитарные",
    "общество": "Гуманитарные",
    "биология": "Учёба",
    "география": "Гуманитарные",
}


def detect_subject(text: str) -> str:
    lowered = (text or "").lower()
    if any(word in lowered for word in STEM_KEYWORDS):
        return "Точные науки"
    if any(word in lowered for word in HUMANITIES_KEYWORDS):
        return "Гуманитарные"
    return "Учёба"


CLASS_SUBJECTS = ("Точные науки", "Гуманитарные", "Учёба")
PHOTO_FALLBACK = "Помогите разобрать задачу с фото."


def parse_ask_text(raw: str) -> tuple[str, str]:
    text = (raw or "").strip()
    if not text:
        return "Учёба", ""
    parts = text.split(maxsplit=1)
    alias = SUBJECT_ALIASES.get(parts[0].lower().strip(".,:"))
    if alias:
        rest = parts[1].strip() if len(parts) > 1 else ""
        return alias, rest
    return detect_subject(text), text


def resolve_subject_name(raw: str) -> str | None:
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


def decode_strict_subjects(chat: ClassChat | None) -> list[str]:
    if not chat:
        return []
    if chat.strict_mode:
        return ["*"]
    raw = (chat.strict_subjects or "").strip()
    if not raw:
        return []
    if raw == "*":
        return ["*"]
    return [part.strip() for part in raw.split(",") if part.strip()]


def is_ai_blocked(chat: ClassChat | None, subject: str) -> bool:
    blocked = decode_strict_subjects(chat)
    if "*" in blocked:
        return True
    return subject in blocked


def encode_strict_subjects(subjects: list[str]) -> tuple[bool, str | None]:
    cleaned = []
    for item in subjects:
        if item == "*":
            return True, None
        if item in CLASS_SUBJECTS and item not in cleaned:
            cleaned.append(item)
    return False, (",".join(cleaned) if cleaned else None)


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


def apply_pending_rewards(user: User) -> bool:
    if is_night_mode():
        return False
    if not user.pending_xp and not user.pending_coins:
        return False
    user.xp += user.pending_xp
    user.coins += user.pending_coins
    user.pending_xp = 0
    user.pending_coins = 0
    return True


def grant_xp_and_coins(user: User, xp: int, coins: int) -> tuple[int, int, bool]:
    deferred = is_night_mode()
    if deferred:
        user.pending_xp += xp
        user.pending_coins += coins
        return xp, coins, True
    user.xp += xp
    user.coins += coins
    return xp, coins, False


def check_and_grant_achievements(
    session: Session,
    user: User,
    fast_help: bool = False,
    subject: str = "",
) -> list[str]:
    newly_unlocked: list[str] = []

    def award_if_not_had(code: str):
        ach = session.exec(select(Achievement).where(Achievement.code == code)).first()
        if not ach:
            return
        existing = session.exec(
            select(UserAchievement).where(
                UserAchievement.user_id == user.id,
                UserAchievement.achievement_id == ach.id,
            )
        ).first()
        if existing:
            return
        session.add(UserAchievement(user_id=user.id, achievement_id=ach.id))
        grant_xp_and_coins(user, ach.xp_reward, ach.coins_reward)
        newly_unlocked.append(
            f"{ach.icon} «{ach.title}» (+{ach.xp_reward} XP, +{ach.coins_reward} спасибок)"
        )

    if user.helped_count >= 1:
        award_if_not_had("first_help")
    if user.helped_count >= 5:
        award_if_not_had("helper_5")
    if user.helped_count >= 20:
        award_if_not_had("helper_20")
    if fast_help:
        award_if_not_had("fast_help")
    if user.stem_helped_count >= 5:
        award_if_not_had("math_expert")
    if user.humanities_helped_count >= 5:
        award_if_not_had("humanities_expert")
    if subject == "Точные науки" and user.stem_helped_count >= 5:
        award_if_not_had("math_expert")
    if subject == "Гуманитарные" and user.humanities_helped_count >= 5:
        award_if_not_had("humanities_expert")

    return newly_unlocked


def find_question_by_reply_mid(session: Session, chat_id: int, mid: str | None) -> Question | None:
    if not mid:
        return None
    hanging = session.exec(
        select(Question).where(Question.chat_id == chat_id, Question.status == "open")
    ).all()
    for q in hanging:
        if mid in (q.ask_mid, q.prompt_mid):
            return q
    return None


def normalize_question_text(text: str) -> str:
    value = (text or "").lower().replace("ё", "е")
    value = re.sub(r"[^\wа-я]+", " ", value, flags=re.IGNORECASE)
    return re.sub(r"\s+", " ", value).strip()


def find_hanging_duplicate(
    session: Session,
    chat_id: int,
    text: str,
    image_url: str | None = None,
) -> Question | None:
    since = utc_now() - timedelta(hours=1)
    norm = normalize_question_text(text)
    photo_norm = normalize_question_text(PHOTO_FALLBACK)
    hanging = session.exec(
        select(Question).where(Question.chat_id == chat_id, Question.status == "open")
    ).all()
    for q in hanging:
        if ensure_aware(q.created_at) < since:
            continue
        if image_url and q.image_url and q.image_url == image_url:
            return q
        if not norm:
            continue
        if normalize_question_text(q.text) != norm:
            continue
        if norm == photo_norm and image_url != q.image_url:
            continue
        return q
    return None


def assign_helper(question: Question, helper: User, author: User | None) -> str:
    if question.status == "answered":
        return "closed"
    if author and author.id == helper.id:
        return "self"
    if question.helper_id == helper.id:
        return "same"
    question.helper_id = helper.id
    return "ok"


def bump_subject_help(user: User, subject: str) -> None:
    if subject == "Точные науки":
        user.stem_helped_count += 1
    elif subject == "Гуманитарные":
        user.humanities_helped_count += 1


def complete_thanks(session: Session, question: Question, author: User, helper: User):
    xp_gained, coins_gained, deferred = grant_xp_and_coins(helper, 15, 5)
    grant_xp_and_coins(author, 2, 0)
    helper.helped_count += 1
    bump_subject_help(helper, question.subject)

    question.status = "answered"
    question.answered_at = utc_now()

    diff_minutes = (ensure_aware(question.answered_at) - ensure_aware(question.created_at)).total_seconds() / 60
    is_fast = diff_minutes <= 15
    unlocked = check_and_grant_achievements(
        session, helper, fast_help=is_fast, subject=question.subject
    )
    return {
        "xp_gained": xp_gained,
        "coins_gained": coins_gained,
        "deferred": deferred,
        "unlocked": unlocked,
        "fast": is_fast,
    }
