from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Union
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from app.database.models import (
    Achievement,
    Answer,
    AuditLog,
    ClassSettings,
    Question,
    Reward,
    RewardClaim,
    User,
    UserAchievement,
)
from app.services.cheatsheet_pdf import build_cheatsheet_pdf
from app.services.anti_cheat import AntiCheatService
from app.services.class_tools import (
    CLASS_SUBJECTS,
    apply_pending_rewards,
    calculate_level,
    consume_hint_quota,
    decode_exam_subjects,
    decode_strict_subjects,
    encode_strict_subjects,
    ensure_report_token,
    exam_blocks_shop,
    exam_label,
    get_class_settings,
    is_ai_blocked,
    is_exam_active,
    primary_class_settings,
    set_exam_mode,
    weekly_csv,
    weekly_rows,
    weekly_summary,
)
from app.services.gigachat import make_hint, chat_with_gigachat
from app.config import get_settings
from app.database.session import get_db_session
from app.services.auth import get_current_user, require_teacher
from app.services.teacher_service import TeacherService
from app.services.gamification import GamificationService

router = APIRouter(tags=["webapp"])

class AchievementItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    code: str
    name_ru: str
    description_ru: str
    icon_url: Optional[str] = None
    unlocked: bool = False
    awarded_at: Optional[datetime] = None

class UserProfileResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    max_user_id: str
    display_name: str
    role: str
    balance_spasibki: int
    xp: int
    rank: str
    level: int
    helped_count: int = 0
    questions_count: int = 0
    rank_position: int = 1
    report_url: Optional[str] = None
    achievements: List[AchievementItem] = []

class LeaderboardEntry(BaseModel):
    rank_position: int
    max_user_id: str
    display_name: str
    rank: str
    xp: int
    balance_spasibki: int

class LeaderboardResponse(BaseModel):
    period: str
    items: List[LeaderboardEntry]

class RewardItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    created_by_teacher_id: str
    title: str
    description: str
    cost_spasibki: int
    is_active: bool

class ClaimRewardRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")
    reward_id: int
    student_max_id: Optional[Union[str, int]] = None
    user_id: Optional[Union[str, int]] = None

class ClaimRewardResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True, extra="ignore")
    id: int
    reward_id: int
    student_max_id: str
    status: str
    created_at: datetime
    success: Optional[bool] = True
    message: Optional[str] = "Запрос отправлен учителю"

class AdjustBalanceRequest(BaseModel):
    user_id: Optional[str] = None
    student_max_id: Optional[str] = None
    amount: Optional[int] = None
    delta_spasibki: Optional[int] = None
    delta_xp: Optional[int] = 0
    reason: str = "Корректировка учителем"

class OverrideAnswerRequest(BaseModel):
    answer_id: int
    status: str
    reason: str = "Решение учителя"

class ReviewClaimRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")
    claim_id: Optional[int] = None
    request_id: Optional[int] = None
    action: Optional[str] = None
    decision: Optional[str] = None

class CreateRewardRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")
    title: str
    description: str
    cost_spasibki: Optional[int] = None
    price_coins: Optional[int] = None
    teacher_id: Optional[int] = None
    icon: Optional[str] = None
    max_per_student: Optional[int] = 1
    stock: Optional[int] = None
    group_chat_id: Optional[str] = None

class AwardBadgeRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")
    student_id: Union[str, int]
    badge_code: Optional[str] = "teacher_choice"
    teacher_id: Optional[int] = None

class TeacherOverviewResponse(BaseModel):
    pending_claims: List[Dict[str, Any]]
    active_questions: List[Dict[str, Any]]
    disputed_answers: List[Dict[str, Any]]
    total_students: int
    statistics: Dict[str, int]
    recent_audit_logs: List[Dict[str, Any]]

