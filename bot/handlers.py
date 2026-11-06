from sqlmodel import Session, select, desc
from maxapi.enums.chat_type import ChatType
from maxapi.filters.command import Command, CommandStart
from maxapi.types import (
    MessageCreated, MessageCallback, BotStarted, BotAdded,
    CallbackButton, LinkButton, OpenAppButton
)
from maxapi.utils.inline_keyboard import InlineKeyboardBuilder

from config import BASE_URL, BOT_USERNAME, HINT_DAILY_LIMIT, SECRET_TEACHER_CODE
from bot.main import bot, dp
from app.database import engine, ensure_class_chat, get_or_create_user
from app.gigachat import download_url, make_hint
from app.jobs import send_class_digest
from app.logic import (
    PHOTO_FALLBACK,
    apply_pending_rewards,
    assign_helper,
    complete_thanks,
    consume_hint_quota,
    decode_strict_subjects,
    encode_strict_subjects,
    find_hanging_duplicate,
    find_question_by_reply_mid,
    is_ai_blocked,
    is_night_mode,
    parse_ask_text,
    resolve_subject_name,
)
from app.reports import ensure_report_token, report_url
from app.models import ClassChat, User, Question, calculate_level


def extract_image_url(event: MessageCreated) -> str | None:
    attachments = event.message.body.attachments or []
    for att in attachments:
        att_type = str(getattr(att, "type", "")).lower()
        if "image" not in att_type and "photo" not in att_type:
            continue
        payload = getattr(att, "payload", None)
        url = getattr(payload, "url", None) if payload else None
        if url:
            return url
    return None


def question_keyboard(q_id: int, user_id: int) -> list:
    b = InlineKeyboardBuilder()
    b.add(CallbackButton(text="💡 Я помог", payload=f"helped:{q_id}"))
    b.add(CallbackButton(text="⭐️ Сказать спасибо", payload=f"thanks:{q_id}"))
    b.row(CallbackButton(text="🤖 Подсказка ГигаЧат", payload=f"gigahint:{q_id}"))
    if BOT_USERNAME:
        b.row(OpenAppButton(text="Профиль и лавка", web_app=BOT_USERNAME, payload=str(user_id)))
    else:
        b.row(LinkButton(text="Профиль и лавка", url=f"{BASE_URL}/?user_id={user_id}"))
    return [b.as_markup()]


def make_webapp_keyboard(user_id: int) -> list:
    b = InlineKeyboardBuilder()
    if BOT_USERNAME:
        b.add(OpenAppButton(
            text="Открыть Лигу Помощников",
            web_app=BOT_USERNAME,
            payload=str(user_id),
        ))
    else:
        b.add(LinkButton(text="Открыть Лигу Помощников", url=f"{BASE_URL}/?user_id={user_id}"))
    return [b.as_markup()]


def is_class_chat(event: MessageCreated) -> bool:
    recipient = event.message.recipient
    return recipient.chat_type == ChatType.CHAT and bool(recipient.chat_id)


def class_chat_id(event: MessageCreated) -> int | None:
    if is_class_chat(event):
        return event.message.recipient.chat_id
    return None


def bind_user_from_event(event: MessageCreated, session: Session | None = None) -> User:
    sender = event.message.sender
    group_id = class_chat_id(event)
    return get_or_create_user(
        max_user_id=sender.user_id,
        name=sender.first_name or sender.username or "Ученик",
        username=sender.username,
        chat_id=group_id,
        session=session,
    )


ASK_HINT = (
    "❓ Вопросы задают **в чате класса** в MAX.\n\n"
    "1. Добавьте бота в групповой чат класса\n"
    "2. Напишите там:\n"
    "`/ask Как решить №245 по геометрии?`\n\n"
    "Одноклассники отвечают **реплаем** на карточку вопроса "
    "(кнопка «Я помог» тоже работает), "
    "а автор — **«Сказать спасибо»**. За это капают XP и спасибки."
)


