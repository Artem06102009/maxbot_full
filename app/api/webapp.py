from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict
from sqlalchemy import delete, func, not_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from app.database.models import (
    Achievement,
    Answer,
    AuditLog,
    Chat,
    ChatMember,
    Question,
    Reward,
    RewardClaim,
    User,
    UserAchievement,
)
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

class ChatItem(BaseModel):
    id: str
    title: str
    role: str = "student"

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
    chats: List[ChatItem] = []
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
    chat_id: Optional[str] = None
    title: str
    description: str
    cost_spasibki: int
    is_active: bool

class ClaimRewardRequest(BaseModel):
    reward_id: int
    student_max_id: Optional[str] = None
    user_id: Optional[str] = None
    chat_id: Optional[str] = None

class ClaimRewardResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    reward_id: int
    student_max_id: str
    status: str
    created_at: datetime

class ReviewClaimRequest(BaseModel):
    claim_id: int
    action: Optional[str] = None
    decision: Optional[str] = None

class CreateRewardRequest(BaseModel):
    title: str
    description: str
    cost_spasibki: int
    chat_id: Optional[str] = None

class SyncUserRequest(BaseModel):
    user_id: str
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    username: Optional[str] = None

class AwardBadgeRequest(BaseModel):
    student_id: str
    badge_code: Optional[str] = "teacher_choice"

class TeacherOverviewResponse(BaseModel):
    pending_claims: List[Dict[str, Any]]
    active_questions: List[Dict[str, Any]]
    total_students: int