@router.get("/profile")
async def get_profile(
    request: Request,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
):
    query = (
        select(Achievement, UserAchievement.awarded_at)
        .join(UserAchievement, Achievement.id == UserAchievement.achievement_id)
        .where(UserAchievement.user_max_id == current_user.max_user_id)
        .order_by(UserAchievement.awarded_at.desc())
    )
    res = await session.execute(query)
    achievements_data = [
        AchievementItem(
            id=ach.id,
            code=ach.code,
            name_ru=ach.name_ru,
            description_ru=ach.description_ru,
            icon_url=ach.icon_url,
            unlocked=True,
            awarded_at=awarded_at,
        )
        for ach, awarded_at in res.all()
    ]

    h_res = await session.execute(
        select(func.count(Answer.id)).where(
            Answer.author_max_id == current_user.max_user_id,
            Answer.status == "accepted",
        )
    )
    helped_count = h_res.scalar_one() or 0

    q_res = await session.execute(
        select(func.count(Question.id)).where(
            Question.author_max_id == current_user.max_user_id
        )
    )
    questions_count = q_res.scalar_one() or 0

    all_students_res = await session.execute(
        select(User).where(User.role == "student").order_by(User.xp.desc(), User.id.asc())
    )
    students = all_students_res.scalars().all()
    rank_pos = 1
    for idx, s in enumerate(students, 1):
        if s.max_user_id == current_user.max_user_id:
            rank_pos = idx
            break

    if apply_pending_rewards(current_user):
        session.add(current_user)
    token = ensure_report_token(current_user)
    class_row = await primary_class_settings(session)
    await session.commit()
    level_info = calculate_level(current_user.xp)
    return {
        "id": current_user.id,
        "max_user_id": current_user.max_user_id,
        "name": current_user.display_name,
        "display_name": current_user.display_name,
        "role": current_user.role,
        "coins": current_user.balance_spasibki,
        "balance_spasibki": current_user.balance_spasibki,
        "xp": current_user.xp,
        "rank": rank_pos,
        "rank_title": current_user.rank,
        "level": level_info,
        "helped_count": helped_count,
        "questions_count": questions_count,
        "rank_position": rank_pos,
        "pending_xp": current_user.pending_xp,
        "pending_coins": current_user.pending_coins,
        "night_mode": AntiCheatService.is_curfew_active(),
        "help_streak": current_user.help_streak or 0,
        "streak_best": current_user.streak_best or 0,
        "class_chat_id": 1,
        "report_url": str(request.base_url).rstrip("/") + "/p/" + token,
        "exam_mode": is_exam_active(class_row),
        "exam_label": exam_label(class_row),
        "shop_locked": exam_blocks_shop(class_row),
        "achievements": achievements_data,
    }

@router.get("/achievements")
async def get_achievements(
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
):
    all_res = await session.execute(select(Achievement).order_by(Achievement.id.asc()))
    all_achievements = all_res.scalars().all()
    ua_res = await session.execute(
        select(UserAchievement).where(UserAchievement.user_max_id == current_user.max_user_id)
    )
    user_unlocked = {ua.achievement_id: ua.awarded_at for ua in ua_res.scalars().all()}
    items = []
    for ach in all_achievements:
        awarded = user_unlocked.get(ach.id)
        items.append({
            "id": ach.id,
            "code": ach.code,
            "title": ach.name_ru,
            "name_ru": ach.name_ru,
            "description": ach.description_ru,
            "description_ru": ach.description_ru,
            "icon": ach.icon_url or "🎖",
            "icon_url": ach.icon_url,
            "xp_reward": 20,
            "coins_reward": 10,
            "unlocked": ach.id in user_unlocked,
            "is_unlocked": ach.id in user_unlocked,
            "unlocked_at": awarded.strftime("%d.%m.%Y") if awarded else "",
        })
    return items

@router.get("/leaderboard")
async def get_leaderboard(
    period: str = Query("all_time"),
    limit: int = Query(50),
    offset: int = Query(0),
    session: AsyncSession = Depends(get_db_session),
):
    all_time_query = (
        select(User)
        .where(User.role == "student")
        .order_by(User.xp.desc(), User.balance_spasibki.desc(), User.id.asc())
        .offset(offset)
        .limit(limit)
    )
    students = (await session.execute(all_time_query)).scalars().all()
    out = []
    for idx, s in enumerate(students, offset + 1):
        helped = (await session.execute(
            select(func.count(Answer.id)).where(Answer.author_max_id == s.max_user_id, Answer.status == "accepted")
        )).scalar_one() or 0
        lvl = calculate_level(s.xp)
        out.append({
            "rank": idx,
            "rank_position": idx,
            "max_user_id": int(s.max_user_id) if str(s.max_user_id).isdigit() else s.max_user_id,
            "name": s.display_name,
            "display_name": s.display_name,
            "level_title": lvl["title"],
            "rank_title": s.rank,
            "xp": s.xp,
            "coins": s.balance_spasibki,
            "helped_count": helped,
            "help_streak": s.help_streak or 0,
        })
    return out

@router.get("/user/classes")
async def get_user_classes(
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
):
    classes = (await session.execute(select(ClassSettings).order_by(ClassSettings.id.asc()))).scalars().all()
    if not classes:
        default_cls = ClassSettings(group_chat_id="1", title="11 «А» класс")
        session.add(default_cls)
        await session.commit()
        classes = [default_cls]

    teachers = {u.max_user_id: u.display_name for u in (await session.execute(select(User).where(User.role == "teacher"))).scalars().all()}
    out = []
    for c in classes:
        t_name = teachers.get(c.teacher_max_id) if c.teacher_max_id else "Учитель"
        title = c.title or (f"Класс {c.group_chat_id}" if c.group_chat_id != "1" else "11 «А» класс")
        out.append({
            "id": c.group_chat_id,
            "title": title,
            "teacher_name": t_name,
            "strict_mode": c.strict_mode,
            "exam_mode": is_exam_active(c),
        })
    return out


