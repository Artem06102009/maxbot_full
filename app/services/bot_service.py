from typing import Optional
from sqlmodel import select, delete, or_
from maxapi import Bot, Dispatcher
from maxapi.filters.command import Command, CommandStart
from maxapi.types import MessageCreated, MessageCallback, BotStarted, BotAdded
from maxapi.types import CallbackButton
from maxapi.utils.inline_keyboard import InlineKeyboardBuilder

from app.config import get_settings
from app.database.session import async_session_factory
from app.database.models import User, Chat, ChatMember, Question, Answer, Reward
from app.services.gamification import GamificationService

settings = get_settings()
dp = Dispatcher()

def extract_display_name(user) -> str:
    if not user:
        return "Участник"
    first = getattr(user, "first_name", "") or ""
    last = getattr(user, "last_name", "") or ""
    full = f"{first} {last}".strip()
    if full:
        return full
    username = getattr(user, "username", "") or ""
    if username:
        return username
    user_id = getattr(user, "user_id", "")
    return f"Участник {user_id}"

def build_main_menu_keyboard():
    builder = InlineKeyboardBuilder()
    builder.add(CallbackButton(text="❓ Как спросить?", payload="menu:ask_info"))
    builder.add(CallbackButton(text="👤 Мой профиль", payload="menu:profile"))
    builder.add(CallbackButton(text="🏆 Рейтинг", payload="menu:top"))
    builder.add(CallbackButton(text="🎁 Лавка наград", payload="menu:shop"))
    builder.add(CallbackButton(text="🎓 Кабинет учителя", payload="menu:teacher"))
    builder.adjust(2, 2, 1)
    return builder

async def send_response_or_dm(event: MessageCreated, text: str, kb=None, is_group: bool = False):
    attachments = [kb.as_markup()] if kb else None
    if is_group:
        user_id = str(event.from_user.user_id) if event.from_user else ""
        target_uid = int(user_id) if user_id.isdigit() else None
        user_name = extract_display_name(event.from_user)
        if target_uid:
            try:
                await event.bot.send_message(user_id=target_uid, text=text, attachments=attachments)
                await event.message.reply(text=f"{user_name}, я отправил вам информацию в личные сообщения.")
                return
            except Exception:
                await event.message.reply(text=f"{user_name}, чтобы открыть меню, перейдите в личный диалог с ботом.")
                return
    await event.message.answer(text=text, attachments=attachments)

def build_approval_keyboard(answer_id: int):
    builder = InlineKeyboardBuilder()
    builder.add(CallbackButton(text="✅ Помог (+50 XP)", payload=f"help_accept:{answer_id}"))
    builder.add(CallbackButton(text="❌ Не помог", payload=f"help_reject:{answer_id}"))
    builder.adjust(2)
    return builder

async def ensure_user_and_chat(
    session,
    user_id: str,
    display_name: str,
    chat_id: Optional[str] = None,
    chat_title: Optional[str] = None,
    owner_id: Optional[str] = None,
    is_admin: bool = False,
    is_group: bool = False,
):
    res_user = await session.execute(select(User).where(User.max_user_id == user_id))
    user = res_user.scalar_one_or_none()

    is_teacher = is_admin or bool(owner_id and str(owner_id) == str(user_id))

    if not user:
        user = User(
            max_user_id=user_id,
            display_name=display_name,
            role="teacher" if is_teacher else "student",
            balance_spasibki=10,
            xp=100,
            rank="Любитель",
        )
        session.add(user)
    else:
        if display_name and not display_name.startswith("Участник "):
            user.display_name = display_name
        if is_teacher and user.role != "teacher":
            user.role = "teacher"

    if is_group and chat_id and chat_title and chat_title.lower() != "диалог":
        res_chat = await session.execute(select(Chat).where(Chat.max_chat_id == chat_id))
        chat_row = res_chat.scalar_one_or_none()
        if not chat_row:
            chat_row = Chat(
                max_chat_id=chat_id,
                title=chat_title,
                owner_id=owner_id,
            )
            session.add(chat_row)
        else:
            if chat_title:
                chat_row.title = chat_title
            if owner_id and not chat_row.owner_id:
                chat_row.owner_id = owner_id

        member_role = "teacher" if is_teacher else "student"
        res_member = await session.execute(
            select(ChatMember).where(ChatMember.chat_max_id == chat_id, ChatMember.user_max_id == user_id)
        )
        member_row = res_member.scalar_one_or_none()
        if not member_row:
            member_row = ChatMember(
                chat_max_id=chat_id,
                user_max_id=user_id,
                role=member_role,
            )
            session.add(member_row)
        else:
            if is_teacher and member_row.role != "teacher":
                member_row.role = "teacher"

    await session.commit()
    return user