@router.get("/profile", response_model=UserProfileResponse)
async def get_profile(
    chat_id: Optional[str] = Query(None),
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
):
    bad_chats_q = select(Chat.max_chat_id).where(
        or_(Chat.title == "Диалог", Chat.title.ilike("%диалог%"))
    )
    bad_ids = (await session.execute(bad_chats_q)).scalars().all()
    if bad_ids:
        await session.execute(delete(ChatMember).where(ChatMember.chat_max_id.in_(bad_ids)))
        await session.execute(delete(Chat).where(Chat.max_chat_id.in_(bad_ids)))
        await session.commit()

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

    memberships_res = await session.execute(
        select(Chat, ChatMember.role)
        .join(ChatMember, Chat.max_chat_id == ChatMember.chat_max_id)
        .where(
            ChatMember.user_max_id == current_user.max_user_id,
            Chat.title != "Диалог",
            not_(Chat.title.ilike("%диалог%")),
        )
        .order_by(Chat.title.asc())
    )
    user_chats_map = {
        c.max_chat_id: ChatItem(id=c.max_chat_id, title=c.title, role=r)
        for c, r in memberships_res.all()
    }

    owned_chats_res = await session.execute(
        select(Chat).where(
            Chat.owner_id == current_user.max_user_id,
            Chat.title != "Диалог",
            not_(Chat.title.ilike("%диалог%")),
        )
    )
    for c in owned_chats_res.scalars().all():
        if c.max_chat_id not in user_chats_map:
            user_chats_map[c.max_chat_id] = ChatItem(id=c.max_chat_id, title=c.title, role="teacher")
        else:
            user_chats_map[c.max_chat_id].role = "teacher"

    if current_user.role in ("teacher", "admin"):
        all_chats_res = await session.execute(
            select(Chat).where(Chat.title != "Диалог", not_(Chat.title.ilike("%диалог%"))).order_by(Chat.title.asc())
        )
        for c in all_chats_res.scalars().all():
            if c.max_chat_id not in user_chats_map:
                user_chats_map[c.max_chat_id] = ChatItem(id=c.max_chat_id, title=c.title, role="teacher")

    user_chats = list(user_chats_map.values())
    if not user_chats:
        user_chats = [
            ChatItem(id="my_class", title="Мой учебный класс", role=current_user.role)
        ]

    current_role = current_user.role
    if chat_id and chat_id != "all":
        for uc in user_chats:
            if uc.id == chat_id:
                current_role = uc.role
                break
    elif user_chats:
        current_role = user_chats[0].role

    level = (current_user.xp // 100) + 1
    return UserProfileResponse(
        id=current_user.id,
        max_user_id=current_user.max_user_id,
        display_name=current_user.display_name,
        role=current_role,
        balance_spasibki=current_user.balance_spasibki,
        xp=current_user.xp,
        rank=current_user.rank,
        level=level,
        helped_count=helped_count,
        questions_count=questions_count,
        rank_position=rank_pos,
        chats=user_chats,
        achievements=achievements_data,
    )

@router.post("/sync-user")
async def sync_user(
    body: SyncUserRequest,
    session: AsyncSession = Depends(get_db_session),
):
    uid = str(body.user_id).strip()
    full_name = f"{body.first_name or ''} {body.last_name or ''}".strip() or body.username or f"Участник {uid}"
    res = await session.execute(select(User).where(User.max_user_id == uid))
    user = res.scalar_one_or_none()
    if not user:
        user = User(
            max_user_id=uid,
            display_name=full_name,
            role="student",
            balance_spasibki=10,
            xp=100,
            rank="Любитель",
        )
        session.add(user)
    else:
        user.display_name = full_name
    await session.commit()
    return {"status": "ok", "display_name": full_name}

@router.get("/chats", response_model=List[ChatItem])
async def get_chats(
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
):
    bad_chats_q = select(Chat.max_chat_id).where(
        or_(Chat.title == "Диалог", Chat.title.ilike("%диалог%"))
    )
    bad_ids = (await session.execute(bad_chats_q)).scalars().all()
    if bad_ids:
        await session.execute(delete(ChatMember).where(ChatMember.chat_max_id.in_(bad_ids)))
        await session.execute(delete(Chat).where(Chat.max_chat_id.in_(bad_ids)))
        await session.commit()

    mem_res = await session.execute(
        select(Chat, ChatMember.role)
        .join(ChatMember, Chat.max_chat_id == ChatMember.chat_max_id)
        .where(
            ChatMember.user_max_id == current_user.max_user_id,
            Chat.title != "Диалог",
            not_(Chat.title.ilike("%диалог%")),
        )
        .order_by(Chat.title.asc())
    )
    user_chats_map = {
        c.max_chat_id: ChatItem(id=c.max_chat_id, title=c.title, role=r)
        for c, r in mem_res.all()
    }

    owned_chats_res = await session.execute(
        select(Chat).where(
            Chat.owner_id == current_user.max_user_id,
            Chat.title != "Диалог",
            not_(Chat.title.ilike("%диалог%")),
        )
    )
    for c in owned_chats_res.scalars().all():
        if c.max_chat_id not in user_chats_map:
            user_chats_map[c.max_chat_id] = ChatItem(id=c.max_chat_id, title=c.title, role="teacher")
        else:
            user_chats_map[c.max_chat_id].role = "teacher"

    if current_user.role in ("teacher", "admin"):
        all_chats_res = await session.execute(
            select(Chat).where(Chat.title != "Диалог", not_(Chat.title.ilike("%диалог%"))).order_by(Chat.title.asc())
        )
        for c in all_chats_res.scalars().all():
            if c.max_chat_id not in user_chats_map:
                user_chats_map[c.max_chat_id] = ChatItem(id=c.max_chat_id, title=c.title, role="teacher")

    my_chats = list(user_chats_map.values())
    if my_chats:
        return my_chats
    return [ChatItem(id="my_class", title="Мой учебный класс", role=current_user.role)]

@router.get("/achievements", response_model=List[AchievementItem])
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

    return [
        AchievementItem(
            id=ach.id,
            code=ach.code,
            name_ru=ach.name_ru,
            description_ru=ach.description_ru,
            icon_url=ach.icon_url,
            unlocked=(ach.id in user_unlocked),
            awarded_at=user_unlocked.get(ach.id),
        )
        for ach in all_achievements
    ]

@router.get("/leaderboard", response_model=LeaderboardResponse)
async def get_leaderboard(
    period: str = Query("all_time"),
    chat_id: Optional[str] = Query(None),
    limit: int = Query(50),
    offset: int = Query(0),
    session: AsyncSession = Depends(get_db_session),
):
    if period == "weekly":
        since_dt = datetime.now(timezone.utc) - timedelta(days=7)
        weekly_query = (
            select(
                User.max_user_id,
                User.display_name,
                User.rank,
                User.balance_spasibki,
                User.xp,
                (func.count(Answer.id) * 50).label("week_xp"),
            )
            .join(Answer, Answer.author_max_id == User.max_user_id)
            .where(User.role == "student", Answer.status == "accepted", Answer.created_at >= since_dt)
        )
        if chat_id and chat_id != "all":
            weekly_query = weekly_query.join(ChatMember, ChatMember.user_max_id == User.max_user_id).where(ChatMember.chat_max_id == chat_id)

        weekly_query = (
            weekly_query.group_by(User.id)
            .order_by(func.count(Answer.id).desc(), User.xp.desc())
            .offset(offset)
            .limit(limit)
        )
        res = await session.execute(weekly_query)
        rows = res.all()
        if rows:
            items = [
                LeaderboardEntry(
                    rank_position=idx,
                    max_user_id=row[0],
                    display_name=row[1],
                    rank=row[2],
                    xp=row[4],
                    balance_spasibki=row[3],
                )
                for idx, row in enumerate(rows, offset + 1)
            ]
            return LeaderboardResponse(period=period, items=items)

    all_time_query = select(User).where(User.role == "student")
    if chat_id and chat_id != "all":
        all_time_query = all_time_query.join(ChatMember, ChatMember.user_max_id == User.max_user_id).where(ChatMember.chat_max_id == chat_id)

    all_time_query = all_time_query.order_by(User.xp.desc(), User.balance_spasibki.desc(), User.id.asc()).offset(offset).limit(limit)
    res = await session.execute(all_time_query)
    students = res.scalars().all()
    items = [
        LeaderboardEntry(
            rank_position=idx,
            max_user_id=s.max_user_id,
            display_name=s.display_name,
            rank=s.rank,
            xp=s.xp,
            balance_spasibki=s.balance_spasibki,
        )
        for idx, s in enumerate(students, offset + 1)
    ]
    return LeaderboardResponse(period=period, items=items)

@router.get("/rewards", response_model=List[RewardItem])
async def get_rewards(
    chat_id: Optional[str] = Query(None),
    session: AsyncSession = Depends(get_db_session),
):
    query = select(Reward).where(Reward.is_active == True)
    if chat_id and chat_id != "all":
        query = query.where(or_(Reward.chat_id == chat_id, Reward.chat_id.is_(None)))
    query = query.order_by(Reward.cost_spasibki.asc())
    res = await session.execute(query)
    return res.scalars().all()

@router.post("/rewards/claim", response_model=ClaimRewardResponse)
@router.post("/rewards/buy", response_model=ClaimRewardResponse)
async def claim_reward(
    body: ClaimRewardRequest,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
):
    student_id = body.student_max_id or body.user_id or current_user.max_user_id
    try:
        claim = await TeacherService.claim_reward(
            session=session,
            student_max_id=student_id,
            reward_id=body.reward_id,
            chat_id=body.chat_id,
        )
        return claim
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
            "chat_id": claim.chat_id,
            "status": claim.status,
            "created_at": claim.created_at.isoformat() if claim.created_at else None,
        }
        for claim, reward in res.all()
    ]