@router.get("/rewards")
async def get_rewards(
    class_id: Optional[str] = Query(None),
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
):
    query = select(Reward).where(Reward.is_active == True)
    if class_id:
        query = query.where(or_(Reward.group_chat_id == class_id, Reward.group_chat_id.is_(None)))
    res = await session.execute(query.order_by(Reward.cost_spasibki.asc()))
    rewards = res.scalars().all()
    claims_res = await session.execute(
        select(RewardClaim, Reward)
        .join(Reward, RewardClaim.reward_id == Reward.id)
        .where(RewardClaim.student_max_id == current_user.max_user_id)
        .order_by(RewardClaim.created_at.desc())
    )
    claims_all = claims_res.all()
    status_map = {"requested": "pending", "approved": "approved", "rejected": "rejected"}
    class_row = await get_class_settings(session, class_id) if class_id else await primary_class_settings(session)
    locked = exam_blocks_shop(class_row)

    rewards_out = []
    for r in rewards:
        user_claims_count = sum(1 for c, _ in claims_all if c.reward_id == r.id and c.status in ("requested", "approved"))
        max_limit = getattr(r, "max_per_student", 1) or 1
        stock = getattr(r, "stock", None)
        limit_reached = user_claims_count >= max_limit or (stock is not None and stock <= 0)
        rewards_out.append({
            "id": r.id,
            "title": r.title,
            "description": r.description,
            "price_coins": r.cost_spasibki,
            "cost_spasibki": r.cost_spasibki,
            "icon": "🎁",
            "is_active": r.is_active,
            "max_per_student": max_limit,
            "stock": stock,
            "user_claims_count": user_claims_count,
            "is_limit_reached": limit_reached,
        })

    return {
        "user_coins": current_user.balance_spasibki,
        "shop_locked": locked,
        "shop_message": exam_label(class_row) if locked else "",
        "rewards": rewards_out,
        "my_requests": [
            {
                "id": claim.id,
                "status": status_map.get(claim.status, claim.status),
                "reward_title": reward.title,
                "reward_icon": "🎁",
                "created_at": claim.created_at.strftime("%d.%m %H:%M") if claim.created_at else "",
            }
            for claim, reward in claims_all
        ],
    }

@router.post("/rewards/claim", response_model=ClaimRewardResponse)
@router.post("/rewards/buy", response_model=ClaimRewardResponse)
async def claim_reward(
    body: ClaimRewardRequest,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
):
    student_id = str(body.student_max_id or body.user_id or current_user.max_user_id)
    class_row = await primary_class_settings(session)
    if exam_blocks_shop(class_row):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=exam_label(class_row) or "Лавка закрыта на время контрольной",
        )
    try:
        claim = await TeacherService.claim_reward(
            session=session,
            student_max_id=student_id,
            reward_id=body.reward_id,
        )
        return {
            "id": claim.id,
            "reward_id": claim.reward_id,
            "student_max_id": claim.student_max_id,
            "status": claim.status,
            "created_at": claim.created_at,
            "success": True,
            "message": "Запрос отправлен учителю",
        }
    except ValueError as e:
        msg = str(e)
        if "not found" in msg.lower():
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=msg)
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=msg)

@router.get("/my-claims")
async def get_my_claims(
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
):
    res = await session.execute(
        select(RewardClaim, Reward)
        .join(Reward, RewardClaim.reward_id == Reward.id)
        .where(RewardClaim.student_max_id == current_user.max_user_id)
        .order_by(RewardClaim.created_at.desc())
    )
    return [
        {
            "id": claim.id,
            "reward_id": reward.id,
            "title": reward.title,
            "cost_spasibki": reward.cost_spasibki,
            "status": claim.status,
            "created_at": claim.created_at.isoformat() if claim.created_at else None,
        }
        for claim, reward in res.all()
    ]