@dp.bot_added()
async def on_bot_added(event: BotAdded):
    if event.is_channel:
        return

    ensure_class_chat(event.chat_id, "Чат класса")
    text = (
        "🎓 **Лига Помощников подключена к чату класса.**\n\n"
        "Теперь вопросы по учёбе задаём прямо здесь, как в классном чате:\n\n"
        "• `/ask [вопрос]` — или фото задачи с подписью `/ask`\n"
        "• `/ask физика …` — сразу указать предмет\n"
        "• Ответ **реплаем** на карточку вопроса (или кнопка «Я помог») / **«Сказать спасибо»**\n"
        "• **«Подсказка ГигаЧат»** — намёк, не готовый ответ (3 раза в день)\n"
        "• `/me` · `/top` · `/help`\n\n"
        "Мини-приложение нужно для профиля, ачивок и школьной лавки, "
        "а сами вопросы живут в этом чате."
    )
    await bot.send_message(chat_id=event.chat_id, text=text)


@dp.bot_started()
async def on_bot_started(event: BotStarted):
    user_id = event.user.user_id if event.user else 0
    name = event.user.name if event.user else "Ученик"
    username = event.user.username if event.user else None
    user = get_or_create_user(max_user_id=user_id, name=name, username=username)

    welcome_text = (
        f"👋 Привет, {user.name}!\n\n"
        "Я бот **«Лига Помощников»** для чата класса в MAX.\n\n"
        f"{ASK_HINT}\n\n"
        "Профиль, рейтинг и лавка — в мини-приложении 👇"
    )
    await bot.send_message(
        chat_id=event.chat_id,
        text=welcome_text,
        attachments=make_webapp_keyboard(user.max_user_id)
    )


@dp.message_created(CommandStart())
async def cmd_start(event: MessageCreated):
    user = bind_user_from_event(event)
    if is_class_chat(event):
        ensure_class_chat(class_chat_id(event))
        text = (
            f"👋 {user.name}, бот уже в чате класса.\n\n"
            "Задайте вопрос одноклассникам:\n"
            "`/ask Кто понял задачу №3 по физике?`\n\n"
            "Ответьте **реплаем** на карточку вопроса — так бот поймёт, кто помог."
        )
    else:
        text = (
            f"👋 Привет, {user.name}!\n\n"
            f"{ASK_HINT}\n\n"
            "📌 Другие команды: `/me` · `/top` · `/help` · `/teacher [код]`"
        )
    await event.message.answer(text=text, attachments=make_webapp_keyboard(user.max_user_id))


@dp.message_created(Command("help"))
async def cmd_help(event: MessageCreated):
    text = (
        "📖 **Как работает Лига в чате класса**\n\n"
        "1️⃣ Добавьте бота в групповой чат класса в MAX.\n\n"
        "2️⃣ Ученик пишет:\n"
        "`/ask физика Как найти силу трения?`\n"
        "Можно прикрепить **фото задачи** и в подписи написать `/ask`.\n\n"
        "3️⃣ Одноклассник отвечает **реплаем** на карточку вопроса (можно без кнопки).\n"
        "4️⃣ Автор жмёт **«Сказать спасибо»** — помощник получает **+15 XP**.\n\n"
        "🤖 Если никто не понимает: **«Подсказка ГигаЧат»** — короткий намёк, не решение. "
        f"Лимит {HINT_DAILY_LIMIT} подсказки в день. За неё XP нет.\n\n"
        "Если вопрос висит 30 минут, бот напомнит классу.\n"
        "Учитель: `/strict физика` — без ИИ по теме, `/digest` — итог недели.\n\n"
        "🌙 Тихий час 22:00–07:00: опыт начислится утром."
    )
    await event.message.answer(text=text)


