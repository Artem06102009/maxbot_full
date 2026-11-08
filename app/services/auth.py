from typing import Optional
from fastapi import Depends, Header, HTTPException, Query, status
from sqlmodel import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.database.models import User
from app.database.session import get_db_session

async def get_current_user(
    x_user_id: Optional[str] = Header(None, alias="X-User-Id"),
    user_id: Optional[str] = Query(None),
    display_name: Optional[str] = Query(None),
    session: AsyncSession = Depends(get_db_session),
) -> User:
    uid = str(x_user_id or user_id or "").strip()
    if not uid:
        first_res = await session.execute(select(User).order_by(User.id.asc()).limit(1))
        first_user = first_res.scalar_one_or_none()
        if first_user:
            return first_user
        uid = "user_1"

    res = await session.execute(select(User).where(User.max_user_id == uid))
    user = res.scalar_one_or_none()

    clean_name = (display_name or "").strip()

    if not user:
        final_name = clean_name if clean_name else f"Участник {uid}"
        user = User(
            max_user_id=uid,
            display_name=final_name,
            role="student",
            balance_spasibki=10,
            xp=100,
            rank="Любитель",
        )
        session.add(user)
        await session.commit()
        await session.refresh(user)
    else:
        if clean_name and (user.display_name.startswith("Ученик ") or user.display_name.startswith("Школьник ") or user.display_name.startswith("Участник ")):
            user.display_name = clean_name
            await session.commit()
            await session.refresh(user)

    return user

async def require_teacher(
    current_user: User = Depends(get_current_user),
) -> User:
    if current_user.role not in ("teacher", "admin"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Доступ разрешен только учителям",
        )
    return current_user