@router.get("/teacher/overview", response_model=TeacherOverviewResponse)
async def get_teacher_overview(
    current_teacher: User = Depends(require_teacher),
    session: AsyncSession = Depends(get_db_session),
):
    overview = await TeacherService.get_teacher_overview(
        session=session,
        teacher_max_id=current_teacher.max_user_id,
    )

    audit_res = await session.execute(
        select(AuditLog).order_by(AuditLog.created_at.desc()).limit(20)
    )
    recent_audits = [
        {
            "id": a.id,
            "actor_max_id": a.actor_max_id,
            "target_user_max_id": a.target_user_max_id,
            "target_type": a.target_type,
            "target_id": a.target_id,
            "action": a.action,
            "details": a.details,
            "created_at": a.created_at.isoformat() if a.created_at else None,
        }
        for a in audit_res.scalars().all()
    ]

    total_q_res = await session.execute(select(func.count(Question.id)))
    total_q = total_q_res.scalar_one() or 0

    resolved_q_res = await session.execute(
        select(func.count(Question.id)).where(Question.status == "resolved")
    )
    resolved_q = resolved_q_res.scalar_one() or 0

    stats = {
        "total_questions": total_q,
        "resolved_questions": resolved_q,
        "open_questions": len(overview.get("active_questions", [])),
        "pending_claims": len(overview.get("pending_claims", [])),
        "total_students": overview.get("total_students", 0),
    }

    return TeacherOverviewResponse(
        pending_claims=overview.get("pending_claims", []),
        active_questions=overview.get("active_questions", []),
        disputed_answers=overview.get("disputed_answers", []),
        total_students=overview.get("total_students", 0),
        statistics=stats,
        recent_audit_logs=recent_audits,
    )

@router.post("/teacher/adjust-balance")
async def adjust_student_balance(
    body: AdjustBalanceRequest,
    current_teacher: User = Depends(require_teacher),
    session: AsyncSession = Depends(get_db_session),
):
    target_uid = body.user_id or body.student_max_id
    if not target_uid:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Missing user_id")
    delta_spasibki = body.delta_spasibki if body.delta_spasibki is not None else (body.amount if body.amount is not None else 0)
    delta_xp = body.delta_xp if body.delta_xp is not None else 0
    try:
        user = await TeacherService.adjust_student_balance(
            session=session,
            teacher_max_id=current_teacher.max_user_id,
            student_max_id=target_uid,
            delta_spasibki=delta_spasibki,
            delta_xp=delta_xp,
            reason=body.reason,
        )
        return {
            "status": "ok",
            "user_id": user.max_user_id,
            "balance_spasibki": user.balance_spasibki,
            "xp": user.xp,
            "rank": user.rank,
        }
    except ValueError as e:
        msg = str(e)
        if "not found" in msg.lower():
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=msg)
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=msg)

@router.post("/teacher/override-answer")
async def override_answer_status(
    body: OverrideAnswerRequest,
    current_teacher: User = Depends(require_teacher),
    session: AsyncSession = Depends(get_db_session),
):
    try:
        ans = await TeacherService.override_answer_status(
            session=session,
            teacher_max_id=current_teacher.max_user_id,
            answer_id=body.answer_id,
            new_status=body.status,
            reason=body.reason,
        )
        return {
            "status": "ok",
            "answer_id": ans.id,
            "new_status": ans.status,
            "question_id": ans.question_id,
        }
    except ValueError as e:
        msg = str(e)
        if "not found" in msg.lower():
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=msg)
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=msg)

@router.post("/teacher/review-claim")
async def review_reward_claim(
    body: ReviewClaimRequest,
    current_teacher: User = Depends(require_teacher),
    session: AsyncSession = Depends(get_db_session),
):
    decision_val = body.action or body.decision
    claim_id = body.claim_id or body.request_id
    if not decision_val or not claim_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Missing action or decision")
    try:
        claim = await TeacherService.review_reward_claim(
            session=session,
            teacher_max_id=current_teacher.max_user_id,
            claim_id=claim_id,
            decision=decision_val,
        )
        return {
            "status": "ok",
            "claim_id": claim.id,
            "new_status": claim.status,
            "success": True,
            "message": "Готово",
        }
    except ValueError as e:
        msg = str(e)
        if "not found" in msg.lower():
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=msg)
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=msg)

@router.post("/teacher/rewards", response_model=RewardItem)
async def create_reward(
    body: CreateRewardRequest,
    current_teacher: User = Depends(require_teacher),
    session: AsyncSession = Depends(get_db_session),
):
    try:
        reward = await TeacherService.create_reward(
            session=session,
            teacher_max_id=current_teacher.max_user_id,
            title=body.title,
            description=body.description,
            cost_spasibki=body.cost_spasibki or body.price_coins or 10,
        )
        return reward
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))

@router.post("/teacher/award-badge")
async def award_teacher_badge(
    body: AwardBadgeRequest,
    current_teacher: User = Depends(require_teacher),
    session: AsyncSession = Depends(get_db_session),
):
    badge = body.badge_code or "teacher_choice"
    student_id = str(body.student_id)
    new_codes = await GamificationService.evaluate_achievements(
        session=session,
        user_id=student_id,
        context={"manual_code": badge},
    )
    await TeacherService.adjust_student_balance(
        session=session,
        teacher_max_id=current_teacher.max_user_id,
        student_max_id=student_id,
        delta_spasibki=20,
        delta_xp=100,
        reason="Награда Выбор учителя",
    )
    return {"status": "ok", "awarded": new_codes, "success": True, "message": "Бейдж «Выбор учителя» вручён"}