@dp.message_created(Command("me"))
async def cmd_me(event: MessageCreated):
    sender = event.message.sender
    with Session(engine, expire_on_commit=False) as session:
        user = bind_user_from_event(event, session=session)
        if apply_pending_rewards(user):
            session.add(user)
            session.commit()
            session.refresh(user)
        ensure_report_token(user)
        session.add(user)
        session.commit()
        level_info = calculate_level(user.xp)
        snapshot = {
            "name": user.name,
            "role": user.role,
            "xp": user.xp,
            "coins": user.coins,
            "helped_count": user.helped_count,
            "questions_count": user.questions_count,
            "pending_xp": user.pending_xp,
            "max_user_id": user.max_user_id,
            "in_class": bool(user.chat_id),
            "report_url": report_url(user),
        }

    pct = level_info["progress_percent"]
    filled = int(pct / 10)
    bar = "█" * filled + "░" * (10 - filled)
    role_str = "Учитель" if snapshot["role"] == "teacher" else "Ученик"
    pending_line = f"🌙 Отложено до утра: {snapshot['pending_xp']} XP\n" if snapshot["pending_xp"] else ""
    class_line = "🏫 Чат класса подключен\n" if snapshot["in_class"] else "⚠️ Бот ещё не добавлен в чат класса\n"

    text = (
        f"👤 **{snapshot['name']}** ({role_str})\n"
        f"{class_line}"
        f"🎖 {level_info['title']} · ур. {level_info['level']}\n"
        f"⚡️ {snapshot['xp']} / {level_info['next_xp']} XP\n"
        f"[{bar}] {pct}%\n"
        f"{pending_line}"
        f"🪙 Спасибки: {snapshot['coins']}\n"
        f"🤝 Помог: {snapshot['helped_count']} · ❓ Вопросов: {snapshot['questions_count']}\n"
        f"👨‍👩‍👧 Для родителя (неделя): {snapshot['report_url']}"
    )
    await event.message.answer(text=text, attachments=make_webapp_keyboard(snapshot["max_user_id"]))


@dp.message_created(Command("top"))
async def cmd_top(event: MessageCreated):
    group_id = class_chat_id(event)
    with Session(engine) as session:
        query = select(User).where(User.role == "student")
        if group_id:
            query = query.where(User.chat_id == group_id)
        top_students = session.exec(query.order_by(desc(User.xp)).limit(5)).all()
        lines = ["🏆 **Топ помощников класса:**\n"]
        medals = ["🥇", "🥈", "🥉", "4️⃣", "5️⃣"]
        if not top_students:
            lines.append("Пока никто не помогал. Напишите `/ask …` в чате класса.")
        for idx, student in enumerate(top_students):
            lvl = calculate_level(student.xp)
            lines.append(f"{medals[idx]} **{student.name}** — {student.xp} XP ({lvl['title']})")

    sender_id = event.message.sender.user_id
    await event.message.answer(text="\n".join(lines), attachments=make_webapp_keyboard(sender_id))


@dp.message_created(Command("teacher"))
async def cmd_teacher(event: MessageCreated):
    text_body = event.message.body.text or ""
    parts = text_body.strip().split(maxsplit=1)
    if len(parts) < 2:
        await event.message.answer(text="Использование: `/teacher [секретный_код]`")
        return

    if parts[1].strip() != SECRET_TEACHER_CODE:
        await event.message.answer(text="❌ Неверный секретный код учителя!")
        return

    sender = event.message.sender
    with Session(engine, expire_on_commit=False) as session:
        user = bind_user_from_event(event, session=session)
        user.role = "teacher"
        session.add(user)
        session.commit()

    await event.message.answer(
        text=(
            f"🎉 {sender.first_name}, вам назначена роль учителя.\n"
            "В мини-приложении откроется вкладка «Учительская»."
        ),
        attachments=make_webapp_keyboard(sender.user_id)
    )


