import secrets
from typing import Generator, Optional
from sqlalchemy import text
from sqlmodel import SQLModel, create_engine, Session, select
from config import DATABASE_URL
from app.models import User, Achievement, Reward, Question, ClassChat

engine = create_engine(
    DATABASE_URL,
    echo=False,
    connect_args={"check_same_thread": False} if "sqlite" in DATABASE_URL else {}
)


def get_session() -> Generator[Session, None, None]:
    with Session(engine, expire_on_commit=False) as session:
        yield session


def seed_initial_data(session: Session):
    existing_achievements = session.exec(select(Achievement)).all()
    if not existing_achievements:
        default_achievements = [
            Achievement(
                code="first_help",
                title="Первый шаг",
                description="Помочь однокласснику с вопросом в первый раз",
                icon="🌱",
                xp_reward=20,
                coins_reward=10,
            ),
            Achievement(
                code="fast_help",
                title="Скорая помощь",
                description="Ответить и получить 'Спасибо' быстрее чем за 15 минут",
                icon="⚡️",
                xp_reward=30,
                coins_reward=15,
            ),
            Achievement(
                code="helper_5",
                title="Спасатель",
                description="Помочь одноклассникам 5 раз",
                icon="🛟",
                xp_reward=50,
                coins_reward=25,
            ),
            Achievement(
                code="helper_20",
                title="Наставник класса",
                description="Успешно помочь 20 раз",
                icon="🎓",
                xp_reward=150,
                coins_reward=60,
            ),
            Achievement(
                code="math_expert",
                title="Знаток точных наук",
                description="Помочь 5 раз по математике или физике",
                icon="📐",
                xp_reward=40,
                coins_reward=20,
            ),
            Achievement(
                code="humanities_expert",
                title="Гуманитарий",
                description="Помочь 5 раз по литературе, русскому или истории",
                icon="📖",
                xp_reward=40,
                coins_reward=20,
            ),
            Achievement(
                code="teacher_choice",
                title="Выбор учителя",
                description="Получить особое признание учителя за лучшее объяснение темы",
                icon="⭐️",
                xp_reward=100,
                coins_reward=50,
            ),
        ]
        for ach in default_achievements:
            session.add(ach)
        session.commit()

    existing_rewards = session.exec(select(Reward)).all()
    if not existing_rewards:
        default_rewards = [
            Reward(
                title="Освобождение от одного ДЗ",
                description="Право не сдавать одно домашнее задание по согласованию с учителем",
                price_coins=50,
                icon="📝",
            ),
            Reward(
                title="Выбор соседа по парте",
                description="Право выбрать место и соседа по парте на всю следующую неделю",
                price_coins=35,
                icon="🪑",
            ),
            Reward(
                title="Первый выбор темы проекта",
                description="Право первым выбрать тему доклада или учебного проекта",
                price_coins=25,
                icon="🎯",
            ),
            Reward(
                title="Консультация учителя 1-на-1",
                description="15 минут персонального разбора сложной темы с учителем",
                price_coins=40,
                icon="💡",
            ),
        ]
        for rew in default_rewards:
            session.add(rew)
        session.commit()

    existing_users = session.exec(select(User)).all()
    if not existing_users:
        demo_users = [
            User(
                max_user_id=1001,
                name="Аркадий Смирнов",
                username="arkadiy",
                role="student",
                chat_id=1,
                xp=480,
                coins=45,
                helped_count=12,
                questions_count=3,
            ),
            User(
                max_user_id=1002,
                name="Маша Ковалева",
                username="masha_k",
                role="student",
                chat_id=1,
                xp=290,
                coins=30,
                helped_count=7,
                questions_count=6,
            ),
            User(
                max_user_id=1003,
                name="Иван Петров",
                username="ivan_p",
                role="student",
                chat_id=1,
                xp=110,
                coins=15,
                helped_count=3,
                questions_count=5,
            ),
            User(
                max_user_id=9999,
                name="Мария Ивановна (Учитель)",
                username="teacher_maria",
                role="teacher",
                chat_id=1,
                xp=0,
                coins=0,
                helped_count=0,
                questions_count=0,
            ),
        ]
        for u in demo_users:
            session.add(u)
        session.commit()

    _seed_demo_questions(session)
    exemplar = session.exec(select(Question).where(Question.is_exemplar == True)).first()  # noqa: E712
    if not exemplar:
        answered = session.exec(select(Question).where(Question.status == "answered")).first()
        if answered:
            answered.is_exemplar = True
            answered.exemplar_note = answered.exemplar_note or "Учитель отметил этот разбор как эталон для шпаргалки класса."
            session.add(answered)
            session.commit()
    if not session.exec(select(ClassChat)).first():
        session.add(ClassChat(max_chat_id=1, title="11 «А»"))
        session.commit()