@router.get("/students")
async def list_students(
    session: AsyncSession = Depends(get_db_session),
):
    res = await session.execute(
        select(User).where(User.role == "student").order_by(User.display_name.asc())
    )
    students = res.scalars().all()
    return [
        {
            "max_user_id": int(s.max_user_id) if str(s.max_user_id).isdigit() else s.max_user_id,
            "name": s.display_name,
            "display_name": s.display_name,
            "rank": s.rank,
            "balance_spasibki": s.balance_spasibki,
            "xp": s.xp,
        }
        for s in students
    ]


async def _cheatsheet_payload(session: AsyncSession, user: User) -> dict:
    res = await session.execute(
        select(Question).where(Question.is_exemplar == True).order_by(Question.created_at.desc())
    )
    questions = res.scalars().all()
    users = {u.max_user_id: u for u in (await session.execute(select(User))).scalars().all()}
    grouped: dict[str, list] = {}
    for q in questions:
        helper_name = None
        if q.resolved_answer_id:
            ans = (await session.execute(select(Answer).where(Answer.id == q.resolved_answer_id))).scalar_one_or_none()
            if ans:
                helper = users.get(ans.author_max_id)
                helper_name = helper.display_name if helper else None
        author = users.get(q.author_max_id)
        item = {
            "id": q.id,
            "text": q.text,
            "subject": q.subject or "Учёба",
            "exemplar_note": q.exemplar_note,
            "author_name": author.display_name if author else q.author_max_id,
            "helper_name": helper_name,
        }
        grouped.setdefault(item["subject"], []).append(item)
    return {
        "subjects": [
            {"subject": name, "items": grouped[name]}
            for name in list(CLASS_SUBJECTS) + [k for k in grouped if k not in CLASS_SUBJECTS]
            if grouped.get(name)
        ]
    }


@router.get("/cheatsheet")
async def get_cheatsheet(
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
):
    return await _cheatsheet_payload(session, current_user)


@router.get("/cheatsheet.pdf")
async def download_cheatsheet_pdf(
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
):
    payload = await _cheatsheet_payload(session, current_user)
    pdf = build_cheatsheet_pdf(payload["subjects"], "Класс")
    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={"Content-Disposition": "attachment; filename=shpargalka-klassa.pdf"},
    )


class StrictModeRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")
    enabled: Optional[bool] = None
    subjects: Optional[list[str]] = None
    group_chat_id: Optional[str] = None
    teacher_id: Optional[int] = None


@router.get("/teacher/class")
async def teacher_class(
    current_teacher: User = Depends(require_teacher),
    session: AsyncSession = Depends(get_db_session),
):
    group_id = "1"
    q = (await session.execute(select(Question.group_chat_id).order_by(Question.id.desc()).limit(1))).first()
    if q:
        group_id = q[0]
    settings_row = await get_class_settings(session, group_id)
    return {
        "strict_mode": settings_row.strict_mode,
        "strict_subjects": decode_strict_subjects(settings_row),
        "exam_mode": is_exam_active(settings_row),
        "exam_subjects": decode_exam_subjects(settings_row),
        "exam_until": settings_row.exam_until,
        "exam_label": exam_label(settings_row),
        "digest_enabled": getattr(settings_row, "digest_enabled", True) is not False,
        "subjects": list(CLASS_SUBJECTS),
        "class_chat_id": group_id,
    }


@router.post("/teacher/strict")
async def set_strict(
    body: StrictModeRequest,
    current_teacher: User = Depends(require_teacher),
    session: AsyncSession = Depends(get_db_session),
):
    q = (await session.execute(select(Question.group_chat_id).order_by(Question.id.desc()).limit(1))).first()
    group_id = q[0] if q else "1"
    settings_row = await get_class_settings(session, group_id)
    if body.subjects is not None:
        all_on, encoded = encode_strict_subjects(body.subjects)
        settings_row.strict_mode = all_on
        settings_row.strict_subjects = encoded
    elif body.enabled is True:
        settings_row.strict_mode = True
        settings_row.strict_subjects = None
    else:
        settings_row.strict_mode = False
        settings_row.strict_subjects = None
    await session.commit()
    return {
        "success": True,
        "strict_mode": settings_row.strict_mode,
        "strict_subjects": decode_strict_subjects(settings_row),
    }


class ExamModeRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")
    enabled: Optional[bool] = None
    subjects: Optional[list[str]] = None
    days: Optional[int] = 1
    teacher_id: Optional[int] = None