@dp.message_created(Command("strict"))
async def cmd_strict(event: MessageCreated):
    if not is_class_chat(event):
        await event.message.answer(text="Строгий режим включают в чате класса.")
        return
    with Session(engine, expire_on_commit=False) as session:
        user = bind_user_from_event(event, session=session)
        if user.role != "teacher":
            await event.message.answer(text="Только учитель может включать строгий режим.")
            return
        parts = (event.message.body.text or "").split()[1:]
        chat = session.exec(select(ClassChat).where(ClassChat.max_chat_id == class_chat_id(event))).first()
        if not chat:
            chat = ClassChat(max_chat_id=class_chat_id(event), title="Чат класса")
        token = parts[0].lower() if parts else "on"
        if token in ("off", "выкл", "0", "нет"):
            chat.strict_mode = False
            chat.strict_subjects = None
            label = "ГигаЧат снова доступен по всем темам."
        elif token in ("on", "вкл", "все", "all"):
            chat.strict_mode = True
            chat.strict_subjects = None
            label = "ГигаЧат выключен **по всем предметам**. Только одноклассники."
        else:
            subjects = []
            for part in parts:
                resolved = resolve_subject_name(part)
                if resolved == "*":
                    subjects = ["*"]
                    break
                if resolved and resolved not in subjects:
                    subjects.append(resolved)
            if not subjects:
                await event.message.answer(
                    text="Использование: `/strict физика` · `/strict on` · `/strict off`"
                )
                return
            all_on, encoded = encode_strict_subjects(subjects)
            chat.strict_mode = all_on
            chat.strict_subjects = encoded
            shown = "все предметы" if all_on else ", ".join(decode_strict_subjects(chat))
            label = f"ГигаЧат выключен для: **{shown}**. По остальным темам подсказки остаются."
        session.add(chat)
        session.commit()
    await event.message.answer(text=f"🔒 {label}")


@dp.message_created(Command("digest"))
async def cmd_digest(event: MessageCreated):
    with Session(engine, expire_on_commit=False) as session:
        user = bind_user_from_event(event, session=session)
        if user.role != "teacher":
            await event.message.answer(text="Дайджест может запросить учитель.")
            return
        chat_id = class_chat_id(event) or user.chat_id
        if not chat_id:
            await event.message.answer(text="Сначала добавьте бота в чат класса.")
            return
    text = await send_class_digest(chat_id)
    if not is_class_chat(event):
        await event.message.answer(text=text)


@dp.message_created(Command("ask"))
async def cmd_ask(event: MessageCreated):
    if not is_class_chat(event):
        await event.message.answer(text=ASK_HINT)
        return

    text_body = event.message.body.text or ""
    parts = text_body.strip().split(maxsplit=1)
    raw_question = parts[1].strip() if len(parts) > 1 else ""
    image_url = extract_image_url(event)
    subject, question_text = parse_ask_text(raw_question)
    if not question_text and image_url:
        question_text = PHOTO_FALLBACK
        if subject == "Учёба":
            subject = "Точные науки"
    if not question_text:
        await event.message.answer(
            text=(
                "Напишите вопрос после команды или приложите фото с подписью `/ask`:\n"
                "`/ask физика Как найти силу трения?`"
            )
        )
        return

    sender = event.message.sender
    chat_id = class_chat_id(event)
    ensure_class_chat(chat_id)
    ask_mid = event.message.body.mid if event.message.body else None

    with Session(engine, expire_on_commit=False) as session:
        user = bind_user_from_event(event, session=session)
        duplicate = find_hanging_duplicate(session, chat_id, question_text, image_url)
        if duplicate:
            await event.message.answer(
                text=(
                    "⏳ Этот вопрос уже висит.\n\n"
                    f"«{duplicate.text}»\n\n"
                    "Ответьте **реплаем** на карточку выше — второй `/ask` не нужен."
                ),
                attachments=question_keyboard(duplicate.id, sender.user_id),
            )
            return
        user.questions_count += 1
        q = Question(
            chat_id=chat_id,
            author_id=user.id,
            subject=subject,
            text=question_text,
            image_url=image_url,
            status="open",
            ask_mid=ask_mid,
        )
        session.add(user)
        session.add(q)
        session.commit()
        session.refresh(q)
        q_id = q.id

    night_warning = ""
    if is_night_mode():
        night_warning = "\n\n🌙 Тихий час (22:00–07:00): вопрос виден всем, опыт начислится утром."
    photo_line = "\n📷 К вопросу приложено фото из учебника." if image_url else ""

    sent = await event.message.answer(
        text=(
            f"📚 **Вопрос от {sender.first_name}** · {subject}\n\n"
            f"«{question_text}»{photo_line}\n\n"
            "Ответьте **реплаем** на это сообщение. Кнопка «Я помог» не обязательна. "
            "Если совсем тупик — «Подсказка ГигаЧат»."
            f"{night_warning}"
        ),
        attachments=question_keyboard(q_id, sender.user_id),
    )
    prompt_mid = None
    if sent and sent.message and sent.message.body:
        prompt_mid = sent.message.body.mid
    if prompt_mid:
        with Session(engine) as session:
            q = session.get(Question, q_id)
            if q:
                q.prompt_mid = prompt_mid
                session.add(q)
                session.commit()


