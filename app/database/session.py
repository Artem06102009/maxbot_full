import os
from sqlalchemy import func, inspect, select, text
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from app.config import get_settings
from app.database.models import (
    Base,
    Achievement,
    Question,
    Reward,
    User,
    DEFAULT_ACHIEVEMENTS,
)

settings = get_settings()

db_url = settings.DATABASE_URL
if db_url.startswith("sqlite+aiosqlite:///"):
    path = db_url.replace("sqlite+aiosqlite:///", "")
    dir_name = os.path.dirname(path)
    if dir_name:
        os.makedirs(dir_name, exist_ok=True)
elif db_url.startswith("sqlite:///"):
    path = db_url.replace("sqlite:///", "")
    dir_name = os.path.dirname(path)
    if dir_name:
        os.makedirs(dir_name, exist_ok=True)

engine = create_async_engine(db_url)
async_session_factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

def _migrate_sqlite(sync_conn):
    insp = inspect(sync_conn)
    tables = set(insp.get_table_names())
    if "users" in tables:
        cols = {c["name"] for c in insp.get_columns("users")}
        for col, ddl in {
            "report_token": "TEXT",
            "hints_used": "INTEGER DEFAULT 0",
            "hints_on": "TEXT",
            "pending_xp": "INTEGER DEFAULT 0",
            "pending_coins": "INTEGER DEFAULT 0",
            "help_streak": "INTEGER DEFAULT 0",
            "streak_best": "INTEGER DEFAULT 0",
            "last_help_on": "TEXT",
        }.items():
            if col not in cols:
                sync_conn.execute(text(f"ALTER TABLE users ADD COLUMN {col} {ddl}"))
    if "questions" in tables:
        cols = {c["name"] for c in insp.get_columns("questions")}
        for col, ddl in {
            "ask_mid": "TEXT",
            "image_url": "TEXT",
            "is_exemplar": "INTEGER DEFAULT 0",
            "exemplar_note": "TEXT",
            "nudge_sent_at": "DATETIME",
        }.items():
            if col not in cols:
                sync_conn.execute(text(f"ALTER TABLE questions ADD COLUMN {col} {ddl}"))
    if "answers" in tables:
        cols = {c["name"] for c in insp.get_columns("answers")}
        if "quality" not in cols:
            sync_conn.execute(text("ALTER TABLE answers ADD COLUMN quality TEXT"))
    if "rewards" in tables:
        cols = {c["name"] for c in insp.get_columns("rewards")}
        for col, ddl in {
            "max_per_student": "INTEGER DEFAULT 1",
            "stock": "INTEGER",
            "group_chat_id": "TEXT",
        }.items():
            if col not in cols:
                sync_conn.execute(text(f"ALTER TABLE rewards ADD COLUMN {col} {ddl}"))
    if "class_settings" in tables:
        cols = {c["name"] for c in insp.get_columns("class_settings")}
        for col, ddl in {
            "title": "TEXT",
            "last_digest_on": "TEXT",
            "exam_until": "TEXT",
            "exam_subjects": "TEXT",
            "digest_enabled": "INTEGER DEFAULT 1",
            "teacher_max_id": "TEXT",
            "setup_done": "INTEGER DEFAULT 0",
        }.items():
            if col not in cols:
                sync_conn.execute(text(f"ALTER TABLE class_settings ADD COLUMN {col} {ddl}"))


async def init_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.run_sync(_migrate_sqlite)

    async with async_session_factory() as session:
        res = await session.execute(select(Achievement).limit(1))
        if res.scalar_one_or_none() is None:
            for item in DEFAULT_ACHIEVEMENTS:
                session.add(Achievement(**item))

            demo_users = [
                User(max_user_id="1001", display_name="Аркадий", role="student", balance_spasibki=18, xp=480, rank="Знаток"),
                User(max_user_id="1002", display_name="Маша", role="student", balance_spasibki=9, xp=290, rank="Знаток"),
                User(max_user_id="1003", display_name="Иван", role="student", balance_spasibki=3, xp=110, rank="Любитель"),
                User(max_user_id="9999", display_name="Мария Ивановна", role="teacher", balance_spasibki=0, xp=0, rank="Учитель"),
                User(max_user_id="teacher_1", display_name="Учитель", role="teacher", balance_spasibki=0, xp=0, rank="Учитель"),
            ]
            for u in demo_users:
                session.add(u)

            demo_rewards = [
                Reward(created_by_teacher_id="9999", title="Освобождение от одного ДЗ", description="Можно сдать одно домашнее задание позже или не сдавать", cost_spasibki=50, is_active=True),
                Reward(created_by_teacher_id="9999", title="Выбор соседа по парте", description="Возможность пересесть на неделю к выбранному однокласснику", cost_spasibki=30, is_active=True),
                Reward(created_by_teacher_id="9999", title="Консультация с учителем", description="30 минут индивидуального разбора трудной темы перед контрольной", cost_spasibki=20, is_active=True),
                Reward(created_by_teacher_id="9999", title="Своя тема проекта", description="Право выбрать нестандартную тему для школьного проекта", cost_spasibki=40, is_active=True),
            ]
            for r in demo_rewards:
                session.add(r)

            session.add(Question(
                group_chat_id="1",
                group_message_id="demo-exemplar",
                author_max_id="1002",
                text="Кто может помочь с сочинением по «Капитанской дочке»? Нужен план.",
                subject="Гуманитарные",
                status="resolved",
                is_exemplar=True,
                exemplar_note="План: экспозиция — встреча с вожатым; завязка — бунт; кульминация — милость Пугачёва; финал — честь Гринева.",
            ))
            await session.commit()

        open_n = (await session.execute(select(func.count()).select_from(Question).where(Question.status == "open"))).scalar_one()
        if not open_n:
            session.add(Question(
                group_chat_id="1",
                group_message_id="demo-open-geom",
                author_max_id="1003",
                text="Как решить №245 по геометрии? Не могу понять теорему Пифагора в этой задаче.",
                subject="Точные науки",
                status="open",
            ))
            session.add(Question(
                group_chat_id="1",
                group_message_id="demo-open-math",
                author_max_id="1001",
                text="Кто поможет с уравнением по математике?",
                subject="Точные науки",
                status="open",
            ))
            await session.commit()

        existing_codes = {a.code for a in (await session.execute(select(Achievement))).scalars().all()}
        added = False
        for item in DEFAULT_ACHIEVEMENTS:
            if item["code"] not in existing_codes:
                session.add(Achievement(**item))
                added = True
        if added:
            await session.commit()

async def get_db_session():
    async with async_session_factory() as session:
        yield session

