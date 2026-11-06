from typing import Optional
from fastapi import Depends, Header, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.database.models import User
from app.database.session import get_db_session

async def get_current_user(
    x_user_id: Optional[str] = Header(None, alias="X-User-Id"),
    user_id: Optional[str] = Query(None),
    teacher_id: Optional[str] = Query(None),
    session: AsyncSession = Depends(get_db_session),
) -> User:
    uid = str(x_user_id or user_id or teacher_id or "1001").strip()
    res = await session.execute(select(User).where(User.max_user_id == uid))
    user = res.scalar_one_or_none()
    if not user:
        user = User(
            max_user_id=uid,
            display_name=f"Ученик {uid}",
            role="student",
            balance_spasibki=5,
            xp=50,
            rank="Новичок",
        )
        session.add(user)
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
