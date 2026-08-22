import logging
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from fastapi import HTTPException

from models.representatives import Representatives
from models.app_users import App_users
from models.permissions import Permissions
from schemas.auth import UserResponse

logger = logging.getLogger(__name__)


async def resolve_user_role(db: AsyncSession, current_user: UserResponse) -> Optional[str]:
    """Resolve the app-level role (rep/manager/accounting/delivery/admin/...)
    used to key the permissions table — same role the Permissions page and
    usePermissions.ts key off of, NOT the JWT-level current_user.role.

    Returns None when the account has no representative or app_user row at
    all (e.g. a bootstrap/service account) — callers should treat None as
    unrestricted, matching this app's existing fail-open convention for
    accounts outside the modeled role system (see services/area_scope.py).
    """
    rep_result = await db.execute(
        select(Representatives).where(Representatives.user_id == current_user.id)
    )
    rep = rep_result.scalar_one_or_none()
    if rep and rep.role:
        return rep.role

    user_result = await db.execute(
        select(App_users).where(App_users.user_id == current_user.id)
    )
    app_user = user_result.scalar_one_or_none()
    if app_user and app_user.role:
        return app_user.role

    return None


async def has_permission(db: AsyncSession, role: str, page: str, action: str) -> bool:
    """Check the permissions table for (role, page).can_{action}. Missing
    row => False, matching the frontend's `perm?.can_x ?? false` default."""
    result = await db.execute(
        select(Permissions).where(Permissions.role == role, Permissions.page == page)
    )
    perm = result.scalar_one_or_none()
    if not perm:
        return False
    return bool(getattr(perm, f"can_{action}", False))


async def require_permission(
    db: AsyncSession, current_user: UserResponse, page: str, action: str
) -> Optional[str]:
    """Raise 403 unless the caller's role has {action} permission on {page}.

    Returns the resolved role (or None if the account has no rep/app_user
    row, i.e. is unrestricted) so callers can reuse it for further scoping
    (e.g. restricting a 'rep' to only their own records)."""
    role = await resolve_user_role(db, current_user)
    if role is None:
        return None
    if not await has_permission(db, role, page, action):
        logger.warning(f"Permission denied: role={role} page={page} action={action} user={current_user.id}")
        raise HTTPException(status_code=403, detail="ليس لديك صلاحية لتنفيذ هذا الإجراء")
    return role
