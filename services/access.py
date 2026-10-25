"""Role-based access helpers shared by the list/map endpoints.

Two independent checks:

* ``require_view_any`` — the caller's role must hold ``can_view`` on at least
  one of the given permission pages (the same matrix the Permissions page
  edits and the frontend's ``usePermissions`` reads).
* ``visible_user_ids`` — which users' records the caller may see. A rep sees
  their own, a manager their assigned reps' (plus their own); every other role
  is unrestricted (and still subject to the view check above), the same
  convention as ``services.area_scope.get_customer_scope``.

Accounts with no representative / app_user row at all (bootstrap or service
accounts) resolve to ``None`` and stay unrestricted, matching the app's
existing fail-open convention for accounts outside the modeled role system.
"""
import logging
from typing import Iterable, Optional

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models.representatives import Representatives
from schemas.auth import UserResponse
from services.area_scope import get_customer_scope
from services.permission_check import has_permission, resolve_user_role

logger = logging.getLogger(__name__)

DENIED = "ليس لديك صلاحية لعرض هذه البيانات"


async def require_view_any(db: AsyncSession, current_user: UserResponse, pages: Iterable[str]) -> Optional[str]:
    """403 unless the caller's role can view at least one of ``pages``.
    Returns the resolved role (None for unrestricted accounts)."""
    role = await resolve_user_role(db, current_user)
    if role is None or role == "admin":
        return role
    for page in pages:
        if await has_permission(db, role, page, "view"):
            return role
    logger.warning("View denied: role=%s pages=%s user=%s", role, list(pages), current_user.id)
    raise HTTPException(status_code=403, detail=DENIED)


async def require_admin(db: AsyncSession, current_user: UserResponse, strict: bool = False) -> None:
    """403 unless the caller is an admin. By default an unrestricted service
    account (no rep/app_user row) also passes; ``strict`` requires an actual
    admin role (use it for account-takeover-grade actions)."""
    role = await resolve_user_role(db, current_user)
    if role == "admin" or (role is None and not strict):
        return
    raise HTTPException(status_code=403, detail="هذا الإجراء مقصور على المسؤول")


async def visible_user_ids(db: AsyncSession, current_user: UserResponse) -> Optional[set[str]]:
    """User ids whose records the caller may see, or ``None`` for everything."""
    scope = await get_customer_scope(db, current_user)
    if scope.unrestricted:
        return None
    rows = await db.execute(
        select(Representatives.user_id).where(Representatives.id.in_(scope.rep_ids))
    )
    return {row[0] for row in rows.all() if row[0]} | {str(current_user.id)}
