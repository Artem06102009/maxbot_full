from datetime import datetime, timezone
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlmodel import Session, select, desc
from app.database import get_session, get_or_create_user
from app.gigachat import download_url, make_hint
from app.jobs import send_class_digest

from app.cheatsheet_pdf import build_cheatsheet_pdf
from app.logic import (
    CLASS_SUBJECTS,
    apply_pending_rewards,
    assign_helper,
    complete_thanks,
    consume_hint_quota,
    decode_strict_subjects,
    encode_strict_subjects,
    is_ai_blocked,
    is_night_mode,
)
from app.reports import (
    ensure_report_token,
    parent_week_payload,
    report_url,
    weekly_export_csv,
    weekly_summary,
)
from app.models import (
    User, Question, Achievement, UserAchievement,
    Reward, RewardRequest, ClassChat, calculate_level
)
from config import HINT_DAILY_LIMIT

api_router = APIRouter()


class BuyRewardRequest(BaseModel):
    user_id: int
    reward_id: int


class ReviewRewardRequest(BaseModel):
    teacher_id: int
    request_id: int
    action: str


class CreateRewardRequest(BaseModel):
    teacher_id: int
    title: str
    description: str
    price_coins: int
    icon: Optional[str] = "🎁"


class AwardTeacherChoiceRequest(BaseModel):
    teacher_id: int
    student_id: int


class AskQuestionRequest(BaseModel):
    user_id: int
    text: str = Field(min_length=3, max_length=1000)
    subject: Optional[str] = None


class QuestionActionRequest(BaseModel):
    user_id: int
    question_id: int


@api_router.get("/profile")
def get_profile(
    request: Request,
    user_id: int = Query(...),
    session: Session = Depends(get_session)
):
    user = session.exec(select(User).where(User.max_user_id == user_id)).first()
    if not user:
        raise HTTPException(status_code=404, detail="Пользователь не найден")

    if apply_pending_rewards(user):
        session.add(user)
        session.commit()
        session.refresh(user)
    if not user.report_token:
        ensure_report_token(user)
        session.add(user)
        session.commit()
        session.refresh(user)

    students_query = select(User).where(User.role == "student")
    if user.chat_id:
        students_query = students_query.where(User.chat_id == user.chat_id)
    all_users = session.exec(students_query.order_by(desc(User.xp))).all()
    rank = 1
    for idx, u in enumerate(all_users):
        if u.id == user.id:
            rank = idx + 1
            break

    level_info = calculate_level(user.xp)

    return {
        "id": user.id,
        "max_user_id": user.max_user_id,
        "name": user.name,
        "username": user.username,
        "role": user.role,
        "coins": user.coins,
        "helped_count": user.helped_count,
        "questions_count": user.questions_count,
        "stem_helped_count": user.stem_helped_count,
        "humanities_helped_count": user.humanities_helped_count,
        "pending_xp": user.pending_xp,
        "pending_coins": user.pending_coins,
        "night_mode": is_night_mode(),
        "class_chat_id": user.chat_id,
        "report_url": str(request.base_url).rstrip("/") + "/p/" + user.report_token,
        "rank": rank,
        "total_students": len(all_users),
        "level": level_info,
    }


def _format_question(session: Session, q: Question) -> dict:
    author = session.exec(select(User).where(User.id == q.author_id)).first()
    helper = session.exec(select(User).where(User.id == q.helper_id)).first() if q.helper_id else None
    created = q.created_at.strftime("%d.%m %H:%M") if q.created_at else ""
    return {
        "id": q.id,
        "text": q.text,
        "subject": q.subject,
        "status": q.status,
        "created_at": created,
        "author_id": author.max_user_id if author else None,
        "author_name": author.name if author else "Ученик",
        "helper_id": helper.max_user_id if helper else None,
        "helper_name": helper.name if helper else None,
        "image_url": q.image_url,
        "is_exemplar": bool(q.is_exemplar),
        "exemplar_note": q.exemplar_note,
        "open": q.status == "open",
    }


