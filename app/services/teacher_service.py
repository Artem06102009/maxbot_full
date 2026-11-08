from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from sqlalchemy import func, or_, select
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
from app.services.gamification import GamificationService

class TeacherService:
    @staticmethod
    async def _require_teacher(session: AsyncSession, teacher_max_id: str) -> User:
        user_res = await session.execute(
            select(User).where(User.max_user_id == teacher_max_id)
        )
        teacher = user_res.scalar_one_or_none()
        if teacher is None:
            raise PermissionError("Teacher user not found")
        if teacher.role not in ("teacher", "admin"):
            member_res = await session.execute(
                select(ChatMember).where(ChatMember.user_max_id == teacher_max_id, ChatMember.role == "teacher")
            )
            if member_res.scalars().first() is None:
                raise PermissionError("Only teachers or administrators can perform this action")
        return teacher

    @classmethod
    async def claim_reward(
        cls,
        session: AsyncSession,
        student_max_id: str,
        reward_id: int,
        chat_id: Optional[str] = None,
    ) -> RewardClaim:
        student_res = await session.execute(
            select(User).where(User.max_user_id == student_max_id)
        )
        student = student_res.scalar_one_or_none()
        if student is None:
            raise ValueError("Student not found")

        reward_res = await session.execute(
            select(Reward).where(Reward.id == reward_id)
        )
        reward = reward_res.scalar_one_or_none()
        if reward is None:
            raise ValueError("Reward not found")
        if not reward.is_active:
            raise ValueError("Reward is inactive")

        if student.balance_spasibki < reward.cost_spasibki:
            raise ValueError("Недостаточно спасибок для обмена")

        student.balance_spasibki -= reward.cost_spasibki

        claim = RewardClaim(
            reward_id=reward.id,
            student_max_id=student_max_id,
            chat_id=chat_id or reward.chat_id,
            status="requested",
            created_at=datetime.now(timezone.utc),
        )
        session.add(claim)
        await session.flush()

        audit = AuditLog(
            actor_max_id=student_max_id,
            target_user_max_id=student_max_id,
            target_type="reward_claim",
            target_id=str(claim.id),
            action="claim_reward",
            details=f"reward_id={reward.id},cost={reward.cost_spasibki}",
            created_at=datetime.now(timezone.utc),
        )
        session.add(audit)
        await session.commit()
        return claim

    @classmethod
    async def review_reward_claim(
        cls,
        session: AsyncSession,
        teacher_max_id: str,
        claim_id: int,
        decision: str,
    ) -> RewardClaim:
        await cls._require_teacher(session, teacher_max_id)

        claim_res = await session.execute(
            select(RewardClaim).where(RewardClaim.id == claim_id)
        )
        claim = claim_res.scalar_one_or_none()
        if claim is None:
            raise ValueError("Reward claim not found")
        if claim.status != "requested":
            raise ValueError("Reward claim is already processed")

        if decision not in ("approved", "rejected", "approve", "reject"):
            raise ValueError("Invalid decision status")

        normalized = "approved" if decision in ("approved", "approve") else "rejected"
        claim.status = normalized

        if normalized == "rejected":
            rew_res = await session.execute(
                select(Reward).where(Reward.id == claim.reward_id)
            )
            reward = rew_res.scalar_one_or_none()
            if reward is not None:
                stu_res = await session.execute(
                    select(User).where(User.max_user_id == claim.student_max_id)
                )
                student = stu_res.scalar_one_or_none()
                if student is not None:
                    student.balance_spasibki += reward.cost_spasibki

        audit = AuditLog(
            actor_max_id=teacher_max_id,
            target_user_max_id=claim.student_max_id,
            target_type="reward_claim",
            target_id=str(claim.id),
            action=f"review_reward_{normalized}",
            details=f"claim_id={claim.id},decision={normalized}",
            created_at=datetime.now(timezone.utc),
        )
        session.add(audit)
        await session.commit()
        return claim

    @classmethod
    async def adjust_student_balance(
        cls,
        session: AsyncSession,
        teacher_max_id: str,
        student_max_id: str,
        delta_spasibki: int,
        delta_xp: int = 0,
        reason: str = "",
    ) -> User:
        await cls._require_teacher(session, teacher_max_id)

        stu_res = await session.execute(
            select(User).where(User.max_user_id == student_max_id)
        )
        student = stu_res.scalar_one_or_none()
        if student is None:
            raise ValueError("Student not found")

        student.balance_spasibki = max(0, student.balance_spasibki + delta_spasibki)
        student.xp = max(0, student.xp + delta_xp)
        student.rank = GamificationService.calculate_rank(student.xp)

        audit = AuditLog(
            actor_max_id=teacher_max_id,
            target_user_max_id=student_max_id,
            target_type="user",
            target_id=student_max_id,
            action="adjust_balance",
            details=f"delta_spasibki={delta_spasibki},delta_xp={delta_xp},reason={reason}",
            created_at=datetime.now(timezone.utc),
        )
        session.add(audit)
        await session.commit()
        return student

    @classmethod
    async def create_reward(
        cls,
        session: AsyncSession,
        teacher_max_id: str,
        title: str,
        description: str,
        cost_spasibki: int,
        chat_id: Optional[str] = None,
    ) -> Reward:
        await cls._require_teacher(session, teacher_max_id)

        reward = Reward(
            created_by_teacher_id=teacher_max_id,
            chat_id=chat_id,
            title=title,
            description=description,
            cost_spasibki=cost_spasibki,
            is_active=True,
        )
        session.add(reward)
        await session.flush()

        audit = AuditLog(
            actor_max_id=teacher_max_id,
            target_user_max_id=None,
            target_type="reward",
            target_id=str(reward.id),
            action="create_reward",
            details=f"title={title},cost={cost_spasibki},chat_id={chat_id}",
            created_at=datetime.now(timezone.utc),
        )
        session.add(audit)
        await session.commit()
        return reward

    @classmethod
    async def get_teacher_overview(
        cls,
        session: AsyncSession,
        teacher_max_id: str,
        chat_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        await cls._require_teacher(session, teacher_max_id)

        claims_query = (
            select(RewardClaim, Reward, User)
            .join(Reward, RewardClaim.reward_id == Reward.id)
            .join(User, RewardClaim.student_max_id == User.max_user_id)
            .where(RewardClaim.status == "requested")
        )
        if chat_id:
            claims_query = claims_query.where(or_(RewardClaim.chat_id == chat_id, Reward.chat_id == chat_id))
        claims_query = claims_query.order_by(RewardClaim.created_at.desc())

        claims_res = await session.execute(claims_query)
        pending_claims = [
            {
                "claim_id": c.id,
                "reward_id": r.id,
                "reward_title": r.title,
                "cost_spasibki": r.cost_spasibki,
                "student_max_id": u.max_user_id,
                "student_name": u.display_name,
                "chat_id": c.chat_id or r.chat_id,
                "status": c.status,
                "created_at": c.created_at.isoformat() if c.created_at else None,
            }
            for c, r, u in claims_res.all()
        ]

        q_query = (
            select(Question, User)
            .join(User, Question.author_max_id == User.max_user_id)
            .where(Question.status == "open")
        )
        if chat_id:
            q_query = q_query.where(Question.group_chat_id == chat_id)
        q_query = q_query.order_by(Question.created_at.desc()).limit(20)

        q_res = await session.execute(q_query)
        active_questions = [
            {
                "id": q.id,
                "author_name": u.display_name,
                "author_max_id": u.max_user_id,
                "text": q.text,
                "subject": q.subject,
                "chat_title": q.group_chat_title,
                "created_at": q.created_at.isoformat() if q.created_at else None,
            }
            for q, u in q_res.all()
        ]

        students_query = select(func.count(User.id)).where(User.role == "student")
        if chat_id:
            students_query = (
                select(func.count(ChatMember.id))
                .where(ChatMember.chat_max_id == chat_id, ChatMember.role == "student")
            )
        students_count_res = await session.execute(students_query)
        total_students = students_count_res.scalar_one() or 0

        return {
            "pending_claims": pending_claims,
            "active_questions": active_questions,
            "total_students": total_students,
        }