@dp.bot_added()
async def on_bot_added(event: BotAdded):
    chat_id = str(event.chat_id)
    print(f"[БОТ] Бот добавлен в чат {chat_id}")

    try:
        chat_obj = await event.bot.get_chat_by_id(event.chat_id)
        chat_title = chat_obj.title if chat_obj else f"Чат {chat_id}"
        owner_id = str(chat_obj.owner_id) if chat_obj and chat_obj.owner_id else None

        async with async_session_factory() as session:
            res_chat = await session.execute(select(Chat).where(Chat.max_chat_id == chat_id))
            chat_row = res_chat.scalar_one_or_none()
            if not chat_row:
                chat_row = Chat(max_chat_id=chat_id, title=chat_title, owner_id=owner_id)
                session.add(chat_row)
            else:
                chat_row.title = chat_title
                if owner_id:
                    chat_row.owner_id = owner_id

            if owner_id:
                res_m = await session.execute(
                    select(ChatMember).where(ChatMember.chat_max_id == chat_id, ChatMember.user_max_id == owner_id)
                )
                if not res_m.scalar_one_or_none():
                    session.add(ChatMember(chat_max_id=chat_id, user_max_id=owner_id, role="teacher"))

            try:
                admin_res = await event.bot.get_list_admin_chat(event.chat_id)
                if admin_res and hasattr(admin_res, "members") and admin_res.members:
                    for adm in admin_res.members:
                        adm_id = str(adm.user_id)
                        adm_name = f"{adm.first_name or ''} {adm.last_name or ''}".strip() or adm.username or f"Учитель {adm_id}"
                        u_res = await session.execute(select(User).where(User.max_user_id == adm_id))
                        adm_u = u_res.scalar_one_or_none()
                        if not adm_u:
                            session.add(User(max_user_id=adm_id, display_name=adm_name, role="teacher", balance_spasibki=10, xp=100))
                        else:
                            adm_u.role = "teacher"

                        res_adm_m = await session.execute(
                            select(ChatMember).where(ChatMember.chat_max_id == chat_id, ChatMember.user_max_id == adm_id)
                        )
                        adm_m = res_adm_m.scalar_one_or_none()
                        if not adm_m:
                            session.add(ChatMember(chat_max_id=chat_id, user_max_id=adm_id, role="teacher"))
                        else:
                            adm_m.role = "teacher"
            except Exception:
                pass

            await session.commit()
    except Exception as e:
        print(f"[БОТ] Ошибка регистрации чата: {e}")

@dp.bot_started()
async def on_bot_started(event: BotStarted):
    user_id = str(event.from_user.user_id) if event.from_user else ""
    user_name = extract_display_name(event.from_user)
    print(f"[БОТ] Бот запущен пользователем {user_name} (ID: {user_id})")

    async with async_session_factory() as session:
        await ensure_user_and_chat(session, user_id=user_id, display_name=user_name, is_group=False)

    kb = build_main_menu_keyboard()
    text = (
        f"Здравствуйте, {user_name}!\n\n"
        f"«Лига Помощников» — бот взаимопомощи для школьников в мессенджере MAX.\n\n"
        f"• Задавайте вопросы по учебе командой /ask в чате группы или класса.\n"
        f"• Отвечайте реплаем на вопросы одноклассников, получайте XP и спасибки.\n"
        f"• Обменивайте спасибки на школьные бонусы в лавке наград!\n\n"
        f"Используйте кнопки под сообщением для быстрого управления:"
    )
    if event.chat:
        chat_target = int(event.chat.chat_id) if str(event.chat.chat_id).isdigit() else event.chat.chat_id
        await event.bot.send_message(chat_id=chat_target, text=text, attachments=[kb.as_markup()])