@router.post("/teacher/exam")
async def set_exam(
    body: ExamModeRequest,
    current_teacher: User = Depends(require_teacher),
    session: AsyncSession = Depends(get_db_session),
):
    q = (await session.execute(select(Question.group_chat_id).order_by(Question.id.desc()).limit(1))).first()
    group_id = q[0] if q else "1"
    settings_row = await get_class_settings(session, group_id)
    if body.enabled is False:
        set_exam_mode(settings_row, None)
    elif body.subjects:
        set_exam_mode(settings_row, body.subjects, days=body.days or 1)
    else:
        set_exam_mode(settings_row, ["*"], days=body.days or 1)
    await session.commit()
    return {
        "success": True,
        "exam_mode": is_exam_active(settings_row),
        "exam_subjects": decode_exam_subjects(settings_row),
        "exam_label": exam_label(settings_row),
    }


class ExemplarRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")
    question_id: int
    note: Optional[str] = None
    teacher_id: Optional[int] = None


@router.post("/teacher/exemplar")
async def mark_exemplar(
    body: ExemplarRequest,
    current_teacher: User = Depends(require_teacher),
    session: AsyncSession = Depends(get_db_session),
):
    q = (await session.execute(select(Question).where(Question.id == body.question_id))).scalar_one_or_none()
    if not q:
        raise HTTPException(status_code=404, detail="Вопрос не найден")
    q.is_exemplar = True
    if body.note:
        q.exemplar_note = body.note.strip()
    await session.commit()
    return {"success": True, "message": "Добавлено в шпаргалку класса"}


@router.get("/teacher/export")
async def teacher_export(
    current_teacher: User = Depends(require_teacher),
    session: AsyncSession = Depends(get_db_session),
):
    rows = await weekly_rows(session)
    return weekly_summary(rows)


@router.get("/teacher/export.csv")
async def teacher_export_csv(
    current_teacher: User = Depends(require_teacher),
    session: AsyncSession = Depends(get_db_session),
):
    rows = await weekly_rows(session)
    content = "\ufeff" + weekly_csv(rows)
    return Response(
        content=content,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": "attachment; filename=liga-week.csv"},
    )


class QuestionActionRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")
    user_id: Optional[int] = None
    question_id: int
    quality: Optional[str] = None


async def _format_question(session: AsyncSession, q: Question) -> dict:
    users = {u.max_user_id: u for u in (await session.execute(select(User))).scalars().all()}
    author = users.get(q.author_max_id)
    helper = None
    ans = None
    if q.resolved_answer_id:
        ans = (await session.execute(select(Answer).where(Answer.id == q.resolved_answer_id))).scalar_one_or_none()
        if ans:
            helper = users.get(ans.author_max_id)
    elif q.status == "open":
        pending = (await session.execute(
            select(Answer).where(Answer.question_id == q.id, Answer.status == "pending").order_by(Answer.created_at.desc())
        )).scalars().first()
        if pending:
            helper = users.get(pending.author_max_id)
    created = q.created_at.strftime("%d.%m %H:%M") if q.created_at else ""
    author_id = author.max_user_id if author else q.author_max_id
    helper_id = helper.max_user_id if helper else None
    return {
        "id": q.id,
        "text": q.text,
        "subject": q.subject or "Учёба",
        "status": "answered" if q.status == "resolved" else q.status,
        "created_at": created,
        "author_id": int(author_id) if str(author_id).isdigit() else author_id,
        "author_name": author.display_name if author else "Ученик",
        "helper_id": int(helper_id) if helper_id and str(helper_id).isdigit() else helper_id,
        "helper_name": helper.display_name if helper else None,
        "image_url": q.image_url,
        "is_exemplar": bool(q.is_exemplar),
        "exemplar_note": q.exemplar_note,
        "open": q.status == "open",
        "quality": ans.quality if ans else None,
        "hint_blocked": is_ai_blocked(await get_class_settings(session, q.group_chat_id), q.subject),
    }


@router.get("/questions")
async def list_questions(
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
):
    items = (await session.execute(select(Question).order_by(Question.created_at.desc()).limit(50))).scalars().all()
    return [await _format_question(session, q) for q in items]


class CreateQuestionRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")
    user_id: Optional[Union[str, int]] = None
    text: str
    subject: Optional[str] = "Учёба"
    image_url: Optional[str] = None


