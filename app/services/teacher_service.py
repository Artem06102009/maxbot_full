from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from app.database.models import (
    Achievement,
    Answer,
    AuditLog,
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
            raise PermissionError("Only teachers or administrators can perform this action")
        return teacher

    @classmethod
    async def claim_reward(
        cls,
        session: AsyncSession,
        student_max_id: str,
        reward_id: int,
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
            raise ValueError("Недостаточно спасибок для покупки")

        # Check limit per student
        max_limit = getattr(reward, "max_per_student", 1) or 1
        claims_count = (await session.execute(
            select(func.count(RewardClaim.id)).where(
                RewardClaim.reward_id == reward.id,
                RewardClaim.student_max_id == student_max_id,
                RewardClaim.status.in_(("requested", "approved")),
            )
        )).scalar_one() or 0
        if claims_count >= max_limit:
            raise ValueError(f"Лимит на эту награду исчерпан (максимум {max_limit} шт. на ученика)")

        # Check stock
        if getattr(reward, "stock", None) is not None:
            if reward.stock <= 0:
                raise ValueError("Награда закончилась в лавке")
            reward.stock -= 1

        student.balance_spasibki -= reward.cost_spasibki

        claim = RewardClaim(
            reward_id=reward.id,
            student_max_id=student_max_id,
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
                if getattr(reward, "stock", None) is not None:
                    reward.stock += 1

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
    async def override_answer_status(
        cls,
        session: AsyncSession,
        teacher_max_id: str,
        answer_id: int,
        new_status: str,
        reason: str = "",
    ) -> Answer:
        await cls._require_teacher(session, teacher_max_id)

        ans_res = await session.execute(
            select(Answer).where(Answer.id == answer_id)
        )
        answer = ans_res.scalar_one_or_none()
        if answer is None:
            raise ValueError("Answer not found")

        answer.status = new_status
        audit = AuditLog(
            actor_max_id=teacher_max_id,
            target_user_max_id=answer.author_max_id,
            target_type="answer",
            target_id=str(answer.id),
            action="override_answer_status",
            details=f"new_status={new_status},reason={reason}",
            created_at=datetime.now(timezone.utc),
        )
        session.add(audit)
        await session.commit()
        return answer

    @classmethod
    async def create_reward(
        cls,
        session: AsyncSession,
        teacher_max_id: str,
        title: str,
        description: str,
        cost_spasibki: int,
        max_per_student: int = 1,
        stock: Optional[int] = None,
        group_chat_id: Optional[str] = None,
    ) -> Reward:
        await cls._require_teacher(session, teacher_max_id)

        reward = Reward(
            created_by_teacher_id=teacher_max_id,
            title=title,
            description=description,
            cost_spasibki=cost_spasibki,
            max_per_student=max_per_student if max_per_student > 0 else 1,
            stock=stock,
            group_chat_id=group_chat_id,
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
            details=f"title={title},cost={cost_spasibki}",
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
    ) -> Dict[str, Any]:
        await cls._require_teacher(session, teacher_max_id)

        claims_res = await session.execute(
            select(RewardClaim, Reward, User)
            .join(Reward, RewardClaim.reward_id == Reward.id)
            .join(User, RewardClaim.student_max_id == User.max_user_id)
            .where(RewardClaim.status == "requested")
            .order_by(RewardClaim.created_at.desc())
        )
        pending_claims = [
            {
                "claim_id": c.id,
                "reward_id": r.id,
                "reward_title": r.title,
                "cost_spasibki": r.cost_spasibki,
                "student_max_id": u.max_user_id,
                "student_name": u.display_name,
                "status": c.status,
                "created_at": c.created_at.isoformat() if c.created_at else None,
            }
            for c, r, u in claims_res.all()
        ]

        q_res = await session.execute(
            select(Question, User)
            .join(User, Question.author_max_id == User.max_user_id)
            .where(Question.status == "open")
            .order_by(Question.created_at.desc())
            .limit(20)
        )
        active_questions = [
            {
                "id": q.id,
                "author_name": u.display_name,
                "author_max_id": u.max_user_id,
                "text": q.text,
                "subject": q.subject,
                "created_at": q.created_at.isoformat() if q.created_at else None,
            }
            for q, u in q_res.all()
        ]

        ans_res = await session.execute(
            select(Answer, Question, User)
            .join(Question, Answer.question_id == Question.id)
            .join(User, Answer.author_max_id == User.max_user_id)
            .where(Answer.status == "rejected")
            .order_by(Answer.created_at.desc())
            .limit(20)
        )
        disputed_answers = [
            {
                "answer_id": a.id,
                "question_id": q.id,
                "question_text": q.text,
                "helper_name": u.display_name,
                "helper_max_id": u.max_user_id,
                "answer_text": a.text,
                "status": a.status,
            }
            for a, q, u in ans_res.all()
        ]

        students_count_res = await session.execute(
            select(func.count(User.id)).where(User.role == "student")
        )
        total_students = students_count_res.scalar_one() or 0

        return {
            "pending_claims": pending_claims,
            "active_questions": active_questions,
            "disputed_answers": disputed_answers,
            "total_students": total_students,
        }