@dp.message_created(CommandStart())
@dp.message_created(Command("start"))
async def on_start_command(event: MessageCreated):
    user_id = str(event.from_user.user_id) if event.from_user else ""
    user_name = extract_display_name(event.from_user)
    chat_id = str(event.chat.chat_id) if event.chat else ""
    chat_type = getattr(event.chat, "type", "") or ""
    is_group = bool(chat_type in ("chat", "group", "channel") and getattr(event.chat, "title", None))
    chat_title = event.chat.title if is_group else None
    owner_id = str(event.chat.owner_id) if event.chat and getattr(event.chat, "owner_id", None) else None

    print(f"[БОТ] Команда /start от {user_name} (ID: {user_id})")

    async with async_session_factory() as session:
        await ensure_user_and_chat(
            session,
            user_id=user_id,
            display_name=user_name,
            chat_id=chat_id if is_group else None,
            chat_title=chat_title,
            owner_id=owner_id,
            is_group=is_group,
        )

    kb = build_main_menu_keyboard()
    text = (
        f"Здравствуйте, {user_name}!\n\n"
        f"Лига Помощников готова к работе. Выберите действие на клавиатуре бота:"
    )
    await send_response_or_dm(event, text=text, kb=kb, is_group=is_group)

@dp.message_created(Command("help"))
async def on_help_command(event: MessageCreated):
    user_id = str(event.from_user.user_id) if event.from_user else ""
    user_name = extract_display_name(event.from_user)
    chat_type = getattr(event.chat, "type", "") or ""
    is_group = bool(chat_type in ("chat", "group", "channel") and getattr(event.chat, "title", None))
    print(f"[БОТ] Команда /help от {user_name}")

    kb = build_main_menu_keyboard()
    text = (
        f"Инструкция по работе с Лигой Помощников:\n\n"
        f"1. Добавьте бота в ваш учебный чат.\n"
        f"2. Чтобы задать вопрос, напишите:\n/ask <текст вопроса>\n"
        f"3. Чтобы помочь, ответьте реплаем (Reply) на сообщение бота с вопросом.\n"
        f"4. Автор вопроса нажимает кнопку «Помог», и помощнику начисляется +50 XP и +1 спасибка.\n"
        f"5. Накопленные спасибки можно обменивать на награды от учителей."
    )
    await send_response_or_dm(event, text=text, kb=kb, is_group=is_group)

@dp.message_created(Command("manage"))
async def on_manage_command(event: MessageCreated):
    user_id = str(event.from_user.user_id) if event.from_user else ""
    user_name = extract_display_name(event.from_user)
    chat_type = getattr(event.chat, "type", "") or ""
    is_group = bool(chat_type in ("chat", "group", "channel") and getattr(event.chat, "title", None))
    print(f"[БОТ] Команда /manage от {user_name}")

    async with async_session_factory() as session:
        res = await session.execute(select(User).where(User.max_user_id == user_id))
        user = res.scalar_one_or_none()
        is_teacher = bool(user and user.role in ("teacher", "admin"))

    kb = build_main_menu_keyboard()

    if is_teacher:
        text = (
            f"Кабинет учителя для {user_name}\n\n"
            f"Вам доступно одобрение заявок учеников на награды, создание бонусов и вручение награды «Выбор учителя» (+100 XP и +20 спасибок)."
        )
    else:
        text = (
            f"Кабинет учителя\n\n"
            f"Этот раздел предназначен для преподавателей и кураторов учебных групп."
        )

    await send_response_or_dm(event, text=text, kb=kb, is_group=is_group)

@dp.message_created(Command("ask"))
async def on_ask_command(event: MessageCreated):
    user_id = str(event.from_user.user_id) if event.from_user else ""
    user_name = extract_display_name(event.from_user)
    chat_id = str(event.chat.chat_id) if event.chat else ""
    chat_type = getattr(event.chat, "type", "") or ""
    is_group = bool(chat_type in ("chat", "group", "channel") and getattr(event.chat, "title", None))
    chat_title = event.chat.title if is_group else "Учебный чат"
    owner_id = str(event.chat.owner_id) if event.chat and getattr(event.chat, "owner_id", None) else None

    raw_text = event.message.body.text if event.message and event.message.body else ""
    parts = raw_text.split(maxsplit=1)
    question_text = parts[1].strip() if len(parts) > 1 else ""

    if not question_text:
        await event.message.reply(
            text="Пожалуйста, укажите текст вопроса после команды.\n\nПример:\n/ask Ребята, как решить задачу №4 по геометрии?"
        )
        return

    user_mid = str(event.message.body.mid) if event.message and event.message.body else f"msg_{user_id}"

    async with async_session_factory() as session:
        await ensure_user_and_chat(
            session,
            user_id=user_id,
            display_name=user_name,
            chat_id=chat_id if is_group else None,
            chat_title=chat_title,
            owner_id=owner_id,
            is_group=is_group,
        )

        q = Question(
            group_chat_id=chat_id,
            group_chat_title=chat_title,
            group_message_id=user_mid,
            user_message_id=user_mid,
            author_max_id=user_id,
            author_name=user_name,
            text=question_text,
            status="open",
        )
        session.add(q)
        await session.commit()
        await session.refresh(q)

        card_text = (
            f"❓ Вопрос по учёбе #{q.id}\n\n"
            f"Автор: {user_name}\n"
            f"Чат: {chat_title}\n\n"
            f"«{question_text}»\n\n"
            f"💬 Чтобы помочь, ответьте реплаем (Reply) на это сообщение!"
        )
        sent = await event.message.answer(text=card_text)
        if sent and sent.message and sent.message.body and sent.message.body.mid:
            q.group_message_id = str(sent.message.body.mid)
            await session.commit()

        print(f"[БОТ] Вопрос #{q.id} опубликован в чате '{chat_title}' от {user_name}")