@api_router.get("/questions")
def list_questions(
    user_id: int = Query(...),
    session: Session = Depends(get_session),
):
    user = session.exec(select(User).where(User.max_user_id == user_id)).first()
    if not user:
        raise HTTPException(status_code=404, detail="Пользователь не найден")

    query = select(Question).order_by(desc(Question.created_at))
    if user.chat_id:
        query = query.where(Question.chat_id == user.chat_id)
    questions = session.exec(query.limit(50)).all()
    return [_format_question(session, q) for q in questions]


def _cheatsheet_payload(session: Session, user: User) -> dict:
    query = select(Question).where(Question.is_exemplar == True).order_by(desc(Question.created_at))  # noqa: E712
    if user.chat_id:
        query = query.where(Question.chat_id == user.chat_id)
    items = [_format_question(session, q) for q in session.exec(query).all()]
    grouped: dict[str, list] = {}
    for item in items:
        grouped.setdefault(item["subject"], []).append(item)
    chat = None
    if user.chat_id:
        chat = session.exec(select(ClassChat).where(ClassChat.max_chat_id == user.chat_id)).first()
    return {
        "class_title": chat.title if chat else "Класс",
        "subjects": [
            {"subject": name, "items": grouped.get(name, [])}
            for name in list(CLASS_SUBJECTS) + [key for key in grouped if key not in CLASS_SUBJECTS]
            if grouped.get(name)
        ],
    }


@api_router.get("/cheatsheet")
def get_cheatsheet(
    user_id: int = Query(...),
    session: Session = Depends(get_session),
):
    user = session.exec(select(User).where(User.max_user_id == user_id)).first()
    if not user:
        raise HTTPException(status_code=404, detail="Пользователь не найден")
    return _cheatsheet_payload(session, user)


@api_router.get("/cheatsheet.pdf")
def download_cheatsheet_pdf(
    user_id: int = Query(...),
    session: Session = Depends(get_session),
):
    user = session.exec(select(User).where(User.max_user_id == user_id)).first()
    if not user:
        raise HTTPException(status_code=404, detail="Пользователь не найден")
    payload = _cheatsheet_payload(session, user)
    pdf = build_cheatsheet_pdf(payload["subjects"], payload["class_title"])
    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={"Content-Disposition": "attachment; filename=shpargalka-klassa.pdf"},
    )


@api_router.post("/questions")
def ask_question(
    data: AskQuestionRequest,
    session: Session = Depends(get_session),
):
    return {
        "success": False,
        "error": "Вопросы задают в чате класса в MAX командой /ask",
    }


@api_router.post("/questions/help")
def mark_helped(
    data: QuestionActionRequest,
    session: Session = Depends(get_session),
):
    q = session.exec(select(Question).where(Question.id == data.question_id)).first()
    if not q:
        raise HTTPException(status_code=404, detail="Вопрос не найден")
    if q.status == "answered":
        return {"success": False, "error": "Вопрос уже закрыт благодарностью"}

    author = session.exec(select(User).where(User.id == q.author_id)).first()
    helper = session.exec(select(User).where(User.max_user_id == data.user_id)).first()
    if not helper:
        helper = get_or_create_user(data.user_id, session=session)
    status = assign_helper(q, helper, author)
    if status == "self":
        return {"success": False, "error": "Нельзя помочь самому себе"}
    if status == "closed":
        return {"success": False, "error": "Вопрос уже закрыт благодарностью"}
    session.add(q)
    session.commit()
    return {
        "success": True,
        "message": "Вы отметились помощником. Автор может сказать спасибо.",
        "question": _format_question(session, q),
    }


