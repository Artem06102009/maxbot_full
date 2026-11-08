from collections import Counter
from datetime import date, datetime, time, timedelta, timezone
from typing import Any, List, Optional, Tuple
from sqlmodel import select, func, or_, and_
from sqlalchemy.ext.asyncio import AsyncSession
from app.config import get_settings
from app.database.models import (
    Achievement,
    Answer,
    AuditLog,
    Question,
    User,
    UserAchievement,
)

settings = get_settings()
SCHOOL_TZ = timezone(timedelta(hours=3))

def ensure_utc(dt: Optional[datetime]) -> datetime:
    if dt is None:
        return datetime.now(timezone.utc)
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)

def to_school_date(dt: Optional[datetime]) -> date:
    utc_dt = ensure_utc(dt)
    return utc_dt.astimezone(SCHOOL_TZ).date()

class AntiCheatService:
    @staticmethod
    def validate_answer_eligibility(question_author_id: Any, helper_id: Any) -> bool:
        if question_author_id is None or helper_id is None:
            return False
        q_author = str(question_author_id).strip().lower()
        h_author = str(helper_id).strip().lower()
        return bool(q_author and h_author)

    @staticmethod
    def is_curfew_active(
        dt: Optional[datetime] = None,
        start_hour: Optional[int] = None,
        end_hour: Optional[int] = None,
    ) -> bool:
        if dt is None:
            dt = datetime.now(SCHOOL_TZ)
        elif dt.tzinfo is None:
            dt = dt.replace(tzinfo=SCHOOL_TZ)
        else:
            dt = dt.astimezone(SCHOOL_TZ)

        current_time = dt.time()
        start_val = start_hour if start_hour is not None else settings.CURFEW_START_HOUR
        end_val = end_hour if end_hour is not None else settings.CURFEW_END_HOUR
        start = time(start_val, 0)
        end = time(end_val, 0)

        if start > end:
            return current_time >= start or current_time < end
        elif start < end:
            return start <= current_time < end
        return False

    @staticmethod
    async def check_daily_gratitude_limit(
        session: AsyncSession,
        author_id: Optional[str] = None,
        helper_id: Optional[str] = None,
        date: Optional[date] = None,
        *,
        author_max_id: Optional[str] = None,
        helper_max_id: Optional[str] = None,
        check_date: Optional[date] = None,
    ) -> bool:
        author = str(author_id or author_max_id or "").strip().lower()
        helper = str(helper_id or helper_max_id or "").strip().lower()
        target_date_val = date if date is not None else check_date
        if target_date_val is None:
            target_date_val = datetime.now(SCHOOL_TZ).date()
        elif isinstance(target_date_val, datetime):
            if target_date_val.tzinfo is None:
                target_date_val = target_date_val.replace(tzinfo=SCHOOL_TZ).date()
            else:
                target_date_val = target_date_val.astimezone(SCHOOL_TZ).date()

        start_school = datetime.combine(target_date_val, time.min).replace(tzinfo=SCHOOL_TZ)
        end_school = datetime.combine(target_date_val, time.max).replace(tzinfo=SCHOOL_TZ)
        start_dt = start_school.astimezone(timezone.utc).replace(tzinfo=None)
        end_dt = end_school.astimezone(timezone.utc).replace(tzinfo=None)

        q_answers = (
            select(func.count(Answer.id))
            .join(Question, Answer.question_id == Question.id)
            .where(
                func.lower(Question.author_max_id) == author,
                func.lower(Answer.author_max_id) == helper,
                Answer.status == "accepted",
                or_(
                    and_(Answer.created_at >= start_dt, Answer.created_at <= end_dt),
                    and_(
                        Question.closed_at.is_not(None),
                        Question.closed_at >= start_dt,
                        Question.closed_at <= end_dt,
                    ),
                ),
            )
        )
        res = await session.execute(q_answers)
        if res.scalar_one() >= 1:
            return True

        q_audit = (
            select(func.count(AuditLog.id))
            .where(
                func.lower(AuditLog.actor_max_id) == author,
                AuditLog.action == "award_gratitude",
                AuditLog.created_at >= start_dt,
                AuditLog.created_at <= end_dt,
            )
        )
        res_audit = await session.execute(q_audit)
        return res_audit.scalar_one() >= 1