async def sync_bot_chats(target_bot: Bot):
    try:
        chats_data = await target_bot.get_chats(count=100)
        if not chats_data or not hasattr(chats_data, "chats") or not chats_data.chats:
            return

        async with async_session_factory() as session:
            for c in chats_data.chats:
                chat_id = str(c.chat_id)
                chat_type = getattr(c, "type", "") or ""
                chat_title = getattr(c, "title", None)

                if chat_type in ("dialog", "user") or not chat_title or chat_title.lower() == "диалог":
                    continue

                owner_id = str(c.owner_id) if getattr(c, "owner_id", None) else None

                res_chat = await session.execute(select(Chat).where(Chat.max_chat_id == chat_id))
                chat_row = res_chat.scalar_one_or_none()
                if not chat_row:
                    chat_row = Chat(max_chat_id=chat_id, title=chat_title, owner_id=owner_id)
                    session.add(chat_row)
                else:
                    chat_row.title = chat_title
                    if owner_id:
                        chat_row.owner_id = owner_id

                if owner_id:
                    res_m = await session.execute(
                        select(ChatMember).where(ChatMember.chat_max_id == chat_id, ChatMember.user_max_id == owner_id)
                    )
                    if not res_m.scalar_one_or_none():
                        session.add(ChatMember(chat_max_id=chat_id, user_max_id=owner_id, role="teacher"))

                try:
                    admins_data = await target_bot.get_list_admin_chat(int(chat_id))
                    if admins_data and hasattr(admins_data, "members") and admins_data.members:
                        for adm in admins_data.members:
                            adm_id = str(adm.user_id)
                            adm_name = f"{adm.first_name or ''} {adm.last_name or ''}".strip() or adm.username or f"Учитель {adm_id}"

                            u_res = await session.execute(select(User).where(User.max_user_id == adm_id))
                            u_row = u_res.scalar_one_or_none()
                            if not u_row:
                                session.add(User(max_user_id=adm_id, display_name=adm_name, role="teacher", balance_spasibki=10, xp=100))
                            else:
                                u_row.role = "teacher"
                                if adm_name and not adm_name.startswith("Учитель "):
                                    u_row.display_name = adm_name

                            am_res = await session.execute(
                                select(ChatMember).where(ChatMember.chat_max_id == chat_id, ChatMember.user_max_id == adm_id)
                            )
                            if not am_res.scalar_one_or_none():
                                session.add(ChatMember(chat_max_id=chat_id, user_max_id=adm_id, role="teacher"))
                            else:
                                am_row = am_res.scalar_one()
                                am_row.role = "teacher"
                except Exception:
                    pass

            await session.commit()
            print("[БОТ] Синхронизация чатов и администраторов завершена")
    except Exception as e:
        print(f"[БОТ] Синхронизация чатов: {e}")