@dp.message_callback()
async def on_callback(event: MessageCallback):
    payload = event.callback.payload or ""
    clicker_user = event.callback.user
    clicker_id = clicker_user.user_id
    reply_chat = event.chat.chat_id if event.chat else clicker_id

    if payload.startswith("gigahint:"):
        q_id = int(payload.split(":")[1])
        await event.answer(notification="Готовлю подсказку…")
        with Session(engine, expire_on_commit=False) as session:
            q = session.exec(select(Question).where(Question.id == q_id)).first()
            if not q:
                await event.answer(notification="Вопрос не найден!")
                return
            clicker = get_or_create_user(
                max_user_id=clicker_id,
                name=clicker_user.first_name,
                username=clicker_user.username,
                chat_id=q.chat_id,
                session=session,
            )
            chat = session.exec(select(ClassChat).where(ClassChat.max_chat_id == q.chat_id)).first()
            if is_ai_blocked(chat, q.subject):
                await bot.send_message(
                    chat_id=reply_chat,
                    text=f"🔒 Сегодня по теме «{q.subject}» ГигаЧат выключен учителем. Попросите одноклассника — за это дают XP.",
                )
                return
            allowed, left = consume_hint_quota(clicker, HINT_DAILY_LIMIT)
            session.add(clicker)
            session.commit()
            if not allowed:
                await bot.send_message(
                    chat_id=reply_chat,
                    text=f"🤖 Лимит подсказок на сегодня исчерпан ({HINT_DAILY_LIMIT}/день). Спросите одноклассников — за это дают XP.",
                )
                return
            q_text, image_url = q.text, q.image_url

        image_bytes = None
        image_name = "task.jpg"
        if image_url:
            try:
                image_bytes, image_name = await download_url(image_url)
            except Exception:
                image_bytes = None
        try:
            hint = await make_hint(q_text, strict=False, image=image_bytes, image_name=image_name)
        except Exception:
            hint = "Не получилось достучаться до ГигаЧат. Попросите одноклассника или попробуйте позже."
        await bot.send_message(
            chat_id=reply_chat,
            text=(
                f"🤖 **Подсказка ГигаЧат** (не готовый ответ)\n"
                f"Осталось сегодня: {left}\n\n"
                f"{hint}\n\n"
                "Лучше, если одноклассник объяснит своими словами — за это капают XP."
            ),
        )
        return

    if payload.startswith("helped:"):
        q_id = int(payload.split(":")[1])
        with Session(engine, expire_on_commit=False) as session:
            q = session.exec(select(Question).where(Question.id == q_id)).first()
            if not q:
                await event.answer(notification="Вопрос не найден!")
                return
            author = session.exec(select(User).where(User.id == q.author_id)).first()
            if not author:
                await event.answer(notification="Ошибка автора вопроса.")
                return
            if author.max_user_id == clicker_id:
                await event.answer(notification="Нельзя помочь самому себе!")
                return
            helper = get_or_create_user(
                max_user_id=clicker_id,
                name=clicker_user.first_name,
                username=clicker_user.username,
                chat_id=q.chat_id,
                session=session,
            )
            status = assign_helper(q, helper, author)
            if status == "self":
                await event.answer(notification="Нельзя помочь самому себе!")
                return
            if status == "closed":
                await event.answer(notification="Вопрос уже закрыт.")
                return
            session.add(q)
            session.commit()
            author_name = author.name
            if status == "same":
                await event.answer(notification="Вы уже отмечены помощником.")
                return

        await event.answer(notification=f"Автор ({author_name}) может нажать «Сказать спасибо».")
        await bot.send_message(
            chat_id=reply_chat,
            text=(
                f"💡 **{clicker_user.first_name}** откликнулся в чате класса.\n"
                f"{author_name}, когда разберётесь — нажмите **«Сказать спасибо»**."
            ),
        )
        return

    if not payload.startswith("thanks:"):
        return

    q_id = int(payload.split(":")[1])
    with Session(engine, expire_on_commit=False) as session:
        q = session.exec(select(Question).where(Question.id == q_id)).first()
        if not q:
            await event.answer(notification="Вопрос не найден!")
            return
        author = session.exec(select(User).where(User.id == q.author_id)).first()
        if not author:
            await event.answer(notification="Ошибка автора вопроса.")
            return
        if author.max_user_id != clicker_id:
            await event.answer(notification=f"Только автор ({author.name}) может сказать спасибо!")
            return
        if q.status == "answered":
            await event.answer(notification="За этот вопрос благодарность уже начислена!")
            return
        if not q.helper_id:
            await event.answer(notification="Сначала ответ реплаем на вопрос или кнопка «Я помог»!")
            return
        helper = session.exec(select(User).where(User.id == q.helper_id)).first()
        if not helper or helper.id == author.id:
            await event.answer(notification="Помощник не найден.")
            return

        result = complete_thanks(session, q, author, helper)
        session.add(helper)
        session.add(author)
        session.add(q)
        session.commit()
        helper_name = helper.name
        author_name = author.name
        helper_level = calculate_level(helper.xp)["title"]

    await event.answer(notification=f"Благодарность отправлена {helper_name}!")
    night_note = "\n🌙 Опыт будет начислен утром." if result["deferred"] else ""
    lines = [
        f"🎉 **{author_name}** поблагодарил(а) **{helper_name}** в чате класса!",
        f"⚡️ {helper_name}: **+{result['xp_gained']} XP** и **+{result['coins_gained']} спасибок**.{night_note}",
        f"🎖 Звание: **{helper_level}**",
    ]
    if result["unlocked"]:
        lines.append("🌟 Новое достижение:")
        lines.extend(f"• {item}" for item in result["unlocked"])
    await bot.send_message(
        chat_id=reply_chat,
        text="\n".join(lines),
        attachments=make_webapp_keyboard(clicker_id),
    )


