from datetime import datetime, timezone
from typing import List, Optional
from sqlmodel import Field, SQLModel, UniqueConstraint

class Chat(SQLModel, table=True):
    __tablename__ = "chats"
    id: Optional[int] = Field(default=None, primary_key=True)
    max_chat_id: str = Field(index=True, unique=True)
    title: str
    owner_id: Optional[str] = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

class ChatMember(SQLModel, table=True):
    __tablename__ = "chat_members"
    __table_args__ = (UniqueConstraint("chat_max_id", "user_max_id", name="uq_chat_members_chat_user"),)
    id: Optional[int] = Field(default=None, primary_key=True)
    chat_max_id: str = Field(index=True)
    user_max_id: str = Field(index=True)
    role: str = Field(default="student")

class User(SQLModel, table=True):
    __tablename__ = "users"
    id: Optional[int] = Field(default=None, primary_key=True)
    max_user_id: str = Field(index=True, unique=True)
    display_name: str
    role: str = Field(default="student")
    balance_spasibki: int = Field(default=0)
    xp: int = Field(default=0)
    rank: str = Field(default="Новичок")
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

class Question(SQLModel, table=True):
    __tablename__ = "questions"
    id: Optional[int] = Field(default=None, primary_key=True)
    group_chat_id: str = Field(index=True)
    group_chat_title: Optional[str] = None
    group_message_id: str = Field(index=True)
    user_message_id: Optional[str] = Field(default=None, index=True)
    author_max_id: str = Field(index=True)
    author_name: Optional[str] = None
    text: str
    subject: Optional[str] = None
    status: str = Field(default="open")
    resolved_answer_id: Optional[int] = Field(default=None, foreign_key="answers.id")
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    closed_at: Optional[datetime] = None

class Answer(SQLModel, table=True):
    __tablename__ = "answers"
    id: Optional[int] = Field(default=None, primary_key=True)
    question_id: int = Field(foreign_key="questions.id", index=True)
    author_max_id: str = Field(index=True)
    author_name: Optional[str] = None
    group_message_id: str = Field(index=True)
    dm_approval_message_id: Optional[str] = None
    text: str
    status: str = Field(default="pending")
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

class Reward(SQLModel, table=True):
    __tablename__ = "rewards"
    id: Optional[int] = Field(default=None, primary_key=True)
    created_by_teacher_id: str = Field(index=True)
    chat_id: Optional[str] = Field(default=None, index=True)
    title: str
    description: str
    cost_spasibki: int
    is_active: bool = Field(default=True)

class RewardClaim(SQLModel, table=True):
    __tablename__ = "reward_claims"
    id: Optional[int] = Field(default=None, primary_key=True)
    reward_id: int = Field(foreign_key="rewards.id", index=True)
    student_max_id: str = Field(index=True)
    chat_id: Optional[str] = Field(default=None, index=True)
    status: str = Field(default="requested")
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

class Achievement(SQLModel, table=True):
    __tablename__ = "achievements"
    id: Optional[int] = Field(default=None, primary_key=True)
    code: str = Field(index=True, unique=True)
    name_ru: str
    description_ru: str
    icon_url: Optional[str] = None

class UserAchievement(SQLModel, table=True):
    __tablename__ = "user_achievements"
    __table_args__ = (UniqueConstraint("user_max_id", "achievement_id", name="uq_user_achievements_user_achievement"),)
    id: Optional[int] = Field(default=None, primary_key=True)
    user_max_id: str = Field(index=True)
    achievement_id: int = Field(foreign_key="achievements.id", index=True)
    awarded_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

class AuditLog(SQLModel, table=True):
    __tablename__ = "audit_logs"
    id: Optional[int] = Field(default=None, primary_key=True)
    actor_max_id: str = Field(index=True)
    target_user_max_id: Optional[str] = Field(default=None, index=True)
    target_type: Optional[str] = None
    target_id: Optional[str] = None
    action: str = Field(index=True)
    details: Optional[str] = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

DEFAULT_ACHIEVEMENTS = [
    {"code": "first_step", "name_ru": "Первый шаг", "description_ru": "Первый принятый лучший ответ", "icon_url": "🎯"},
    {"code": "fast_aid", "name_ru": "Скорая помощь", "description_ru": "Принятый ответ в течение 10 минут после вопроса", "icon_url": "⚡️"},
    {"code": "rescuer", "name_ru": "Спасатель", "description_ru": "Принятый ответ на вопрос, висевший более 24 часов", "icon_url": "🛟"},
    {"code": "marathoner", "name_ru": "Марафонец", "description_ru": "5 лучших ответов за один день", "icon_url": "🏃‍♂️"},
    {"code": "week_streak", "name_ru": "Неделя в строю", "description_ru": "Лучшие ответы 7 дней подряд", "icon_url": "🔥"},
    {"code": "mentor", "name_ru": "Наставник", "description_ru": "Оказана помощь 10 разным одноклассникам", "icon_url": "🧠"},
    {"code": "subject_expert", "name_ru": "Знаток предмета", "description_ru": "10 принятых ответов по одному предмету", "icon_url": "📐"},
    {"code": "universal", "name_ru": "Универсал", "description_ru": "Принятые ответы по 5 разным предметам", "icon_url": "🌐"},
    {"code": "class_legend", "name_ru": "Легенда класса", "description_ru": "Суммарно 100 благодарностей", "icon_url": "👑"},
    {"code": "brave_question", "name_ru": "Смелый вопрос", "description_ru": "Первый заданный вопрос в чате класса", "icon_url": "🙋‍♂️"},
    {"code": "curious", "name_ru": "Любознательный", "description_ru": "10 заданных вопросов", "icon_url": "🔍"},
    {"code": "grateful", "name_ru": "Благодарный", "description_ru": "Отмечен лучший ответ в 10 своих вопросах", "icon_url": "🤝"},
    {"code": "week_leader", "name_ru": "Лидер недели", "description_ru": "1-е место в недельном рейтинге класса", "icon_url": "🏆"},
    {"code": "teacher_choice", "name_ru": "Выбор учителя", "description_ru": "Специальная награда от педагога", "icon_url": "⭐️"},
]