@api_router.post("/questions/thanks")
def say_thanks(
    data: QuestionActionRequest,
    session: Session = Depends(get_session),
):
    q = session.exec(select(Question).where(Question.id == data.question_id)).first()
    if not q:
        raise HTTPException(status_code=404, detail="Вопрос не найден")

    author = session.exec(select(User).where(User.id == q.author_id)).first()
    clicker = session.exec(select(User).where(User.max_user_id == data.user_id)).first()
    if not author or not clicker or author.id != clicker.id:
        return {"success": False, "error": "Только автор вопроса может сказать спасибо"}
    if q.status == "answered":
        return {"success": False, "error": "Благодарность уже начислена"}
    if not q.helper_id:
        return {"success": False, "error": "Сначала одноклассник отвечает реплаем на вопрос или жмёт «Я помог»"}

    helper = session.exec(select(User).where(User.id == q.helper_id)).first()
    if not helper or helper.id == author.id:
        return {"success": False, "error": "Помощник не найден"}

    result = complete_thanks(session, q, author, helper)
    session.add(helper)
    session.add(author)
    session.add(q)
    session.commit()

    extra = " Опыт будет начислен утром — сейчас тихий час." if result["deferred"] else ""
    return {
        "success": True,
        "message": f"Спасибо отправлено! {helper.name} получает +{result['xp_gained']} XP.{extra}",
        "unlocked": result["unlocked"],
        "question": _format_question(session, q),
    }


@api_router.get("/students")
def list_students(session: Session = Depends(get_session)):
    students = session.exec(select(User).where(User.role == "student").order_by(User.name)).all()
    return [
        {
            "max_user_id": s.max_user_id,
            "name": s.name,
            "xp": s.xp,
            "helped_count": s.helped_count,
        }
        for s in students
    ]


@api_router.get("/leaderboard")
def get_leaderboard(
    user_id: int | None = Query(default=None),
    session: Session = Depends(get_session),
):
    query = select(User).where(User.role == "student")
    if user_id:
        current = session.exec(select(User).where(User.max_user_id == user_id)).first()
        if current and current.chat_id:
            query = query.where(User.chat_id == current.chat_id)
    students = session.exec(query.order_by(desc(User.xp)).limit(20)).all()

    result = []
    for idx, s in enumerate(students):
        level_info = calculate_level(s.xp)
        result.append({
            "rank": idx + 1,
            "max_user_id": s.max_user_id,
            "name": s.name,
            "username": s.username,
            "xp": s.xp,
            "coins": s.coins,
            "level_title": level_info["title"],
            "helped_count": s.helped_count,
        })
    return result


@api_router.get("/achievements")
def get_achievements(
    user_id: int = Query(...),
    session: Session = Depends(get_session)
):
    user = session.exec(select(User).where(User.max_user_id == user_id)).first()
    if not user:
        raise HTTPException(status_code=404, detail="Пользователь не найден")

    achievements = session.exec(select(Achievement)).all()
    user_achs = session.exec(
        select(UserAchievement).where(UserAchievement.user_id == user.id)
    ).all()
    unlocked_map = {ua.achievement_id: ua.unlocked_at for ua in user_achs}

    result = []
    for ach in achievements:
        is_unlocked = ach.id in unlocked_map
        result.append({
            "id": ach.id,
            "code": ach.code,
            "title": ach.title,
            "description": ach.description,
            "icon": ach.icon,
            "xp_reward": ach.xp_reward,
            "coins_reward": ach.coins_reward,
            "is_unlocked": is_unlocked,
            "unlocked_at": unlocked_map[ach.id].strftime("%d.%m.%Y") if is_unlocked else None,
        })
    return result


@api_router.get("/rewards")
def get_rewards(
    user_id: int = Query(...),
    session: Session = Depends(get_session)
):
    user = session.exec(select(User).where(User.max_user_id == user_id)).first()
    if not user:
        raise HTTPException(status_code=404, detail="Пользователь не найден")

    rewards = session.exec(select(Reward).where(Reward.is_active == True)).all()

    requests = session.exec(
        select(RewardRequest, Reward)
        .join(Reward, RewardRequest.reward_id == Reward.id)
        .where(RewardRequest.user_id == user.id)
        .order_by(desc(RewardRequest.created_at))
    ).all()

    my_requests = []
    for req, rew in requests:
        my_requests.append({
            "id": req.id,
            "reward_title": rew.title,
            "reward_icon": rew.icon,
            "price_coins": rew.price_coins,
            "status": req.status,
            "created_at": req.created_at.strftime("%d.%m %H:%M"),
        })

    return {
        "user_coins": user.coins,
        "rewards": [
            {
                "id": r.id,
                "title": r.title,
                "description": r.description,
                "price_coins": r.price_coins,
                "icon": r.icon,
            }
            for r in rewards
        ],
        "my_requests": my_requests,
    }