@dp.message_created()
async def on_reply_help(event: MessageCreated):
    if not is_class_chat(event):
        return
    sender = event.message.sender
    if not sender or sender.is_bot:
        return
    body = event.message.body
    text = (body.text or "").strip() if body else ""
    if text.startswith("/"):
        return
    link = event.message.link
    if not link or str(getattr(link, "type", "")).lower() != "reply":
        return
    linked = getattr(link, "message", None)
    mid = getattr(linked, "mid", None)
    attachments = (body.attachments or []) if body else []
    if len(text) < 8 and not attachments:
        return

    chat_id = class_chat_id(event)
    with Session(engine, expire_on_commit=False) as session:
        q = find_question_by_reply_mid(session, chat_id, mid)
        if not q:
            return
        helper = bind_user_from_event(event, session=session)
        author = session.exec(select(User).where(User.id == q.author_id)).first()
        status = assign_helper(q, helper, author)
        if status != "ok":
            return
        session.add(q)
        session.commit()
        author_name = author.name if author else "Автор"
        q_id = q.id

    await event.message.answer(
        text=(
            f"💡 **{sender.first_name}** ответил(а) реплаем.\n"
            f"{author_name}, если разбор понятен — нажмите **«Сказать спасибо»**."
        ),
        attachments=question_keyboard(q_id, sender.user_id),
    )