@router.get("/teacher/overview", response_model=TeacherOverviewResponse)
async def get_teacher_overview(
    chat_id: Optional[str] = Query(None),
    current_teacher: User = Depends(require_teacher),
    session: AsyncSession = Depends(get_db_session),
):
    overview = await TeacherService.get_teacher_overview(
        session=session,
        teacher_max_id=current_teacher.max_user_id,
        chat_id=chat_id if chat_id != "all" else None,
    )
    return TeacherOverviewResponse(
        pending_claims=overview.get("pending_claims", []),
        active_questions=overview.get("active_questions", []),
        total_students=overview.get("total_students", 0),
    )

@router.post("/teacher/review-claim")
async def review_reward_claim(
    body: ReviewClaimRequest,
    current_teacher: User = Depends(require_teacher),
    session: AsyncSession = Depends(get_db_session),
):
    decision_val = body.action or body.decision
    if not decision_val:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Missing action or decision")
    try:
        claim = await TeacherService.review_reward_claim(
            session=session,
            teacher_max_id=current_teacher.max_user_id,
            claim_id=body.claim_id,
            decision=decision_val,
        )
        return {
            "status": "ok",
            "claim_id": claim.id,
            "new_status": claim.status,
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
            cost_spasibki=body.cost_spasibki,
            chat_id=body.chat_id if body.chat_id != "all" else None,
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
    new_codes = await GamificationService.evaluate_achievements(
        session=session,
        user_id=body.student_id,
        context={"manual_code": badge},
    )
    await TeacherService.adjust_student_balance(
        session=session,
        teacher_max_id=current_teacher.max_user_id,
        student_max_id=body.student_id,
        delta_spasibki=20,
        delta_xp=100,
        reason="Награда Выбор учителя",
    )
    return {"status": "ok", "awarded": new_codes}

@router.get("/students")
async def list_students(
    chat_id: Optional[str] = Query(None),
    session: AsyncSession = Depends(get_db_session),
):
    query = select(User).where(User.role == "student")
    if chat_id and chat_id != "all":
        query = query.join(ChatMember, ChatMember.user_max_id == User.max_user_id).where(ChatMember.chat_max_id == chat_id)
    query = query.order_by(User.display_name.asc())
    res = await session.execute(query)
    students = res.scalars().all()
    return [
        {
            "max_user_id": s.max_user_id,
            "display_name": s.display_name,
            "rank": s.rank,
            "balance_spasibki": s.balance_spasibki,
            "xp": s.xp,
        }
        for s in students
    ]