@dp.message_created()
async def on_generic_message(event: MessageCreated):
    if not event.message or not event.message.body:
        return

    chat_id = str(event.chat.chat_id) if event.chat else ""
    chat_type = getattr(event.chat, "type", "") or ""
    chat_title = getattr(event.chat, "title", None)
    is_group = bool(chat_type in ("chat", "group", "channel") and chat_title and chat_title.lower() != "диалог")

    sender_id = str(event.from_user.user_id) if event.from_user else ""
    sender_name = extract_display_name(event.from_user)
    owner_id = str(event.chat.owner_id) if event.chat and getattr(event.chat, "owner_id", None) else None

    if is_group and sender_id:
        async with async_session_factory() as session:
            is_owner = bool(owner_id and owner_id == sender_id)
            await ensure_user_and_chat(
                session,
                user_id=sender_id,
                display_name=sender_name,
                chat_id=chat_id,
                chat_title=chat_title,
                owner_id=owner_id,
                is_admin=is_owner,
                is_group=True,
            )

    raw_text = event.message.body.text or ""
    if raw_text.startswith("/"):
        return

    if not event.message.link or not event.message.link.message:
        return

    parent_mid = event.message.link.message.mid
    if not parent_mid:
        return

    helper_id = str(event.from_user.user_id) if event.from_user else ""
    helper_name = extract_display_name(event.from_user)
    answer_mid = event.message.body.mid

    async with async_session_factory() as session:
        res_q = await session.execute(
            select(Question).where(
                or_(
                    Question.group_message_id == parent_mid,
                    Question.user_message_id == parent_mid,
                    Question.group_message_id.like(f"%{parent_mid}%"),
                ),
                Question.status == "open",
            )
        )
        question = res_q.scalar_one_or_none()
        if not question:
            return

        if helper_id == question.author_max_id:
            await event.message.reply(text="Вы не можете отвечать на свой собственный вопрос.")
            return

        await ensure_user_and_chat(
            session,
            user_id=helper_id,
            display_name=helper_name,
            chat_id=chat_id if is_group else None,
            chat_title=chat_title,
            is_group=is_group,
        )

        ans = Answer(
            question_id=question.id,
            author_max_id=helper_id,
            author_name=helper_name,
            group_message_id=answer_mid,
            text=raw_text,
            status="pending",
        )
        session.add(ans)
        await session.commit()
        await session.refresh(ans)

        print(f"[БОТ] Ответ на вопрос #{question.id} от {helper_name}")

    author_target = int(question.author_max_id) if str(question.author_max_id).isdigit() else None
    approval_kb = build_approval_keyboard(ans.id)
    dm_text = (
        f"Вам ответил(а) {helper_name} на вопрос #{question.id}:\n\n"
        f"«{raw_text}»\n\n"
        f"Помогло ли вам это объяснение?"
    )

    sent_to_author = False
    if author_target:
        try:
            await event.bot.send_message(user_id=author_target, text=dm_text, attachments=[approval_kb.as_markup()])
            sent_to_author = True
        except Exception as e:
            print(f"[БОТ] Не удалось отправить ЛС автору {author_target}: {e}")

    if sent_to_author:
        await event.message.reply(text=f"Ответ от {helper_name} принят и отправлен автору вопроса в личные сообщения для подтверждения.")
    else:
        await event.message.reply(text=f"Ответ от {helper_name} принят! Автору вопроса нужно открыть диалог с ботом, чтобы подтвердить помощь.")

