import hashlib
import logging
import time
from datetime import datetime, timezone
from typing import Dict, Optional, Tuple

from core.auth import AccessTokenError, decode_access_token
from core.database import get_db
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from models.app_users import App_users
from schemas.auth import UserResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

bearer_scheme = HTTPBearer(auto_error=False)


async def get_bearer_token(
    request: Request, credentials: Optional[HTTPAuthorizationCredentials] = Depends(bearer_scheme)
) -> str:
    """Extract bearer token from Authorization header."""
    if credentials and credentials.scheme.lower() == "bearer":
        return credentials.credentials

    logger.debug("Authentication required for request %s %s", request.method, request.url.path)
    raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication credentials were not provided")


# A token lives 7 days, so a blocked / deactivated account kept working until it ran out, and so did a
# session opened with a password that has since been changed. The account's status and the moment its
# sessions were last ended are looked up (cached for a minute: one query per user per minute at most).
_BLOCKED_STATUSES = {"blocked", "inactive"}
_STATUS_TTL_SECONDS = 60.0
_status_cache: Dict[str, Tuple[float, Optional[str], Optional[float]]] = {}


async def _account_state(db: AsyncSession, user_id: str) -> Tuple[Optional[str], Optional[float]]:
    """The app account's status (None when there is no such row: service / bootstrap accounts) and the
    time (epoch seconds) before which its sessions no longer count, if it was ever set."""
    now = time.monotonic()
    hit = _status_cache.get(user_id)
    if hit and now - hit[0] < _STATUS_TTL_SECONDS:
        return hit[1], hit[2]
    status_value: Optional[str] = None
    reset_after: Optional[float] = None
    try:
        row = (await db.execute(select(App_users.status).where(App_users.user_id == user_id).limit(1))).first()
        status_value = row[0] if row else None
        from models.auth_security import Session_resets

        reset = (await db.execute(select(Session_resets.after).where(Session_resets.user_id == user_id).limit(1))).first()
        if reset and reset[0] is not None:
            after = reset[0] if reset[0].tzinfo else reset[0].replace(tzinfo=timezone.utc)
            reset_after = after.timestamp()
    except Exception:  # never lock everybody out because the lookup failed
        logger.warning("Account status lookup failed", exc_info=True)
        return status_value, reset_after
    if len(_status_cache) > 5000:
        _status_cache.clear()
    _status_cache[user_id] = (now, status_value, reset_after)
    return status_value, reset_after


def forget_account_status(user_id: Optional[str] = None) -> None:
    """Drop the cached status (after the admin blocks / unblocks an account) so it applies at once."""
    if user_id is None:
        _status_cache.clear()
    else:
        _status_cache.pop(str(user_id), None)


async def get_current_user(token: str = Depends(get_bearer_token), db: AsyncSession = Depends(get_db)) -> UserResponse:
    """Dependency to get current authenticated user via JWT token."""
    try:
        payload = decode_access_token(token)
    except AccessTokenError as exc:
        # Log error type only, not the full exception which may contain sensitive token data
        logger.warning("Token validation failed: %s", type(exc).__name__)
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=exc.message)

    user_id = payload.get("sub")
    if not user_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid authentication token")

    account_status, reset_after = await _account_state(db, str(user_id))
    if account_status in _BLOCKED_STATUSES:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="هذا الحساب موقوف. تواصل مع المسؤول")
    if reset_after is not None:
        issued = payload.get("iat")
        if not isinstance(issued, (int, float)) or issued < reset_after:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="انتهت الجلسة. سجّل الدخول مرة أخرى")

    last_login_raw = payload.get("last_login")
    last_login = None
    if isinstance(last_login_raw, str):
        try:
            last_login = datetime.fromisoformat(last_login_raw)
        except ValueError:
            # Log user hash instead of actual user ID to avoid exposing sensitive information
            user_hash = hashlib.sha256(str(user_id).encode()).hexdigest()[:8] if user_id else "unknown"
            logger.debug("Failed to parse last_login for user hash: %s", user_hash)

    return UserResponse(
        id=user_id,
        email=payload.get("email", ""),
        name=payload.get("name"),
        role=payload.get("role", "user"),
        last_login=last_login,
    )


async def get_admin_user(current_user: UserResponse = Depends(get_current_user)) -> UserResponse:
    """Dependency to ensure current user has admin role."""
    if current_user.role != "admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin access required")
    return current_user