@api_router.post("/rewards/buy")
def buy_reward(
    data: BuyRewardRequest,
    session: Session = Depends(get_session)
):
    user = session.exec(select(User).where(User.max_user_id == data.user_id)).first()
    if not user:
        raise HTTPException(status_code=404, detail="Пользователь не найден")

    reward = session.exec(select(Reward).where(Reward.id == data.reward_id)).first()
    if not reward or not reward.is_active:
        raise HTTPException(status_code=404, detail="Награда не найдена")

    if user.coins < reward.price_coins:
        return {"success": False, "error": f"Не хватает спасибок! Нужно {reward.price_coins}, а у вас {user.coins}"}

    user.coins -= reward.price_coins
    req = RewardRequest(
        user_id=user.id,
        reward_id=reward.id,
        status="pending"
    )
    session.add(user)
    session.add(req)
    session.commit()

    return {
        "success": True,
        "message": f"Заявка на '{reward.title}' отправлена учителю на подтверждение!",
        "new_coins": user.coins,
    }


@api_router.get("/teacher/requests")
def get_teacher_requests(
    teacher_id: int = Query(...),
    session: Session = Depends(get_session)
):
    teacher = session.exec(select(User).where(User.max_user_id == teacher_id)).first()
    if not teacher or teacher.role != "teacher":
        raise HTTPException(status_code=403, detail="Доступ запрещен. Требуется роль учителя.")

    pending_requests = session.exec(
        select(RewardRequest, Reward, User)
        .join(Reward, RewardRequest.reward_id == Reward.id)
        .join(User, RewardRequest.user_id == User.id)
        .where(RewardRequest.status == "pending")
        .order_by(desc(RewardRequest.created_at))
    ).all()

    result = []
    for req, rew, u in pending_requests:
        result.append({
            "id": req.id,
            "student_name": u.name,
            "student_id": u.max_user_id,
            "reward_title": rew.title,
            "reward_icon": rew.icon,
            "price_coins": rew.price_coins,
            "created_at": req.created_at.strftime("%d.%m %H:%M"),
        })
    return result


@api_router.post("/teacher/request/review")
def review_teacher_request(
    data: ReviewRewardRequest,
    session: Session = Depends(get_session)
):
    teacher = session.exec(select(User).where(User.max_user_id == data.teacher_id)).first()
    if not teacher or teacher.role != "teacher":
        raise HTTPException(status_code=403, detail="Доступ разрешен только учителям.")

    req = session.exec(select(RewardRequest).where(RewardRequest.id == data.request_id)).first()
    if not req:
        raise HTTPException(status_code=404, detail="Заявка не найдена")

    req.reviewed_at = datetime.now(timezone.utc)

    if data.action == "approve":
        req.status = "approved"
        session.add(req)
        session.commit()
        return {"success": True, "message": "Заявка успешно одобрена!"}
    else:
        req.status = "rejected"
        student = session.exec(select(User).where(User.id == req.user_id)).first()
        reward = session.exec(select(Reward).where(Reward.id == req.reward_id)).first()
        if student and reward:
            student.coins += reward.price_coins
            session.add(student)
        session.add(req)
        session.commit()
        return {"success": True, "message": "Заявка отклонена, спасибки возвращены ученику."}