@dp.message_callback()
async def on_callback_query(event: MessageCallback):
    payload = event.callback.payload if event.callback else ""
    user_id = str(event.callback.user.user_id) if event.callback and event.callback.user else ""
    user_name = extract_display_name(event.callback.user) if event.callback else "Ученик"

    print(f"[БОТ] Нажата кнопка '{payload}' от {user_name} (ID: {user_id})")

    if payload.startswith("help_accept:"):
        ans_id = int(payload.split(":")[1])
        async with async_session_factory() as session:
            res_ans = await session.execute(select(Answer).where(Answer.id == ans_id))
            answer = res_ans.scalar_one_or_none()
            if not answer or answer.status != "pending":
                if event.callback and event.callback.callback_id:
                    await event.bot.send_callback(callback_id=event.callback.callback_id, notification="Ответ уже обработан.")
                return

            res_q = await session.execute(select(Question).where(Question.id == answer.question_id))
            question = res_q.scalar_one_or_none()
            if not question or str(question.author_max_id) != str(user_id):
                if event.callback and event.callback.callback_id:
                    await event.bot.send_callback(callback_id=event.callback.callback_id, notification="Только автор вопроса может подтвердить помощь.")
                return

            await GamificationService.award_gratitude(
                session=session,
                question=question,
                answer=answer,
            )

            print(f"[БОТ] Помощь подтверждена: вопрос #{question.id}, {answer.author_name} получил +50 XP и +1 спасибку")

        try:
            await event.message.edit(text=f"Помощь принята! {answer.author_name} получил(а) +50 XP и +1 спасибку. Спасибо за взаимовыручку!")
        except Exception:
            pass

        try:
            chat_target = int(question.group_chat_id) if str(question.group_chat_id).isdigit() else question.group_chat_id
            celeb_text = (
                f"🎉 Вопрос #{question.id} успешно решён!\n\n"
                f"Помог(ла): {answer.author_name} (+50 XP, +1 спасибка)\n"
                f"Автор {question.author_name} поблагодарил(а) за помощь."
            )
            await event.bot.send_message(chat_id=chat_target, text=celeb_text)
        except Exception:
            pass

        if event.callback and event.callback.callback_id:
            await event.bot.send_callback(callback_id=event.callback.callback_id, notification="Помощь принята!")
        return

    if payload.startswith("help_reject:"):
        ans_id = int(payload.split(":")[1])
        async with async_session_factory() as session:
            res_ans = await session.execute(select(Answer).where(Answer.id == ans_id))
            answer = res_ans.scalar_one_or_none()
            if answer:
                answer.status = "rejected"
                await session.commit()

        try:
            await event.message.edit(text="Ответ отклонён. Вопрос остаётся открытым для других участников.")
        except Exception:
            pass

        if event.callback and event.callback.callback_id:
            await event.bot.send_callback(callback_id=event.callback.callback_id, notification="Ответ отклонён")
        return

    kb = build_main_menu_keyboard()

    if payload == "menu:ask_info":
        info_text = (
            f"Как задать вопрос классу:\n\n"
            f"1. Откройте чат вашей учебной группы.\n"
            f"2. Отправьте сообщение:\n/ask <текст вопроса>\n\n"
            f"Бот создаст карточку с вопросом. Одноклассники смогут ответить реплаем, а вы отметите лучший ответ."
        )
        try:
            await event.message.edit(text=info_text, attachments=[kb.as_markup()])
        except Exception:
            pass

    elif payload == "menu:profile":
        async with async_session_factory() as session:
            res_user = await session.execute(select(User).where(User.max_user_id == user_id))
            u = res_user.scalar_one_or_none()
            rank_name = u.rank if u else "Любитель"
            xp_val = u.xp if u else 0
            coins_val = u.balance_spasibki if u else 0
            role_title = "Учитель" if (u and u.role == "teacher") else "Ученик"

        profile_text = (
            f"Личный профиль:\n\n"
            f"Имя: {user_name}\n"
            f"Статус: {role_title}\n"
            f"Звание: {rank_name}\n"
            f"Опыт: {xp_val} XP\n"
            f"Баланс: {coins_val} спасибок\n\n"
            f"Используйте команды и кнопки для навигации."
        )
        try:
            await event.message.edit(text=profile_text, attachments=[kb.as_markup()])
        except Exception:
            pass

    elif payload == "menu:top":
        async with async_session_factory() as session:
            leaders_res = await session.execute(
                select(User).where(User.role == "student").order_by(User.xp.desc()).limit(5)
            )
            leaders = leaders_res.scalars().all()

        lines = ["Топ помощников:\n"]
        if not leaders:
            lines.append("Пока нет участников с начисленными баллами.")
        else:
            for idx, leader in enumerate(leaders, 1):
                lines.append(f"{idx}. {leader.display_name} — {leader.xp} XP ({leader.balance_spasibki} спасибок)")

        top_text = "\n".join(lines)
        try:
            await event.message.edit(text=top_text, attachments=[kb.as_markup()])
        except Exception:
            pass

    elif payload == "menu:shop":
        async with async_session_factory() as session:
            rewards_res = await session.execute(
                select(Reward).where(Reward.is_active == True).order_by(Reward.cost_spasibki.asc()).limit(5)
            )
            rewards = rewards_res.scalars().all()

        lines = ["Доступные награды в лавке:\n"]
        for r in rewards:
            lines.append(f"• {r.title} — {r.cost_spasibki} спасибок")

        shop_text = "\n".join(lines)
        try:
            await event.message.edit(text=shop_text, attachments=[kb.as_markup()])
        except Exception:
            pass

    elif payload == "menu:teacher":
        teacher_text = (
            f"Кабинет учителя\n\n"
            f"Педагоги могут создавать школьные награды, одобрять запросы школьников и вручать бейдж «Выбор учителя»."
        )
        try:
            await event.message.edit(text=teacher_text, attachments=[kb.as_markup()])
        except Exception:
            pass

    if event.callback and event.callback.callback_id:
        try:
            await event.bot.send_callback(callback_id=event.callback.callback_id)
        except Exception:
            pass