class GamificationService:
    RANKS = (
        (4000, "Легенда школы"),
        (2000, "Сверхразум"),
        (1000, "Гений"),
        (500, "Эксперт"),
        (250, "Знаток"),
        (100, "Любитель"),
        (0, "Новичок"),
    )

    @classmethod
    def calculate_rank(cls, xp: int) -> str:
        for threshold, rank_name in cls.RANKS:
            if xp >= threshold:
                return rank_name
        return "Новичок"

    @classmethod
    async def award_gratitude(
        cls,
        session: AsyncSession,
        question: Optional[Question] = None,
        answer: Optional[Answer] = None,
        now: Optional[datetime] = None,
        *,
        question_id: Optional[int] = None,
        answer_id: Optional[int] = None,
        author_id: Optional[str] = None,
        helper_id: Optional[str] = None,
    ) -> Tuple[int, int]:
        if question is None and question_id is not None:
            res_q = await session.execute(select(Question).where(Question.id == question_id))
            question = res_q.scalar_one_or_none()
        if answer is None and answer_id is not None:
            res_a = await session.execute(select(Answer).where(Answer.id == answer_id))
            answer = res_a.scalar_one_or_none()
        if question is None or answer is None:
            raise ValueError("Question or answer not found")

        if not AntiCheatService.validate_answer_eligibility(
            question.author_max_id, answer.author_max_id
        ):
            raise ValueError("Self-reward is prohibited")
        if answer.status not in ("pending", "open"):
            raise ValueError("Answer is already processed")

        event_now = ensure_utc(now)

        is_curfew = AntiCheatService.is_curfew_active(event_now)
        is_daily_limit = await AntiCheatService.check_daily_gratitude_limit(
            session,
            question.author_max_id,
            answer.author_max_id,
            to_school_date(event_now),
        )

        if is_curfew or is_daily_limit:
            delta_spasibki = 0
            delta_xp = 0
        else:
            delta_spasibki = 1
            delta_xp = 50

        user_query = select(User).where(User.max_user_id == answer.author_max_id)
        user_res = await session.execute(user_query)
        helper = user_res.scalar_one_or_none()
        if helper is not None:
            helper.balance_spasibki += delta_spasibki
            helper.xp += delta_xp
            helper.rank = cls.calculate_rank(helper.xp)

        answer.status = "accepted"
        question.status = "resolved"
        question.resolved_answer_id = answer.id
        question.closed_at = event_now

        audit = AuditLog(
            actor_max_id=question.author_max_id,
            target_user_max_id=answer.author_max_id,
            target_type="answer",
            target_id=str(answer.id),
            action="award_gratitude",
            details=f"spasibki={delta_spasibki},xp={delta_xp},curfew={is_curfew},limit={is_daily_limit}",
            created_at=event_now,
        )
        session.add(audit)

        await cls.evaluate_achievements(
            session,
            answer.author_max_id,
            context={"question": question, "answer": answer, "now": event_now},
        )
        await cls.evaluate_achievements(
            session,
            question.author_max_id,
            context={"question": question, "answer": answer, "now": event_now},
        )

        await session.commit()
        return delta_spasibki, delta_xp

    @classmethod
    async def evaluate_achievements(
        cls,
        session: AsyncSession,
        user_id: str,
        context: Optional[dict[str, Any]] = None,
    ) -> List[str]:
        now = ensure_utc(context.get("now") if context else None)
        achievements_res = await session.execute(select(Achievement))
        all_achievements = achievements_res.scalars().all()
        if not all_achievements:
            return []

        awarded_res = await session.execute(
            select(UserAchievement.achievement_id).where(
                UserAchievement.user_max_id == user_id
            )
        )
        awarded_ids = set(awarded_res.scalars().all())

        pending_achievements = [a for a in all_achievements if a.id not in awarded_ids]
        if not pending_achievements:
            return []

        pending_codes = {a.code: a for a in pending_achievements}
        newly_awarded_codes: List[str] = []

        answers_query = (
            select(Answer, Question)
            .join(Question, Answer.question_id == Question.id)
            .where(
                Answer.author_max_id == user_id,
                Answer.status == "accepted",
            )
        )
        answers_res = await session.execute(answers_query)
        accepted_pairs = answers_res.all()
        accepted_count = len(accepted_pairs)

        questions_query = select(Question).where(Question.author_max_id == user_id)
        questions_res = await session.execute(questions_query)
        user_questions = questions_res.scalars().all()
        user_questions_count = len(user_questions)

        if "first_step" in pending_codes and accepted_count >= 1:
            newly_awarded_codes.append("first_step")

        if "fast_aid" in pending_codes:
            for ans, q in accepted_pairs:
                diff = (ensure_utc(ans.created_at) - ensure_utc(q.created_at)).total_seconds()
                if 0 <= diff <= 600:
                    newly_awarded_codes.append("fast_aid")
                    break

        if "rescuer" in pending_codes:
            for ans, q in accepted_pairs:
                diff = (ensure_utc(ans.created_at) - ensure_utc(q.created_at)).total_seconds()
                if diff >= 86400:
                    newly_awarded_codes.append("rescuer")
                    break

        if "marathoner" in pending_codes:
            dates = [to_school_date(ans.created_at) for ans, _ in accepted_pairs]
            date_counts = Counter(dates)
            if any(cnt >= 5 for cnt in date_counts.values()):
                newly_awarded_codes.append("marathoner")

        if "week_streak" in pending_codes:
            unique_dates = sorted(set(to_school_date(ans.created_at) for ans, _ in accepted_pairs))
            streak = 1
            max_streak = 1 if unique_dates else 0
            for i in range(1, len(unique_dates)):
                if unique_dates[i] - unique_dates[i - 1] == timedelta(days=1):
                    streak += 1
                    if streak > max_streak:
                        max_streak = streak
                else:
                    streak = 1
            if max_streak >= 7:
                newly_awarded_codes.append("week_streak")

        if "mentor" in pending_codes:
            distinct_authors = set(q.author_max_id for _, q in accepted_pairs)
            if len(distinct_authors) >= 10:
                newly_awarded_codes.append("mentor")

        if "subject_expert" in pending_codes:
            subjects = [q.subject for _, q in accepted_pairs if q.subject]
            subj_counts = Counter(subjects)
            if any(cnt >= 10 for cnt in subj_counts.values()):
                newly_awarded_codes.append("subject_expert")

        if "universal" in pending_codes:
            distinct_subjects = set(q.subject for _, q in accepted_pairs if q.subject)
            if len(distinct_subjects) >= 5:
                newly_awarded_codes.append("universal")

        if "class_legend" in pending_codes and accepted_count >= 100:
            newly_awarded_codes.append("class_legend")

        if "brave_question" in pending_codes and user_questions_count >= 1:
            newly_awarded_codes.append("brave_question")

        if "curious" in pending_codes and user_questions_count >= 10:
            newly_awarded_codes.append("curious")

        if "grateful" in pending_codes:
            resolved_count = sum(1 for q in user_questions if q.status == "resolved")
            if resolved_count >= 10:
                newly_awarded_codes.append("grateful")

        if "week_leader" in pending_codes:
            one_week_ago = now - timedelta(days=7)
            leader_query = (
                select(Answer.author_max_id, func.count(Answer.id).label("cnt"))
                .where(Answer.status == "accepted", Answer.created_at >= one_week_ago)
                .group_by(Answer.author_max_id)
                .order_by(func.count(Answer.id).desc())
            )
            leader_res = await session.execute(leader_query)
            top_row = leader_res.first()
            if top_row is not None and top_row[0] == user_id and top_row[1] > 0:
                newly_awarded_codes.append("week_leader")

        if "teacher_choice" in pending_codes:
            if context and context.get("manual_code") == "teacher_choice":
                newly_awarded_codes.append("teacher_choice")

        for code in newly_awarded_codes:
            ach = pending_codes[code]
            ua = UserAchievement(
                user_max_id=user_id,
                achievement_id=ach.id,
                awarded_at=now,
            )
            session.add(ua)
            audit = AuditLog(
                actor_max_id=user_id,
                target_user_max_id=user_id,
                target_type="achievement",
                target_id=code,
                action="unlock_achievement",
                details=ach.name_ru,
                created_at=now,
            )
            session.add(audit)

        if newly_awarded_codes:
            await session.flush()

        return newly_awarded_codes