@router.post("/questions")
async def create_question_webapp(
    body: CreateQuestionRequest,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
):
    text = (body.text or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="Текст вопроса не может быть пустым")

    author_uid = str(body.user_id or current_user.max_user_id)
    class_row = await primary_class_settings(session)
    group_cid = class_row.group_chat_id or "1"

    question = Question(
        group_chat_id=group_cid,
        group_message_id=f"web-{author_uid}-{int(datetime.now(timezone.utc).timestamp())}",
        author_max_id=author_uid,
        text=text,
        subject=body.subject or "Учёба",
        image_url=body.image_url,
        status="open",
    )
    session.add(question)
    await session.commit()

    # Post to class chat if bot token is active and chat_id is valid
    try:
        from app.services.max_client import get_bot_client
        client = get_bot_client()
        if client.token and group_cid not in ("0", "1", ""):
            cid = int(group_cid) if str(group_cid).lstrip("-").isdigit() else None
            if cid:
                resp = await client.send_message(
                    chat_id=cid,
                    text=(
                        f"❓ Вопрос от {current_user.display_name} · {question.subject}:\n"
                        f"{text}\n\n"
                        "💬 Ответьте реплаем на это сообщение, чтобы помочь однокласснику!"
                    ),
                )
                bot_msg = resp.get("message", {})
                bot_mid = (
                    bot_msg.get("body", {}).get("mid")
                    if isinstance(bot_msg.get("body"), dict)
                    else None
                ) or bot_msg.get("id") or bot_msg.get("mid")
                if bot_mid:
                    question.group_message_id = str(bot_mid)
                    await session.commit()
    except Exception:
        pass

    return {
        "success": True,
        "message": "Вопрос успешно опубликован в классе!",
        "question_id": question.id,
    }


@router.post("/questions/help")
async def mark_helped(data: QuestionActionRequest, session: AsyncSession = Depends(get_db_session)):
    q = (await session.execute(select(Question).where(Question.id == data.question_id))).scalar_one_or_none()
    if not q:
        raise HTTPException(status_code=404, detail="Вопрос не найден")
    if q.status != "open":
        return {"success": False, "error": "Вопрос уже закрыт"}
    helper_id = str(data.user_id)
    if helper_id == str(q.author_max_id):
        return {"success": False, "error": "Нельзя помочь самому себе"}
    existing = (await session.execute(
        select(Answer).where(Answer.question_id == q.id, Answer.author_max_id == helper_id, Answer.status == "pending")
    )).scalar_one_or_none()
    if not existing:
        session.add(Answer(
            question_id=q.id,
            author_max_id=helper_id,
            group_message_id=f"web-{q.id}-{helper_id}",
            text="Помощь в чате класса (отметка из мини-приложения)",
            status="pending",
        ))
        await session.commit()

        # Send DM notification to author in MAX
        try:
            from app.services.max_client import get_bot_client
            client = get_bot_client()
            if client.token and q.author_max_id:
                author_uid = int(q.author_max_id) if str(q.author_max_id).isdigit() else None
                helper_user = (await session.execute(select(User).where(User.max_user_id == helper_id))).scalar_one_or_none()
                h_name = helper_user.display_name if helper_user else "Одноклассник"
                if author_uid:
                    await client.send_message(
                        user_id=author_uid,
                        text=(
                            f"🤝 {h_name} отметил, что помог с вашим вопросом «{q.text[:60]}…»!\n\n"
                            "Откройте Лигу Помощников, чтобы подтвердить ответ и начислить спасибки 🪙."
                        ),
                    )
        except Exception:
            pass
    return {"success": True, "message": "Вы отметились помощником. Автор может сказать спасибо."}


@router.post("/questions/thanks")
async def say_thanks(data: QuestionActionRequest, session: AsyncSession = Depends(get_db_session)):
    q = (await session.execute(select(Question).where(Question.id == data.question_id))).scalar_one_or_none()
    if not q:
        raise HTTPException(status_code=404, detail="Вопрос не найден")
    if str(q.author_max_id) != str(data.user_id):
        return {"success": False, "error": "Только автор может сказать спасибо"}
    if q.status != "open":
        return {"success": False, "error": "Благодарность уже начислена"}
    answer = (await session.execute(
        select(Answer).where(Answer.question_id == q.id, Answer.status == "pending").order_by(Answer.created_at.desc())
    )).scalars().first()
    if not answer:
        return {"success": False, "error": "Сначала одноклассник отвечает реплаем или жмёт «Я помог»"}
    quality = data.quality or "clear"
    spasibki, xp = await GamificationService.award_gratitude(session, q, answer, quality=quality)
    if quality == "error":
        return {"success": True, "message": f"Отметили ошибку. Помощник получил +{xp} XP, вопрос ещё открыт", "unlocked": []}
    return {"success": True, "message": f"Спасибо! Помощник получил +{xp} XP", "unlocked": []}


class GigaChatMessage(BaseModel):
    role: str
    content: str


class GigaChatRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")
    user_id: Optional[Union[str, int]] = None
    message: str
    history: Optional[List[GigaChatMessage]] = None
    class_id: Optional[str] = None