def _seed_demo_questions(session: Session):
    if session.exec(select(Question)).first():
        return
    arkadiy = session.exec(select(User).where(User.max_user_id == 1001)).first()
    masha = session.exec(select(User).where(User.max_user_id == 1002)).first()
    ivan = session.exec(select(User).where(User.max_user_id == 1003)).first()
    if not (arkadiy and masha and ivan):
        return
    session.add(Question(
        chat_id=1,
        author_id=ivan.id,
        subject="Точные науки",
        text="Как решить №245 по геометрии? Не могу понять теорему Пифагора в этой задаче.",
        status="open",
    ))
    session.add(Question(
        chat_id=1,
        author_id=masha.id,
        subject="Гуманитарные",
        text="Кто может помочь с сочинением по «Капитанской дочке»? Нужен план.",
        status="open",
    ))
    session.add(Question(
        chat_id=1,
        author_id=arkadiy.id,
        subject="Точные науки",
        text="Объясните, пожалуйста, как найти производную сложной функции.",
        status="answered",
        helper_id=masha.id,
        is_exemplar=True,
        exemplar_note="Производная сложной функции: сначала внешняя, потом внутренняя. (f(g(x)))' = f'(g(x)) · g'(x).",
    ))
    session.commit()


def create_db_and_tables():
    SQLModel.metadata.create_all(engine)
    _migrate_sqlite_columns()
    with Session(engine) as session:
        seed_initial_data(session)


def _migrate_sqlite_columns():
    if "sqlite" not in DATABASE_URL:
        return
    with engine.connect() as conn:
        cols = {row[1] for row in conn.execute(text("PRAGMA table_info(user)")).fetchall()}
        user_cols = {
            "pending_xp": "INTEGER DEFAULT 0",
            "pending_coins": "INTEGER DEFAULT 0",
            "stem_helped_count": "INTEGER DEFAULT 0",
            "humanities_helped_count": "INTEGER DEFAULT 0",
            "hints_used": "INTEGER DEFAULT 0",
            "hints_on": "TEXT",
            "report_token": "TEXT",
        }
        for col, ddl in user_cols.items():
            if col not in cols:
                conn.execute(text(f"ALTER TABLE user ADD COLUMN {col} {ddl}"))
        conn.execute(text("UPDATE user SET chat_id = 1 WHERE chat_id IS NULL"))

        tables = {row[0] for row in conn.execute(text("SELECT name FROM sqlite_master WHERE type='table'")).fetchall()}
        if "question" in tables:
            q_cols = {row[1] for row in conn.execute(text("PRAGMA table_info(question)")).fetchall()}
            question_alter = {
                "image_url": "TEXT",
                "is_exemplar": "INTEGER DEFAULT 0",
                "exemplar_note": "TEXT",
                "nudge_sent_at": "DATETIME",
                "ask_mid": "TEXT",
                "prompt_mid": "TEXT",
            }
            for col, ddl in question_alter.items():
                if col not in q_cols:
                    conn.execute(text(f"ALTER TABLE question ADD COLUMN {col} {ddl}"))
        if "classchat" in tables:
            c_cols = {row[1] for row in conn.execute(text("PRAGMA table_info(classchat)")).fetchall()}
            chat_alter = {
                "strict_mode": "INTEGER DEFAULT 0",
                "strict_subjects": "TEXT",
                "last_digest_on": "TEXT",
            }
            for col, ddl in chat_alter.items():
                if col not in c_cols:
                    conn.execute(text(f"ALTER TABLE classchat ADD COLUMN {col} {ddl}"))
        conn.commit()


def get_or_create_user(
    max_user_id: int,
    name: str = "Ученик",
    username: Optional[str] = None,
    chat_id: Optional[int] = None,
    session: Optional[Session] = None,
) -> User:
    own_session = session is None
    sess = session or Session(engine, expire_on_commit=False)
    try:
        statement = select(User).where(User.max_user_id == max_user_id)
        user = sess.exec(statement).first()
        if not user:
            user = User(
                max_user_id=max_user_id,
                name=name or "Ученик",
                username=username,
                chat_id=chat_id,
                role="student",
                xp=0,
                coins=10,
                report_token=secrets.token_urlsafe(10),
            )
            sess.add(user)
            sess.commit()
            sess.refresh(user)
        else:
            updated = False
            if name and name != "Ученик" and user.name != name:
                user.name = name
                updated = True
            if username and user.username != username:
                user.username = username
                updated = True
            if chat_id and user.chat_id != chat_id:
                user.chat_id = chat_id
                updated = True
            if updated:
                sess.add(user)
                sess.commit()
                sess.refresh(user)
        if own_session:
            sess.expunge(user)
        return user
    finally:
        if own_session:
            sess.close()


def ensure_class_chat(max_chat_id: int, title: str = "Чат класса") -> ClassChat:
    with Session(engine, expire_on_commit=False) as session:
        chat = session.exec(select(ClassChat).where(ClassChat.max_chat_id == max_chat_id)).first()
        if not chat:
            chat = ClassChat(max_chat_id=max_chat_id, title=title or "Чат класса")
            session.add(chat)
            session.commit()
            session.refresh(chat)
        session.expunge(chat)
        return chat
