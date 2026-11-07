from datetime import datetime, timezone
from typing import Any, Optional, Union
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from app.database.session import async_session_factory
from app.services.anti_cheat import AntiCheatService
from app.services.gamification import GamificationService
from app.config import get_settings
from app.database.models import Answer, Question, User
from app.services.class_tools import (
    CLASS_SUBJECTS,
    PHOTO_FALLBACK,
    consume_hint_quota,
    decode_strict_subjects,
    encode_strict_subjects,
    ensure_report_token,
    exam_label,
    find_hanging_duplicate,
    get_class_settings,
    is_ai_blocked,
    is_exam_active,
    parse_ask_text,
    resolve_subject_name,
    set_exam_mode,
)
from app.services.gamification import QUALITY_LABELS
from app.services.gigachat import make_hint
from app.services.max_client import (
    KeyboardBuilder,
    MaxBotClient,
    build_callback_button,
    build_link_button,
    build_setup_home_keyboard,
    format_inline_keyboard,
    get_bot_client,
)

def to_int_id(val: Any) -> Optional[int]:
    if val is None:
        return None
    s = str(val).strip()
    if not s:
        return None
    if s.isdigit():
        return int(s)
    if s.startswith("-") and s[1:].isdigit():
        return -int(s[1:])
    digits = "".join(ch for ch in s if ch.isdigit())
    if digits:
        val_int = int(digits)
        return -val_int if s.startswith("-") else val_int
    return None