@router.post("/gigachat/chat")
async def gigachat_chat(
    body: GigaChatRequest,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
):
    text = (body.message or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="Сообщение не может быть пустым")

    target_class_id = str(body.class_id or "1")
    settings_row = await get_class_settings(session, target_class_id)
    if is_exam_active(settings_row):
        return {
            "status": "blocked",
            "reply": "Внимание: в данном классе включён режим контрольной работы. ГигаЧат временно недоступен.",
            "role": "assistant",
        }

    history_dicts = []
    if body.history:
        for m in body.history:
            history_dicts.append({"role": m.role, "content": m.content})
    history_dicts.append({"role": "user", "content": text})

    reply = await chat_with_gigachat(history_dicts, student_name=current_user.display_name)
    return {
        "status": "ok",
        "reply": reply,
        "role": "assistant",
    }


@router.post("/questions/hint")
async def web_hint(data: QuestionActionRequest, session: AsyncSession = Depends(get_db_session)):
    user = (await session.execute(select(User).where(User.max_user_id == str(data.user_id)))).scalar_one_or_none()
    q = (await session.execute(select(Question).where(Question.id == data.question_id))).scalar_one_or_none()
    if not user or not q:
        raise HTTPException(status_code=404, detail="Не найдено")
    settings_row = await get_class_settings(session, q.group_chat_id)
    if is_ai_blocked(settings_row, q.subject):
        return {"success": False, "error": f"Сегодня по теме «{q.subject}» ГигаЧат выключен учителем."}
    settings = get_settings()
    allowed, left = consume_hint_quota(user, settings.HINT_DAILY_LIMIT)
    await session.commit()
    if not allowed:
        return {"success": False, "error": "Лимит подсказок на сегодня исчерпан"}
    hint = await make_hint(q.text)
    return {"success": True, "hint": hint, "left": left}


@router.get("/teacher/requests")
async def teacher_requests(current_teacher: User = Depends(require_teacher), session: AsyncSession = Depends(get_db_session)):
    overview = await TeacherService.get_teacher_overview(session=session, teacher_max_id=current_teacher.max_user_id)
    out = []
    for c in overview.get("pending_claims", []):
        out.append({
            "id": c.get("claim_id") or c.get("id"),
            "student_name": c.get("student_name"),
            "price_coins": c.get("cost_spasibki"),
            "reward_icon": "🎁",
            "reward_title": c.get("reward_title"),
            "created_at": c.get("created_at") or "",
        })
    return out


@router.post("/teacher/request/review")
async def teacher_request_review_alias(body: ReviewClaimRequest, current_teacher: User = Depends(require_teacher), session: AsyncSession = Depends(get_db_session)):
    return await review_reward_claim(body, current_teacher, session)


@router.post("/teacher/reward/create")
async def teacher_reward_create_alias(body: CreateRewardRequest, current_teacher: User = Depends(require_teacher), session: AsyncSession = Depends(get_db_session)):
    reward = await TeacherService.create_reward(
        session=session,
        teacher_max_id=current_teacher.max_user_id,
        title=body.title,
        description=body.description,
        cost_spasibki=body.cost_spasibki or body.price_coins or 10,
        max_per_student=body.max_per_student or 1,
        stock=body.stock,
        group_chat_id=body.group_chat_id,
    )
    return {"success": True, "message": "Награда добавлена в лавку", "id": reward.id}


@router.post("/teacher/award_badge")
async def teacher_award_alias(body: AwardBadgeRequest, current_teacher: User = Depends(require_teacher), session: AsyncSession = Depends(get_db_session)):
    body.student_id = str(body.student_id)
    return await award_teacher_badge(body, current_teacher, session)


@router.get("/teacher/unanswered")
async def teacher_unanswered(current_teacher: User = Depends(require_teacher), session: AsyncSession = Depends(get_db_session)):
    items = (await session.execute(select(Question).where(Question.status == "open").order_by(Question.created_at.asc()).limit(30))).scalars().all()
    return [await _format_question(session, q) for q in items]


@router.post("/teacher/digest")
async def teacher_digest(current_teacher: User = Depends(require_teacher), session: AsyncSession = Depends(get_db_session)):
    from app.services.jobs import send_class_digest
    q = (await session.execute(select(Question.group_chat_id).order_by(Question.id.desc()).limit(1))).first()
    chat_id = q[0] if q else "1"
    text = await send_class_digest(chat_id)
    return {"success": True, "message": "Дайджест сформирован", "text": text}


@router.get("/demo/users")
async def demo_users(session: AsyncSession = Depends(get_db_session)):
    users = (await session.execute(select(User).order_by(User.id))).scalars().all()
    return [{"max_user_id": u.max_user_id, "name": u.display_name, "role": u.role, "xp": u.xp} for u in users]

