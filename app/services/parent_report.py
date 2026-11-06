from datetime import timedelta
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import Answer, Question, User
from app.services.class_tools import utc_now


async def parent_week_payload(session: AsyncSession, user: User) -> dict:
    start = utc_now() - timedelta(days=7)
    asked_res = await session.execute(select(Question).where(Question.author_max_id == user.max_user_id))
    asked = []
    helped = []
    for q in asked_res.scalars().all():
        created = q.created_at.replace(tzinfo=q.created_at.tzinfo or __import__("datetime").timezone.utc) if q.created_at else None
        if created and created.tzinfo is None:
            from datetime import timezone
            created = created.replace(tzinfo=timezone.utc)
        if created and created < start:
            continue
        asked.append(q)
    ans_res = await session.execute(
        select(Answer, Question)
        .join(Question, Answer.question_id == Question.id)
        .where(Answer.author_max_id == user.max_user_id, Answer.status == "accepted")
    )
    for answer, question in ans_res.all():
        created = answer.created_at
        if created and created.tzinfo is None:
            from datetime import timezone
            created = created.replace(tzinfo=timezone.utc)
        if created and created < start:
            continue
        helped.append(question)
    return {
        "name": user.display_name,
        "rank": user.rank,
        "xp": user.xp,
        "asked": asked,
        "helped": helped,
    }


def parent_report_html(data: dict) -> str:
    def esc(value: Optional[str]) -> str:
        return (
            str(value or "")
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
        )

    asked = data["asked"] or []
    helped = data["helped"] or []
    asked_html = "".join(
        f"<li><b>{esc(q.subject or 'Учёба')}</b> — {esc(q.text)} "
        f"({'разобрали' if q.status == 'resolved' else 'ещё открыт'})</li>"
        for q in asked
    ) or "<li>На этой неделе вопросов не задавал(а).</li>"
    helped_html = "".join(
        f"<li><b>{esc(q.subject or 'Учёба')}</b> — {esc(q.text)}</li>"
        for q in helped
    ) or "<li>На этой неделе пока не отметился как помощник.</li>"
    return f"""<!DOCTYPE html>
<html lang="ru">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Неделя в Лиге Помощников</title>
  <style>
    body {{ font-family: -apple-system, Segoe UI, sans-serif; background:#eceef3; color:#2c2d2e; margin:0; }}
    .wrap {{ max-width: 640px; margin: 0 auto; padding: 24px 16px 48px; }}
    .card {{ background:#fff; border-radius:16px; padding:18px; margin-bottom:12px; }}
    .brand {{ color:#2d72d9; font-weight:800; }}
    .muted {{ color:#818c99; font-size:14px; }}
    .stats {{ display:grid; grid-template-columns: 1fr 1fr; gap:8px; }}
    .stat {{ background:#eef4ff; border-radius:12px; padding:12px; text-align:center; }}
    .stat b {{ display:block; font-size:20px; color:#2d72d9; }}
  </style>
</head>
<body>
  <div class="wrap">
    <div class="brand">Лига Помощников</div>
    <h1>{esc(data["name"])}</h1>
    <p class="muted">Что делал(а) ребёнок в чате класса за 7 дней. Это не переписка, только активность.</p>
    <div class="card stats">
      <div class="stat"><b>{esc(data["rank"])}</b>звание</div>
      <div class="stat"><b>{data["xp"]}</b>опыт</div>
      <div class="stat"><b>{len(asked)}</b>вопросов за неделю</div>
      <div class="stat"><b>{len(helped)}</b>помог одноклассникам</div>
    </div>
    <div class="card"><h3>Спрашивал(а)</h3><ul>{asked_html}</ul></div>
    <div class="card"><h3>Помогал(а)</h3><ul>{helped_html}</ul></div>
  </div>
</body>
</html>"""