class BotService:
    def __init__(self, client: Optional[MaxBotClient] = None) -> None:
        self.client = client or get_bot_client()
        self._seen_updates: set[str] = set()
        self._seen_messages: set[str] = set()
        self._last_start_ts: dict[str, float] = {}
        self._in_flight_answers: set[int] = set()

    def _mark_seen(self, collection: set[str], item_id: Any, max_size: int = 3000) -> bool:
        if not item_id:
            return False
        key = str(item_id)
        if key in collection:
            return True
        if len(collection) >= max_size:
            to_remove = len(collection) - (max_size // 2)
            for _ in range(to_remove):
                collection.pop()
        collection.add(key)
        return False

    async def _auto_assign_chat_teacher(
        self,
        session: AsyncSession,
        group_cid: str,
        client: MaxBotClient,
        triggering_user_id: Optional[str] = None,
        triggering_user_name: Optional[str] = None,
    ) -> Optional[User]:
        row = await get_class_settings(session, str(group_cid))
        if row.teacher_max_id:
            stmt = select(User).where(User.max_user_id == row.teacher_max_id)
            teacher = (await session.execute(stmt)).scalar_one_or_none()
            if teacher:
                return teacher

        creator_id = None
        try:
            chat_info = await client.get_chat(group_cid)
            if isinstance(chat_info, dict):
                creator_id = (
                    chat_info.get("owner_id")
                    or chat_info.get("creator_id")
                    or (chat_info.get("owner", {}) if isinstance(chat_info.get("owner"), dict) else {}).get("user_id")
                    or (chat_info.get("creator", {}) if isinstance(chat_info.get("creator"), dict) else {}).get("user_id")
                )
        except Exception:
            pass

        if not creator_id:
            try:
                admins_info = await client.get_chat_admins(group_cid)
                if isinstance(admins_info, dict):
                    admins_list = admins_info.get("admins") or admins_info.get("members") or []
                    for adm in admins_list:
                        if isinstance(adm, dict) and adm.get("role") in ("creator", "owner"):
                            creator_id = adm.get("user_id") or adm.get("id")
                            break
                    if not creator_id and admins_list and isinstance(admins_list[0], dict):
                        creator_id = admins_list[0].get("user_id") or admins_list[0].get("id")
            except Exception:
                pass

        if not creator_id and triggering_user_id:
            creator_id = triggering_user_id

        if creator_id:
            name = triggering_user_name if str(creator_id) == str(triggering_user_id) else None
            teacher = await self.ensure_user(session, str(creator_id), display_name=name, role="teacher")
            teacher.role = "teacher"
            row.teacher_max_id = teacher.max_user_id
            await session.commit()
            return teacher

        return None

    async def ensure_user(
        self,
        session: AsyncSession,
        max_user_id: Union[int, str],
        display_name: Optional[str] = None,
        role: str = "student",
    ) -> User:
        uid_str = str(max_user_id).strip()
        conditions = [User.max_user_id == uid_str]
        if uid_str.isdigit():
            conditions.append(User.max_user_id.endswith(f"_{uid_str}"))
        if display_name:
            conditions.append(User.display_name == display_name)
        stmt = select(User).where(or_(*conditions))
        res = await session.execute(stmt)
        user = res.scalars().first()
        if user is None:
            name = display_name or f"Ученик {uid_str}"
            user = User(
                max_user_id=uid_str,
                display_name=name,
                role=role,
                balance_spasibki=0,
                xp=0,
                rank="Новичок",
            )
            session.add(user)
            await session.flush()
        ensure_report_token(user)
        return user

    async def handle_ask_command(
        self,
        session: AsyncSession,
        message: dict[str, Any],
        client: MaxBotClient,
    ) -> None:
        recipient = message.get("recipient", {})
        chat_type = recipient.get("chat_type")
        raw_chat_id = message.get("chat_id") or recipient.get("chat_id")
        sender = message.get("sender", {})
        raw_sender_id = str(sender.get("user_id", ""))
        sender_name = sender.get("name") or sender.get("username") or f"Ученик {raw_sender_id}"
        user = await self.ensure_user(session, raw_sender_id, display_name=sender_name)
        sender_id = user.max_user_id

        raw_text = (
            message.get("body", {}).get("text")
            if isinstance(message.get("body"), dict)
            else None
        ) or message.get("text") or ""
        raw_text = raw_text.strip()
        parts = raw_text.split(None, 1)
        raw_question = parts[1].strip() if len(parts) > 1 else ""
        subject, question_text = parse_ask_text(raw_question)
        image_url = None
        body = message.get("body") if isinstance(message.get("body"), dict) else {}
        for att in body.get("attachments") or message.get("attachments") or []:
            if not isinstance(att, dict):
                continue
            payload = att.get("payload") if isinstance(att.get("payload"), dict) else {}
            url = payload.get("url")
            if url and ("image" in str(att.get("type", "")).lower() or "photo" in str(att.get("type", "")).lower() or url):
                image_url = url
                break
        if not question_text and image_url:
            question_text = PHOTO_FALLBACK
            if subject == "Учёба":
                subject = "Точные науки"
        if not question_text:
            return

        target_chat_id = to_int_id(raw_chat_id) or raw_chat_id
        target_uid = to_int_id(raw_sender_id) or raw_sender_id
        is_group = target_chat_id and target_chat_id != 0 and chat_type != "dialog" and target_chat_id != target_uid
        if not is_group:
            await client.send_message(
                user_id=target_uid,
                text=(
                    "❓ Вопросы задают в чате класса в MAX.\n\n"
                    "Напишите там `/ask Как решить №245 по геометрии?`\n"
                    "Одноклассники отвечают реплаем, автор подтверждает помощь."
                ),
            )
            return
        group_cid = str(target_chat_id if is_group else target_uid)
        ask_mid = (
            message.get("body", {}).get("mid")
            if isinstance(message.get("body"), dict)
            else None
        ) or message.get("id") or message.get("mid") or ""

        duplicate = await find_hanging_duplicate(session, group_cid, question_text)
        if duplicate:
            dup_text = (
                "⏳ Этот вопрос уже висит.\n\n"
                f"«{duplicate.text}»\n\n"
                "Ответьте реплаем на карточку выше — второй `/ask` не нужен."
            )
            if is_group:
                await client.send_message(chat_id=target_chat_id, text=dup_text)
            else:
                await client.send_message(user_id=target_uid, text=dup_text)
            return

        question = Question(
            group_chat_id=group_cid,
            group_message_id=f"pending-{ask_mid or sender_id}",
            ask_mid=str(ask_mid) if ask_mid else None,
            author_max_id=sender_id,
            text=question_text,
            subject=subject,
            image_url=image_url,
            status="open",
        )
        session.add(question)
        await session.flush()

        class_row = await get_class_settings(session, group_cid)
        if not class_row.teacher_max_id and is_group:
            await self._auto_assign_chat_teacher(session, group_cid, client, triggering_user_id=raw_sender_id, triggering_user_name=sender_name)

        announcement = (
            f"❓ Вопрос от {sender_name} · {subject}:\n"
            f"{question_text}\n\n"
            "💬 Ответьте реплаем на это сообщение, чтобы помочь однокласснику!"
        )
        if is_group:
            resp = await client.send_message(chat_id=target_chat_id, text=announcement)
        else:
            resp = await client.send_message(user_id=target_uid, text=announcement)

        bot_msg = resp.get("message", {})
        bot_mid = (
            bot_msg.get("body", {}).get("mid")
            if isinstance(bot_msg.get("body"), dict)
            else None
        ) or bot_msg.get("id") or bot_msg.get("mid") or question.group_message_id
        question.group_message_id = str(bot_mid)
        await GamificationService.evaluate_achievements(session, sender_id)
        await session.commit()

    async def handle_reply_message(
        self,
        session: AsyncSession,
        message: dict[str, Any],
        client: MaxBotClient,
        reply_to_mid: str,
    ) -> None:
        recipient = message.get("recipient", {})
        chat_type = recipient.get("chat_type")
        raw_chat_id = message.get("chat_id") or recipient.get("chat_id")
        sender = message.get("sender", {})
        raw_sender_id = str(sender.get("user_id", ""))
        sender_name = sender.get("name") or sender.get("username") or f"Ученик {raw_sender_id}"
        user = await self.ensure_user(session, raw_sender_id, display_name=sender_name)
        sender_id = user.max_user_id

        stmt = select(Question).where(
            Question.status == "open",
            or_(
                Question.group_message_id == str(reply_to_mid),
                Question.ask_mid == str(reply_to_mid),
            ),
        )
        res = await session.execute(stmt)
        question = res.scalar_one_or_none()
        if question is None:
            return

        is_same_author = (
            str(question.author_max_id).strip().lower() == sender_id.strip().lower()
            or str(question.author_max_id).strip().lower() == raw_sender_id.strip().lower()
            or to_int_id(question.author_max_id) == to_int_id(raw_sender_id)
        )
        target_chat = to_int_id(raw_chat_id) or raw_chat_id
        target_uid = to_int_id(raw_sender_id) or raw_sender_id

        if is_same_author or not AntiCheatService.validate_answer_eligibility(question.author_max_id, sender_id):
            reject_text = f"⚠️ {sender_name}, нельзя отвечать на собственный вопрос в Лиге Помощников!"
            if target_chat and target_chat != 0 and chat_type != "dialog" and target_chat != target_uid:
                await client.send_message(chat_id=target_chat, text=reject_text)
            else:
                await client.send_message(user_id=target_uid, text=reject_text)
            return

        incoming_mid = (
            message.get("body", {}).get("mid")
            if isinstance(message.get("body"), dict)
            else None
        ) or message.get("id") or message.get("mid") or ""
        answer_text = (
            message.get("body", {}).get("text")
            if isinstance(message.get("body"), dict)
            else None
        ) or message.get("text") or ""

        answer = Answer(
            question_id=question.id,
            author_max_id=sender_id,
            group_message_id=str(incoming_mid),
            text=answer_text,
            status="pending",
        )
        session.add(answer)
        await session.flush()

        keyboard = KeyboardBuilder.question_verification_keyboard(answer.id)
        author_target_uid = to_int_id(question.author_max_id)
        dm_resp = await client.send_message(
            user_id=author_target_uid,
            text=(
                f"К вашему вопросу получен ответ:\n«{answer_text}»\n\n"
                "Как ответ?\n"
                "• Понятно — полный разбор (+50 XP)\n"
                "• Коротко — помог, но без объяснения (+25 XP)\n"
                "• С ошибкой — вопрос останется открытым (+10 XP)"
            ),
            attachments=keyboard,
        )
        dm_msg = dm_resp.get("message", {})
        dm_mid = (
            dm_msg.get("body", {}).get("mid")
            if isinstance(dm_msg.get("body"), dict)
            else None
        ) or dm_msg.get("id") or dm_msg.get("mid")
        if dm_mid:
            answer.dm_approval_message_id = str(dm_mid)
        await session.commit()

    async def handle_manage_command(
        self,
        session: AsyncSession,
        message: dict[str, Any],
        client: MaxBotClient,
    ) -> None:
        settings = get_settings()
        recipient = message.get("recipient", {})
        chat_type = recipient.get("chat_type")
        raw_chat_id = message.get("chat_id") or recipient.get("chat_id")
        sender = message.get("sender", {})
        raw_sender_id = str(sender.get("user_id", ""))
        sender_name = sender.get("name") or sender.get("username")
        user = await self.ensure_user(session, raw_sender_id, display_name=sender_name)
        target_uid = to_int_id(raw_sender_id)
        target_chat = to_int_id(raw_chat_id) or raw_chat_id
        is_group = target_chat and target_chat != 0 and chat_type != "dialog" and target_chat != target_uid

        if is_group:
            teacher = await self._auto_assign_chat_teacher(
                session, str(target_chat), client, triggering_user_id=raw_sender_id, triggering_user_name=sender_name
            )
            is_teacher = user.role in ("teacher", "admin") or (teacher and teacher.max_user_id == user.max_user_id)
            if not is_teacher:
                t_name = teacher.display_name if teacher else "учителю"
                await client.send_message(user_id=target_uid, text=f"⛔ Панель управления доступна только учителю класса ({t_name}).")
                return

        base_url = settings.WEBAPP_BASE_URL.rstrip("/")
        webapp_url = f"{base_url}/?user_id={user.max_user_id}"
        keyboard = KeyboardBuilder.teacher_menu_keyboard(webapp_url)
        menu_text = "👨‍🏫 Панель учителя Лиги Помощников:\nИспользуйте веб-кабинет для модерации наград, настройки класса и рейтинга:"
        await client.send_message(user_id=target_uid, text=menu_text, attachments=keyboard)

    async def handle_me_command(self, session: AsyncSession, message: dict[str, Any], client: MaxBotClient) -> None:
        sender = message.get("sender", {})
        raw_sender_id = str(sender.get("user_id", ""))
        user = await self.ensure_user(session, raw_sender_id, display_name=sender.get("name"))
        token = ensure_report_token(user)
        await session.commit()
        settings = get_settings()
        url = f"{settings.WEBAPP_BASE_URL.rstrip('/')}/p/{token}"
        target_uid = to_int_id(raw_sender_id)
        await client.send_message(
            user_id=target_uid,
            text=(
                f"👤 {user.display_name}\n"
                f"🎖 {user.rank} · {user.xp} XP · 🪙 {user.balance_spasibki}\n"
                f"👨‍👩‍👧 Для родителя (неделя, не переписка): {url}"
            ),
        )

    async def handle_strict_command(self, session: AsyncSession, message: dict[str, Any], client: MaxBotClient) -> None:
        recipient = message.get("recipient", {})
        chat_type = recipient.get("chat_type")
        raw_chat_id = message.get("chat_id") or recipient.get("chat_id")
        sender = message.get("sender", {})
        raw_sender_id = str(sender.get("user_id", ""))
        user = await self.ensure_user(session, raw_sender_id, display_name=sender.get("name"))
        target_chat = to_int_id(raw_chat_id) or raw_chat_id
        target_uid = to_int_id(raw_sender_id)
        is_group = target_chat and target_chat != 0 and chat_type != "dialog" and target_chat != target_uid
        if not is_group:
            await client.send_message(user_id=target_uid, text="Строгий режим включают в чате класса.")
            return
        if user.role not in ("teacher", "admin"):
            await client.send_message(user_id=target_uid, text="⛔ Только учитель может включать строгий режим.")
            return
        raw_text = (
            message.get("body", {}).get("text")
            if isinstance(message.get("body"), dict)
            else None
        ) or message.get("text") or ""
        parts = raw_text.split()[1:]
        settings_row = await get_class_settings(session, str(target_chat))
        token = parts[0].lower() if parts else "on"
        if token in ("off", "выкл", "0", "нет"):
            settings_row.strict_mode = False
            settings_row.strict_subjects = None
            label = "ГигаЧат снова доступен по всем темам."
        elif token in ("on", "вкл", "все", "all"):
            settings_row.strict_mode = True
            settings_row.strict_subjects = None
            label = "ГигаЧат выключен по всем предметам."
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
                await client.send_message(user_id=target_uid, text="Использование: `/strict физика` · `/strict on` · `/strict off`")
                return
            all_on, encoded = encode_strict_subjects(subjects)
            settings_row.strict_mode = all_on
            settings_row.strict_subjects = encoded
            shown = "все предметы" if all_on else ", ".join(decode_strict_subjects(settings_row))
            label = f"ГигаЧат выключен для: {shown}."
        await session.commit()
        await client.send_message(chat_id=target_chat, text=f"🔒 {label}")

    async def _require_class_teacher(self, session, message, client) -> Optional[tuple]:
        recipient = message.get("recipient", {})
        chat_type = recipient.get("chat_type")
        raw_chat_id = message.get("chat_id") or recipient.get("chat_id")
        sender = message.get("sender", {})
        raw_sender_id = str(sender.get("user_id", ""))
        user = await self.ensure_user(session, raw_sender_id, display_name=sender.get("name"))
        target_chat = to_int_id(raw_chat_id) or raw_chat_id
        target_uid = to_int_id(raw_sender_id)
        is_group = target_chat and target_chat != 0 and chat_type != "dialog" and target_chat != target_uid
        return user, target_chat, target_uid, is_group

    async def handle_exam_command(self, session: AsyncSession, message: dict[str, Any], client: MaxBotClient) -> None:
        user, target_chat, target_uid, is_group = await self._require_class_teacher(session, message, client)
        if not is_group:
            await client.send_message(user_id=target_uid, text="Режим контрольной включают в чате класса: `/exam физика`.")
            return
        row = await get_class_settings(session, str(target_chat))
        if user.role not in ("teacher", "admin") and row.teacher_max_id != user.max_user_id:
            await client.send_message(user_id=target_uid, text="⛔ Только учитель включает контрольную.")
            return
        raw_text = (
            message.get("body", {}).get("text")
            if isinstance(message.get("body"), dict)
            else None
        ) or message.get("text") or ""
        parts = raw_text.split()[1:]
        if not parts:
            text = exam_label(row) or "Контрольной нет. `/exam физика` · `/exam on` · `/exam off`"
            await client.send_message(chat_id=target_chat, text=f"📝 {text}")
            return
        token = parts[0].lower()
        days = 1
        for part in parts[1:]:
            if part.isdigit():
                days = int(part)
        if token in ("off", "выкл", "0", "нет"):
            set_exam_mode(row, None)
            label = "Контрольная снята. ГигаЧат и лавка снова можно."
        elif token in ("on", "вкл", "все", "all"):
            set_exam_mode(row, ["*"], days=days)
            label = exam_label(row)
        else:
            subjects = []
            for part in parts:
                if part.isdigit():
                    continue
                resolved = resolve_subject_name(part)
                if resolved == "*":
                    subjects = ["*"]
                    break
                if resolved and resolved not in subjects:
                    subjects.append(resolved)
            if not subjects:
                await client.send_message(user_id=target_uid, text="Использование: `/exam физика` · `/exam on` · `/exam off` · `/exam физика 2`")
                return
            set_exam_mode(row, subjects, days=days)
            label = exam_label(row)
        await session.commit()
        await client.send_message(chat_id=target_chat, text=f"📝 {label}")

    async def handle_setup_command(self, session: AsyncSession, message: dict[str, Any], client: MaxBotClient) -> None:
        user, target_chat, target_uid, is_group = await self._require_class_teacher(session, message, client)
        if is_group:
            teacher = await self._auto_assign_chat_teacher(
                session, str(target_chat), client, triggering_user_id=user.max_user_id, triggering_user_name=user.display_name
            )
            row = await get_class_settings(session, str(target_chat))
            is_teacher = user.role in ("teacher", "admin") or (teacher and teacher.max_user_id == user.max_user_id)
            if not is_teacher:
                t_name = teacher.display_name if teacher else "учителю"
                await client.send_message(user_id=target_uid, text=f"⚙️ Настройки Лиги доступны только учителю класса ({t_name}).")
                return

            text = self._setup_summary(row, user)
            await client.send_message(user_id=target_uid, text=text, attachments=build_setup_home_keyboard())
            return

        # Called in PM (в ЛС)
        stmt = select(ClassSettings).where(ClassSettings.teacher_max_id == user.max_user_id)
        class_res = await session.execute(stmt)
        row = class_res.scalars().first()
        if not row:
            row = await primary_class_settings(session)
            if not row.teacher_max_id:
                row.teacher_max_id = user.max_user_id
                user.role = "teacher"
                await session.commit()

        text = self._setup_summary(row, user)
        await client.send_message(user_id=target_uid, text=text, attachments=build_setup_home_keyboard())

    def _setup_summary(self, row, user) -> str:
        strict = decode_strict_subjects(row)
        if "*" in strict or row.strict_mode:
            ai = "выключен везде"
        elif strict:
            ai = "выключен для: " + ", ".join(strict)
        else:
            ai = "стандартный"
        digest = "воскресенье 18:00 включено" if getattr(row, "digest_enabled", True) is not False else "дайджест выключен"
        exam = exam_label(row) or "контрольной нет"
        return (
            f"⚙️ Настройки Лиги Помощников для класса\nУчитель: {user.display_name}\n\n"
            f"1. Предметы: {', '.join(CLASS_SUBJECTS)}\n"
            f"2. Строгий режим: {ai}\n"
            f"3. Дайджест: {digest}\n"
            f"4. Родителям: ребёнок пишет боту `/me` в ЛС — ссылка на неделю.\n"
            f"5. Режим контрольной: {exam}\n\n"
            "Выберите раздел для настройки (все настройки меняются здесь, в ЛС):"
        )

    async def handle_setup_callback(self, session, client, callback, user) -> None:
        callback_id = str(callback.get("callback_id", ""))
        payload = str(callback.get("payload", "")).strip()
        msg = callback.get("message") or {}
        raw_chat_id = msg.get("chat_id") or (msg.get("recipient") or {}).get("chat_id") or callback.get("chat_id")
        target_chat = to_int_id(raw_chat_id) or raw_chat_id
        target_uid = to_int_id(user.max_user_id)

        # Support configuration in PM or group
        if target_chat and target_chat != target_uid and target_chat != 0:
            row = await get_class_settings(session, str(target_chat))
        else:
            stmt = select(ClassSettings).where(ClassSettings.teacher_max_id == user.max_user_id)
            row = (await session.execute(stmt)).scalars().first()
            if not row:
                row = await primary_class_settings(session)

        if user.role not in ("teacher", "admin"):
            user.role = "teacher"
            if not row.teacher_max_id:
                row.teacher_max_id = user.max_user_id
            row.teacher_max_id = user.max_user_id

        action = payload.split(":", 1)[1] if ":" in payload else ""
        note = "Готово"
        extra = None
        kb = build_setup_home_keyboard()
        if action == "subjects":
            kb = format_inline_keyboard([
                [build_callback_button("Точные + гуманитарные", payload="setup:subj:all")],
                [build_callback_button("Только точные", payload="setup:subj:exact")],
                [build_callback_button("Только гуманитарные", payload="setup:subj:hum")],
                [build_callback_button("« Назад", payload="setup:home")],
            ])
            extra = "Полки класса. Ученик всё равно пишет предмет словами: `/ask физика …`."
        elif action.startswith("subj:"):
            kind = action.split(":", 1)[1]
            if kind == "exact":
                row.strict_mode = False
                row.strict_subjects = "Гуманитарные"
                note = "ИИ оставлен для точных наук"
            elif kind == "hum":
                row.strict_mode = False
                row.strict_subjects = "Точные науки"
                note = "ИИ оставлен для гуманитарных"
            else:
                row.strict_mode = False
                row.strict_subjects = None
                note = "Обе полки открыты"
            extra = self._setup_summary(row, user)
        elif action == "strict":
            kb = format_inline_keyboard([
                [build_callback_button("ИИ можно", payload="setup:ai:off")],
                [build_callback_button("Выкл ИИ везде", payload="setup:ai:all")],
                [build_callback_button("Без ИИ: точные", payload="setup:ai:exact")],
                [build_callback_button("« Назад", payload="setup:home")],
            ])
            extra = "Строгий режим: ГигаЧат не даёт подсказку в выбранных темах. Вопросы одноклассникам остаются."
        elif action.startswith("ai:"):
            kind = action.split(":", 1)[1]
            if kind == "all":
                row.strict_mode = True
                row.strict_subjects = None
                note = "ГигаЧат выключен"
            elif kind == "exact":
                row.strict_mode = False
                row.strict_subjects = "Точные науки"
                note = "Без ИИ по точным наукам"
            else:
                row.strict_mode = False
                row.strict_subjects = None
                note = "ГигаЧат снова можно"
            extra = self._setup_summary(row, user)
        elif action == "digest":
            kb = format_inline_keyboard([
                [build_callback_button("Вс 18:00 включить", payload="setup:dig:on")],
                [build_callback_button("Не присылать", payload="setup:dig:off")],
                [build_callback_button("« Назад", payload="setup:home")],
            ])
            extra = "Воскресный дайджест: кто помогал, какие темы, висящие вопросы."
        elif action.startswith("dig:"):
            row.digest_enabled = action.endswith("on")
            note = "Дайджест включён" if row.digest_enabled else "Дайджест выключен"
            extra = self._setup_summary(row, user)
        elif action == "parents":
            extra = (
                "👨‍👩‍👧 Родителям не нужна переписка класса.\n"
                "Попросите ребёнка написать боту в личке: `/me`\n"
                "Там ссылка на неделю: что спрашивал и кому помог."
            )
            note = "Текст для родителей"
        elif action == "exam":
            kb = format_inline_keyboard([
                [build_callback_button("Сегодня все темы", payload="setup:ex:all")],
                [build_callback_button("Сегодня точные", payload="setup:ex:exact")],
                [build_callback_button("Снять контрольную", payload="setup:ex:off")],
                [build_callback_button("« Назад", payload="setup:home")],
            ])
            extra = "На контрольной ГигаЧат и лавка выключены. `/ask` и ответы реплаем работают."
        elif action.startswith("ex:"):
            kind = action.split(":", 1)[1]
            if kind == "all":
                set_exam_mode(row, ["*"], days=1)
            elif kind == "exact":
                set_exam_mode(row, ["Точные науки"], days=1)
            else:
                set_exam_mode(row, None)
            note = "Контрольная обновлена"
            extra = self._setup_summary(row, user)
        elif action == "done":
            row.setup_done = True
            extra = self._setup_summary(row, user) + "\n\nГотово. Дальше: `/exam`, `/strict`, `/manage`."
            note = "Класс настроен"
            kb = None
        else:
            extra = self._setup_summary(row, user)
        await session.commit()
        await client.answer_callback(callback_id=callback_id, notification=note)
        if extra:
            await client.send_message(user_id=target_uid, text=extra, attachments=kb)

    async def _send_gigahint(self, session, client, callback, user, q_id: int) -> None:
        callback_id = str(callback.get("callback_id", ""))
        await client.answer_callback(callback_id=callback_id, notification="Подсказки ИИ отключены в чате класса")

    async def handle_message_callback(
        self,
        session: AsyncSession,
        callback: dict[str, Any],
        client: MaxBotClient,
    ) -> None:
        callback_id = str(callback.get("callback_id", ""))
        payload = str(callback.get("payload", "")).strip()
        user_info = callback.get("user", {})
        raw_user_id = str(user_info.get("user_id", ""))
        user = await self.ensure_user(session, raw_user_id, display_name=user_info.get("name"))

        answer_id: Optional[int] = None
        decision: Optional[str] = None

        if payload.startswith("verify_help:"):
            parts = payload.split(":")
            if len(parts) >= 3 and parts[1].isdigit():
                answer_id = int(parts[1])
                decision = parts[2]
        elif payload.startswith("verify_yes:"):
            parts = payload.split(":")
            if len(parts) >= 2 and parts[1].isdigit():
                answer_id = int(parts[1])
                decision = "yes"
        elif payload.startswith("verify_no:"):
            parts = payload.split(":")
            if len(parts) >= 2 and parts[1].isdigit():
                answer_id = int(parts[1])
                decision = "no"

        if payload.startswith("gigahint:"):
            await client.answer_callback(callback_id=callback_id, notification="Подсказки ИИ отключены в чате класса")
            return

        if payload.startswith("setup:"):
            await self.handle_setup_callback(session, client, callback, user)
            return

        if answer_id is None or decision not in ("yes", "no", "clear", "short", "error"):
            return

        if answer_id in self._in_flight_answers:
            await client.answer_callback(callback_id=callback_id, notification="Обрабатывается…")
            return

        self._in_flight_answers.add(answer_id)
        try:
            ans_stmt = select(Answer).where(Answer.id == answer_id)
            ans_res = await session.execute(ans_stmt)
            answer = ans_res.scalar_one_or_none()
            if answer is None:
                await client.answer_callback(callback_id=callback_id, notification="Ответ не найден")
                return

            if answer.status != "pending":
                await client.answer_callback(callback_id=callback_id, notification="Ответ уже обработан")
                return

            q_stmt = select(Question).where(Question.id == answer.question_id)
            q_res = await session.execute(q_stmt)
            question = q_res.scalar_one_or_none()
            if question is None:
                await client.answer_callback(callback_id=callback_id, notification="Вопрос не найден")
                return

            is_author = (
                str(question.author_max_id).strip().lower() == user.max_user_id.strip().lower()
                or str(question.author_max_id).strip().lower() == raw_user_id.strip().lower()
                or to_int_id(question.author_max_id) == to_int_id(raw_user_id)
            )
            if not is_author:
                await client.answer_callback(
                    callback_id=callback_id,
                    notification="Только автор вопроса может подтвердить ответ!",
                )
                return

            helper_uid = to_int_id(answer.author_max_id)
            group_cid = to_int_id(question.group_chat_id) or question.group_chat_id

            quality = {"yes": "clear", "no": "error"}.get(decision, decision)
            spasibki, xp = await GamificationService.award_gratitude(
                session, question, answer, quality=quality
            )
            q_label = QUALITY_LABELS.get(quality, quality)
            if quality == "error":
                await client.answer_callback(
                    callback_id=callback_id,
                    notification="Отметили ошибку",
                    message={"text": "Вопрос остаётся открытым. Можно ждать другой разбор."},
                )
                await client.send_message(
                    user_id=helper_uid,
                    text=f"Автор отметил ответ как «с ошибкой». +{xp} XP за попытку, вопрос ещё открыт.",
                )
            else:
                await client.answer_callback(
                    callback_id=callback_id,
                    notification="Благодарность отправлена!",
                    message={"text": f"✅ Вопрос закрыт. Оценка: {q_label}."},
                )
                helper_stmt = select(User).where(User.max_user_id == answer.author_max_id)
                helper_user = (await session.execute(helper_stmt)).scalar_one_or_none()
                streak = helper_user.help_streak if helper_user else 0
                streak_line = f" Серия: {streak} дн. 🔥" if streak else ""
                await client.send_message(
                    user_id=helper_uid,
                    text=f"Ответ «{q_label}». +{spasibki} спасибка, +{xp} XP.{streak_line}",
                )
        finally:
            self._in_flight_answers.discard(answer_id)

    async def handle_bot_started(
        self,
        session: AsyncSession,
        update: dict[str, Any],
        client: MaxBotClient,
    ) -> None:
        user_info = update.get("user", {})
        uid = str(user_info.get("user_id", ""))
        name = user_info.get("name") or user_info.get("username") or f"Ученик {uid}"
        user = await self.ensure_user(session, uid, display_name=name)
        chat_id = update.get("chat_id")
        user_target = to_int_id(uid)
        c_id = to_int_id(chat_id)
        is_group = c_id and c_id != 0 and c_id != user_target

        now = datetime.now(timezone.utc).timestamp()
        if now - self._last_start_ts.get(uid, 0.0) < 5.0:
            return
        self._last_start_ts[uid] = now

        settings = get_settings()
        webapp_url = f"{settings.WEBAPP_BASE_URL.rstrip('/')}/?user_id={uid}"
        keyboard = format_inline_keyboard([
            [build_link_button("Открыть Лигу Помощников", url=webapp_url)]
        ])

        if is_group:
            teacher = await self._auto_assign_chat_teacher(
                session, str(c_id), client, triggering_user_id=uid, triggering_user_name=name
            )
            t_name = teacher.display_name if teacher else "Создатель чата"
            welcome_text = (
                f"👋 Привет, класс! Я бот «Лига Помощников» 🎓\n\n"
                "• Задавайте вопросы по учёбе: `/ask <вопрос>`\n"
                "• Отвечайте реплаем на карточку вопроса, чтобы заработать опыт и спасибки 🪙\n"
                f"• Учитель класса: {t_name}\n\n"
                "Учитель может написать боту `/setup` или `/manage` для настройки лавки наград."
            )
            await client.send_message(chat_id=c_id, text=welcome_text, attachments=keyboard)
        else:
            welcome_text = (
                f"Привет, {name}! Добро пожаловать в Лигу Помощников 🎓\n\n"
                "Добавьте меня в чат вашего класса в MAX!\n"
                "• Задавайте вопросы через `/ask` и помогайте одноклассникам реплаем\n"
                "• За помощь начисляются опыт, звания и спасибки 🪙\n"
                "• В школьной лавке спасибки можно обменивать на награды от учителя!"
            )
            await client.send_message(user_id=user_target, text=welcome_text, attachments=keyboard)

    async def handle_hint_command(
        self, session: AsyncSession, message: dict[str, Any], client: MaxBotClient
    ) -> None:
        sender = message.get("sender", {})
        raw_sender_id = str(sender.get("user_id", ""))
        user = await self.ensure_user(session, raw_sender_id, display_name=sender.get("name"))
        target_uid = to_int_id(raw_sender_id)

        raw_text = (
            message.get("body", {}).get("text")
            if isinstance(message.get("body"), dict)
            else None
        ) or message.get("text") or ""
        question_text = raw_text.replace("/hint", "", 1).strip()
        if not question_text:
            await client.send_message(
                user_id=target_uid,
                text="💡 Напишите `/hint <вопрос или задача>` в этом личном диалоге, и я дам наводящую подсказку!",
            )
            return

        from app.services.gigachat import make_private_hint
        hint = await make_private_hint(question_text)
        await client.send_message(
            user_id=target_uid,
            text=f"🤖 Подсказка наставника (в ЛС):\n\n{hint}",
        )

    async def handle_message_created(
        self,
        session: AsyncSession,
        message: dict[str, Any],
        client: MaxBotClient,
    ) -> None:
        raw_text = (
            message.get("body", {}).get("text")
            if isinstance(message.get("body"), dict)
            else None
        ) or message.get("text") or ""
        text = raw_text.strip()

        if text.startswith("/hint"):
            await self.handle_hint_command(session, message, client)
            return

        if text.startswith("/ask"):
            await self.handle_ask_command(session, message, client)
            return

        if text.startswith("/me"):
            await self.handle_me_command(session, message, client)
            return

        if text.startswith("/strict"):
            await self.handle_strict_command(session, message, client)
            return

        if text.startswith("/exam"):
            await self.handle_exam_command(session, message, client)
            return

        if text.startswith("/setup"):
            await self.handle_setup_command(session, message, client)
            return

        if text.startswith("/manage"):
            await self.handle_manage_command(session, message, client)
            return

        if text.startswith(("/start", "/help")) or text.lower() in ("привет", "здравствуйте", "помощь", "меню", "хелп", "start", "help"):
            recipient = message.get("recipient", {})
            chat_type = recipient.get("chat_type")
            raw_chat_id = message.get("chat_id") or recipient.get("chat_id")
            sender = message.get("sender", {})
            raw_sender_id = str(sender.get("user_id", ""))
            sender_name = sender.get("name") or sender.get("username") or f"Ученик {raw_sender_id}"
            user = await self.ensure_user(session, raw_sender_id, display_name=sender_name)
            target_chat = to_int_id(raw_chat_id) or raw_chat_id
            target_uid = to_int_id(raw_sender_id)

            now = datetime.now(timezone.utc).timestamp()
            if now - self._last_start_ts.get(raw_sender_id, 0.0) < 5.0:
                # Avoid sending duplicate greeting if bot_started was just handled
                return
            self._last_start_ts[raw_sender_id] = now

            settings = get_settings()
            webapp_url = f"{settings.WEBAPP_BASE_URL.rstrip('/')}/?user_id={raw_sender_id}"
            keyboard = format_inline_keyboard([
                [build_link_button("Открыть Лигу Помощников", url=webapp_url)]
            ])
            help_text = (
                f"👋 Привет, {sender_name}! Бот «Лига Помощников» для взаимопомощи в учёбе.\n\n"
                "1. `/ask физика Как найти силу трения?` — задать вопрос классу (или фото с подписью `/ask`).\n"
                "2. Одноклассник отвечает реплаем на сообщение вопроса.\n"
                "3. Автор подтверждает помощь: понятно (+50 XP, +1 🪙), коротко (+25 XP) или с ошибкой.\n"
                "4. Копите спасибки и обменивайте их на бонусы от учителя в лавке наград!\n\n"
                "Учитель: `/setup` · `/exam` · `/strict` · `/manage`.\n"
                "`/me` — сводка родителю за неделю. 🌙 Тихий час 22:00–07:00."
            )
            if target_chat and target_chat != 0 and chat_type != "dialog" and target_chat != target_uid:
                await client.send_message(chat_id=target_chat, text=help_text, attachments=keyboard)
            else:
                await client.send_message(user_id=target_uid, text=help_text, attachments=keyboard)
            return

        reply_to_mid = None
        if "reply_to" in message and message["reply_to"]:
            rt = message["reply_to"]
            if isinstance(rt, dict):
                reply_to_mid = rt.get("id") or rt.get("mid")
            else:
                reply_to_mid = str(rt)
        elif "link" in message and isinstance(message["link"], dict):
            link = message["link"]
            reply_to_mid = link.get("id") or link.get("mid")
            if not reply_to_mid and "message" in link and isinstance(link["message"], dict):
                reply_to_mid = link["message"].get("mid") or link["message"].get("id")

        if reply_to_mid:
            await self.handle_reply_message(session, message, client, str(reply_to_mid))

    async def dispatch(
        self,
        session: AsyncSession,
        update: dict[str, Any],
        client: Optional[MaxBotClient] = None,
    ) -> None:
        active_client = client or self.client
        update_id = update.get("update_id")
        if update_id and self._mark_seen(self._seen_updates, update_id):
            return

        msg = update.get("message")
        if isinstance(msg, dict):
            mid = msg.get("mid") or msg.get("id") or (msg.get("body") if isinstance(msg.get("body"), dict) else {}).get("mid")
            if mid and self._mark_seen(self._seen_messages, mid):
                return

        utype = update.get("update_type")
        if utype == "message_created":
            await self.handle_message_created(session, update.get("message", {}), active_client)
        elif utype == "message_callback":
            await self.handle_message_callback(session, update.get("callback", {}), active_client)
        elif utype == "bot_started":
            await self.handle_bot_started(session, update, active_client)

default_bot_service = BotService()

async def handle_update(
    arg1: Any = None,
    arg2: Any = None,
    *,
    session: Optional[AsyncSession] = None,
    update: Optional[dict[str, Any]] = None,
    client: Optional[MaxBotClient] = None,
) -> None:
    target_session: Optional[AsyncSession] = session
    target_update: Optional[dict[str, Any]] = update

    if isinstance(arg1, AsyncSession):
        target_session = arg1
        if isinstance(arg2, dict):
            target_update = arg2
    elif isinstance(arg1, dict):
        target_update = arg1
        if isinstance(arg2, AsyncSession):
            target_session = arg2

    if target_update is None:
        return

    active_client = client or get_bot_client()

    if target_session is not None:
        await default_bot_service.dispatch(target_session, target_update, client=active_client)
    else:
        async with async_session_factory() as new_session:
            await default_bot_service.dispatch(new_session, target_update, client=active_client)

