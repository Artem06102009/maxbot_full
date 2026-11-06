from datetime import datetime, timezone
from typing import Optional
from sqlmodel import Field, SQLModel


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class User(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    max_user_id: int = Field(index=True, unique=True)
    chat_id: Optional[int] = Field(default=None)
    name: str = Field(default="Ученик")
    username: Optional[str] = Field(default=None)
    role: str = Field(default="student")
    xp: int = Field(default=0)
    coins: int = Field(default=0)
    pending_xp: int = Field(default=0)
    pending_coins: int = Field(default=0)
    helped_count: int = Field(default=0)
    questions_count: int = Field(default=0)
    stem_helped_count: int = Field(default=0)
    humanities_helped_count: int = Field(default=0)
    hints_used: int = Field(default=0)
    hints_on: Optional[str] = Field(default=None)
    report_token: Optional[str] = Field(default=None, index=True, unique=True)
    created_at: datetime = Field(default_factory=utc_now)


class ClassChat(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    max_chat_id: int = Field(index=True, unique=True)
    title: str = Field(default="Чат класса")
    strict_mode: bool = Field(default=False)
    strict_subjects: Optional[str] = Field(default=None)
    last_digest_on: Optional[str] = Field(default=None)
    created_at: datetime = Field(default_factory=utc_now)


class Question(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    chat_id: int = Field(index=True)
    author_id: int = Field(foreign_key="user.id")
    subject: str = Field(default="Учёба")
    text: str
    image_url: Optional[str] = Field(default=None)
    status: str = Field(default="open")
    helper_id: Optional[int] = Field(default=None, foreign_key="user.id")
    is_exemplar: bool = Field(default=False)
    exemplar_note: Optional[str] = Field(default=None)
    nudge_sent_at: Optional[datetime] = Field(default=None)
    ask_mid: Optional[str] = Field(default=None, index=True)
    prompt_mid: Optional[str] = Field(default=None, index=True)
    created_at: datetime = Field(default_factory=utc_now)
    answered_at: Optional[datetime] = Field(default=None)


class Achievement(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    code: str = Field(unique=True, index=True)
    title: str
    description: str
    icon: str = Field(default="🏆")
    xp_reward: int = Field(default=20)
    coins_reward: int = Field(default=10)


class UserAchievement(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="user.id", index=True)
    achievement_id: int = Field(foreign_key="achievement.id")
    unlocked_at: datetime = Field(default_factory=utc_now)


class Reward(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    chat_id: Optional[int] = Field(default=None)
    title: str
    description: str
    price_coins: int = Field(default=50)
    icon: str = Field(default="🎁")
    is_active: bool = Field(default=True)
    created_by_teacher_id: Optional[int] = Field(default=None, foreign_key="user.id")


class RewardRequest(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="user.id")
    reward_id: int = Field(foreign_key="reward.id")
    status: str = Field(default="pending")
    created_at: datetime = Field(default_factory=utc_now)
    reviewed_at: Optional[datetime] = Field(default=None)


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
    current_level = LEVELS[0]
    next_level = None

    for i, lvl in enumerate(LEVELS):
        if xp >= lvl["xp"]:
            current_level = lvl
            if i + 1 < len(LEVELS):
                next_level = LEVELS[i + 1]
            else:
                next_level = None
        else:
            break

    if next_level:
        needed = next_level["xp"] - current_level["xp"]
        gained = xp - current_level["xp"]
        progress_percent = max(0, min(100, int((gained / needed) * 100)))
        next_xp = next_level["xp"]
    else:
        progress_percent = 100
        next_xp = current_level["xp"]

    return {
        "level": current_level["level"],
        "title": current_level["title"],
        "current_xp": xp,
        "next_xp": next_xp,
        "progress_percent": progress_percent,
    }