@api_router.post("/teacher/reward/create")
def create_reward_by_teacher(
    data: CreateRewardRequest,
    session: Session = Depends(get_session)
):
    teacher = session.exec(select(User).where(User.max_user_id == data.teacher_id)).first()
    if not teacher or teacher.role != "teacher":
        raise HTTPException(status_code=403, detail="Доступ разрешен только учителям.")

    new_reward = Reward(
        title=data.title,
        description=data.description,
        price_coins=data.price_coins,
        icon=data.icon or "🎁",
        created_by_teacher_id=teacher.id,
    )
    session.add(new_reward)
    session.commit()
    return {"success": True, "message": f"Награда '{data.title}' успешно добавлена в лавку!"}


@api_router.post("/teacher/award_badge")
def award_teacher_choice(
    data: AwardTeacherChoiceRequest,
    session: Session = Depends(get_session)
):
    teacher = session.exec(select(User).where(User.max_user_id == data.teacher_id)).first()
    if not teacher or teacher.role != "teacher":
        raise HTTPException(status_code=403, detail="Доступ разрешен только учителям.")

    student = session.exec(select(User).where(User.max_user_id == data.student_id)).first()
    if not student:
        raise HTTPException(status_code=404, detail="Ученик не найден")

    ach = session.exec(select(Achievement).where(Achievement.code == "teacher_choice")).first()
    if not ach:
        raise HTTPException(status_code=404, detail="Достижение не найдено")

    existing = session.exec(
        select(UserAchievement).where(
            UserAchievement.user_id == student.id,
            UserAchievement.achievement_id == ach.id
        )
    ).first()

    if existing:
        return {"success": False, "message": "Ученик уже имеет это достижение!"}

    ua = UserAchievement(user_id=student.id, achievement_id=ach.id)
    student.xp += ach.xp_reward
    student.coins += ach.coins_reward
    session.add(ua)
    session.add(student)
    session.commit()

    return {
        "success": True,
        "message": f"Достижение '{ach.title}' успешно вручено ученику {student.name}! (+{ach.xp_reward} XP, +{ach.coins_reward} спасибок)"
    }


def _require_teacher(session: Session, teacher_id: int) -> User:
    teacher = session.exec(select(User).where(User.max_user_id == teacher_id)).first()
    if not teacher or teacher.role != "teacher":
        raise HTTPException(status_code=403, detail="Доступ разрешен только учителям.")
    return teacher


@api_router.get("/teacher/unanswered")
def teacher_unanswered(
    teacher_id: int = Query(...),
    session: Session = Depends(get_session),
):
    teacher = _require_teacher(session, teacher_id)
    query = select(Question).where(Question.status == "open").order_by(Question.created_at)
    if teacher.chat_id:
        query = query.where(Question.chat_id == teacher.chat_id)
    items = session.exec(query.limit(30)).all()
    return [_format_question(session, q) for q in items]


class StrictModeRequest(BaseModel):
    teacher_id: int
    enabled: Optional[bool] = None
    subjects: Optional[list[str]] = None


@api_router.get("/teacher/class")
def teacher_class(
    teacher_id: int = Query(...),
    session: Session = Depends(get_session),
):
    teacher = _require_teacher(session, teacher_id)
    chat = None
    if teacher.chat_id:
        chat = session.exec(select(ClassChat).where(ClassChat.max_chat_id == teacher.chat_id)).first()
    blocked = decode_strict_subjects(chat)
    return {
        "strict_mode": bool(chat.strict_mode) if chat else False,
        "strict_subjects": blocked,
        "subjects": list(CLASS_SUBJECTS),
        "class_chat_id": teacher.chat_id,
        "hint_limit": HINT_DAILY_LIMIT,
    }


