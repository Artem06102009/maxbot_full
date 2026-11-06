import csv
import io
import secrets
from datetime import timedelta

from sqlmodel import Session, select

from app.logic import ensure_aware, utc_now
from app.models import Question, User, calculate_level
from config import BASE_URL


def ensure_report_token(user: User) -> str:
    if user.report_token:
        return user.report_token
    user.report_token = secrets.token_urlsafe(10)
    return user.report_token


def report_url(user: User) -> str:
    token = ensure_report_token(user)
    return f"{BASE_URL.rstrip('/')}/p/{token}"


def week_window():
    end = utc_now()
    start = end - timedelta(days=7)
    return start, end


def class_questions(session: Session, chat_id: int | None) -> list[Question]:
    query = select(Question)
    if chat_id:
        query = query.where(Question.chat_id == chat_id)
    return session.exec(query).all()


def weekly_export_rows(session: Session, chat_id: int | None) -> list[dict]:
    start, _ = week_window()
    rows = []
    for q in class_questions(session, chat_id):
        if ensure_aware(q.created_at) < start:
            continue
        author = session.exec(select(User).where(User.id == q.author_id)).first()
        helper = session.exec(select(User).where(User.id == q.helper_id)).first() if q.helper_id else None
        rows.append({
            "date": q.created_at.strftime("%d.%m.%Y %H:%M") if q.created_at else "",
            "subject": q.subject,
            "author": author.name if author else "",
            "helper": helper.name if helper else "",
            "text": q.text,
            "status": q.status,
            "exemplar": "да" if q.is_exemplar else "нет",
        })
    rows.sort(key=lambda item: item["date"], reverse=True)
    return rows


def weekly_export_csv(session: Session, chat_id: int | None) -> str:
    rows = weekly_export_rows(session, chat_id)
    buf = io.StringIO()
    writer = csv.DictWriter(
        buf,
        fieldnames=["date", "subject", "author", "helper", "text", "status", "exemplar"],
        extrasaction="ignore",
    )
    writer.writeheader()
    writer.writerows(rows)
    return buf.getvalue()


def weekly_summary(session: Session, chat_id: int | None) -> dict:
    rows = weekly_export_rows(session, chat_id)
    subjects: dict[str, int] = {}
    helpers: dict[str, int] = {}
    askers: dict[str, int] = {}
    for row in rows:
        subjects[row["subject"]] = subjects.get(row["subject"], 0) + 1
        if row["author"]:
            askers[row["author"]] = askers.get(row["author"], 0) + 1
        if row["helper"]:
            helpers[row["helper"]] = helpers.get(row["helper"], 0) + 1
    return {
        "questions": len(rows),
        "answered": sum(1 for row in rows if row["status"] == "answered"),
        "subjects": sorted(subjects.items(), key=lambda x: -x[1]),
        "top_helpers": sorted(helpers.items(), key=lambda x: -x[1])[:5],
        "top_askers": sorted(askers.items(), key=lambda x: -x[1])[:5],
        "rows": rows,
    }


def parent_week_payload(session: Session, user: User) -> dict:
    start, _ = week_window()
    asked = []
    helped = []
    for q in class_questions(session, user.chat_id):
        created = ensure_aware(q.created_at)
        if created < start:
            continue
        if q.author_id == user.id:
            asked.append(q)
        if q.helper_id == user.id:
            helped.append(q)
    level = calculate_level(user.xp)
    return {
        "name": user.name,
        "role": user.role,
        "level": level,
        "xp": user.xp,
        "coins": user.coins,
        "helped_total": user.helped_count,
        "asked_week": [
            {"text": q.text, "subject": q.subject, "status": q.status}
            for q in asked
        ],
        "helped_week": [
            {"text": q.text, "subject": q.subject}
            for q in helped
        ],
    }


def parent_report_html(data: dict) -> str:
    asked = data["asked_week"] or []
    helped = data["helped_week"] or []
    asked_html = "".join(
        f"<li><b>{_esc(item['subject'])}</b> — {_esc(item['text'])} "
        f"({ 'разбрали' if item['status']=='answered' else 'ещё открыт' })</li>"
        for item in asked
    ) or "<li>На этой неделе вопросов не задавал(а).</li>"
    helped_html = "".join(
        f"<li><b>{_esc(item['subject'])}</b> — {_esc(item['text'])}</li>"
        for item in helped
    ) or "<li>На этой неделе пока не отметился как помощник.</li>"
    lvl = data["level"]
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
    .brand {{ color:#7b61ff; font-weight:800; }}
    h1 {{ font-size:22px; margin: 8px 0 4px; }}
    .muted {{ color:#818c99; font-size:14px; }}
    .stats {{ display:grid; grid-template-columns: 1fr 1fr; gap:8px; }}
    .stat {{ background:#f7f4ff; border-radius:12px; padding:12px; text-align:center; }}
    .stat b {{ display:block; font-size:20px; color:#6246f0; }}
    ul {{ padding-left:18px; line-height:1.45; }}
  </style>
</head>
<body>
  <div class="wrap">
    <div class="brand">Лига Помощников</div>
    <h1>{_esc(data["name"])}</h1>
    <p class="muted">Что делал(а) ребёнок в чате класса за 7 дней. Это не переписка, только активность.</p>
    <div class="card stats">
      <div class="stat"><b>{_esc(lvl["title"])}</b>звание</div>
      <div class="stat"><b>{data["xp"]}</b>опыт</div>
      <div class="stat"><b>{len(asked)}</b>вопросов за неделю</div>
      <div class="stat"><b>{len(helped)}</b>помог одноклассникам</div>
    </div>
    <div class="card">
      <h3>Спрашивал(а)</h3>
      <ul>{asked_html}</ul>
    </div>
    <div class="card">
      <h3>Помогал(а)</h3>
      <ul>{helped_html}</ul>
    </div>
    <p class="muted">Ссылка постоянная, её можно отправить родителю из профиля ученика.</p>
  </div>
</body>
</html>"""


def _esc(value: str) -> str:
    return (
        str(value or "")
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )
