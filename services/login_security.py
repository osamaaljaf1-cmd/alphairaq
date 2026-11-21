"""Login security helpers: the password rule, the login history, and logging an account out of every
device (its older sessions stop working)."""
import logging
import re
from datetime import datetime, timezone
from typing import Optional

from fastapi import Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models.auth_security import Login_events, Session_resets

logger = logging.getLogger(__name__)

MIN_LENGTH = 8


def password_problem(password: Optional[str], username: Optional[str] = None) -> Optional[str]:
    """Why a new password is not acceptable (Arabic, for the user), or None when it is fine:
    at least 8 characters, letters and digits both, and not the username."""
    pw = password or ""
    if len(pw) < MIN_LENGTH:
        return f"كلمة المرور يجب أن تكون {MIN_LENGTH} أحرف على الأقل"
    if not re.search(r"[^\W\d_]", pw) or not re.search(r"\d", pw):
        return "كلمة المرور يجب أن تحتوي على حروف وأرقام معاً"
    if username and pw.strip().lower() == username.strip().lower():
        return "كلمة المرور لا تكون نفس اسم الدخول"
    if len(set(pw)) < 4:
        return "كلمة المرور سهلة التخمين، اختر أحرفاً وأرقاماً مختلفة"
    return None


async def record_login(
    db: AsyncSession, account: str, ok: bool, reason: str, request: Optional[Request], user_id: Optional[str] = None
) -> None:
    """Add a row to the login history. Never fails the login itself."""
    try:
        ip = None
        agent = None
        if request is not None:
            from core.login_throttle import client_address

            ip = client_address(request)
            agent = (request.headers.get("user-agent") or "")[:300]
        db.add(Login_events(account=(account or "")[:120], user_id=user_id, ok=ok, reason=reason, ip=ip, user_agent=agent, created_at=datetime.now(timezone.utc)))
        await db.commit()
    except Exception:
        await db.rollback()
        logger.warning("Could not record the login event", exc_info=True)


async def end_all_sessions(db: AsyncSession, user_id: str) -> datetime:
    """Every session of this account issued until now stops working (tokens carry their issue time).
    Returns the cut-off; a token issued from this second on is valid."""
    cutoff = datetime.now(timezone.utc).replace(microsecond=0)
    row = (await db.execute(select(Session_resets).where(Session_resets.user_id == str(user_id)))).scalar_one_or_none()
    if row is None:
        db.add(Session_resets(user_id=str(user_id), after=cutoff))
    else:
        row.after = cutoff
    await db.commit()
    from dependencies.auth import forget_account_status

    forget_account_status(str(user_id))
    return cutoff