@api_router.post("/teacher/strict")
def set_strict(data: StrictModeRequest, session: Session = Depends(get_session)):
    teacher = _require_teacher(session, data.teacher_id)
    if not teacher.chat_id:
        raise HTTPException(status_code=400, detail="Сначала добавьте бота в чат класса")
    chat = session.exec(select(ClassChat).where(ClassChat.max_chat_id == teacher.chat_id)).first()
    if not chat:
        chat = ClassChat(max_chat_id=teacher.chat_id, title="Чат класса")
    if data.subjects is not None:
        all_on, encoded = encode_strict_subjects(data.subjects)
        chat.strict_mode = all_on
        chat.strict_subjects = encoded
    elif data.enabled is True:
        chat.strict_mode = True
        chat.strict_subjects = None
    else:
        chat.strict_mode = False
        chat.strict_subjects = None
    session.add(chat)
    session.commit()
    return {
        "success": True,
        "strict_mode": chat.strict_mode,
        "strict_subjects": decode_strict_subjects(chat),
    }


class ExemplarRequest(BaseModel):
    teacher_id: int
    question_id: int
    note: Optional[str] = None


@api_router.post("/teacher/exemplar")
def mark_exemplar(data: ExemplarRequest, session: Session = Depends(get_session)):
    _require_teacher(session, data.teacher_id)
    q = session.exec(select(Question).where(Question.id == data.question_id)).first()
    if not q:
        raise HTTPException(status_code=404, detail="Вопрос не найден")
    q.is_exemplar = True
    if data.note:
        q.exemplar_note = data.note.strip()
    session.add(q)
    session.commit()
    return {"success": True, "message": "Добавлено в шпаргалку класса"}


@api_router.post("/teacher/digest")
async def teacher_digest(teacher_id: int = Query(...), session: Session = Depends(get_session)):
    teacher = _require_teacher(session, teacher_id)
    if not teacher.chat_id:
        raise HTTPException(status_code=400, detail="Нет чата класса")
    text = await send_class_digest(teacher.chat_id)
    return {"success": True, "message": "Дайджест отправлен в чат класса", "text": text}


@api_router.get("/teacher/export")
def teacher_export(
    teacher_id: int = Query(...),
    session: Session = Depends(get_session),
):
    teacher = _require_teacher(session, teacher_id)
    return weekly_summary(session, teacher.chat_id)


@api_router.get("/teacher/export.csv")
def teacher_export_csv(
    teacher_id: int = Query(...),
    session: Session = Depends(get_session),
):
    teacher = _require_teacher(session, teacher_id)
    content = "\ufeff" + weekly_export_csv(session, teacher.chat_id)
    return Response(
        content=content,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": "attachment; filename=liga-week.csv"},
    )


@api_router.post("/questions/hint")
async def web_hint(data: QuestionActionRequest, session: Session = Depends(get_session)):
    user = session.exec(select(User).where(User.max_user_id == data.user_id)).first()
    if not user:
        raise HTTPException(status_code=404, detail="Пользователь не найден")
    q = session.exec(select(Question).where(Question.id == data.question_id)).first()
    if not q:
        raise HTTPException(status_code=404, detail="Вопрос не найден")
    chat = session.exec(select(ClassChat).where(ClassChat.max_chat_id == q.chat_id)).first()
    if is_ai_blocked(chat, q.subject):
        return {
            "success": False,
            "error": f"Сегодня по теме «{q.subject}» ГигаЧат выключен учителем. Спросите одноклассников.",
        }
    allowed, left = consume_hint_quota(user, HINT_DAILY_LIMIT)
    session.add(user)
    session.commit()
    if not allowed:
        return {"success": False, "error": f"Лимит подсказок на сегодня: {HINT_DAILY_LIMIT}"}
    strict = bool(chat and chat.strict_mode)
    image_bytes = None
    image_name = "task.jpg"
    if q.image_url:
        try:
            image_bytes, image_name = await download_url(q.image_url)
        except Exception:
            image_bytes = None
    hint = await make_hint(q.text, strict=strict, image=image_bytes, image_name=image_name)
    return {"success": True, "hint": hint, "left": left}


@api_router.get("/demo/users")
def get_demo_users(session: Session = Depends(get_session)):
    users = session.exec(select(User)).all()
    return [
        {
            "id": u.id,
            "max_user_id": u.max_user_id,
            "name": u.name,
            "role": u.role,
            "coins": u.coins,
            "xp": u.xp
        }
        for u in users
    ]
