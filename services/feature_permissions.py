"""Action permissions: things a role may *do* that are not a page of their own.

Each feature is a row in the permissions matrix (page = the feature id) and the
single flag that counts is ``can_edit`` ("allowed to perform it"). Until a role
has a row for a feature, the check falls back to what that role could already do
before the feature existed, so deploying this never takes an action away from
anyone; the seed (migrate.py) writes the rows with exactly those values and
from then on the matrix decides.

``roles``   : the roles that were hard-coded for the action.
``inherit`` : the page permission that used to stand in for it, e.g. cancelling a
              payment used to ride on ``debts.delete``.
"""
from typing import Dict, Optional

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models.permissions import Permissions
from schemas.auth import UserResponse
from services.permission_check import has_permission, resolve_user_role

DENIED = "ليس لديك صلاحية لتنفيذ هذا الإجراء"

FEATURES: Dict[str, dict] = {
    # order workflow, one permission per stage
    "orders_manager_approval": {"roles": ("admin", "manager")},
    "orders_accounting_approval": {"roles": ("admin", "accounting")},
    "orders_printing": {"roles": ("admin", "accounting", "printing")},
    "orders_delivery": {"roles": ("admin", "delivery")},
    # debts and collections
    "debts_payments": {"inherit": ("debts", "view")},
    "debts_payment_cancel": {"inherit": ("debts", "delete")},
    "debts_handover": {"inherit": ("debts", "delete")},
    # customers
    "customers_merge": {"inherit": ("customers", "delete")},
}


async def has_feature(db: AsyncSession, role: str, feature: str) -> bool:
    row = (
        await db.execute(select(Permissions).where(Permissions.role == role, Permissions.page == feature))
    ).scalar_one_or_none()
    if row is not None:
        return bool(row.can_edit)
    rule = FEATURES[feature]
    if "roles" in rule:
        return role in rule["roles"]
    page, action = rule["inherit"]
    return await has_permission(db, role, page, action)


async def require_feature(db: AsyncSession, current_user: UserResponse, feature: str) -> Optional[str]:
    """403 unless the caller's role may perform ``feature``. Accounts outside the
    modeled role system (no representative / app user row) stay unrestricted, like
    require_permission."""
    role = await resolve_user_role(db, current_user)
    if role is None:
        return None
    if not await has_feature(db, role, feature):
        raise HTTPException(status_code=403, detail=DENIED)
    return role


def default_for_seed(role: str, feature: str, flags_by_pair: dict) -> bool:
    """The value the seed writes for a role that has no row yet: what the role
    could already do, per the fallback rule."""
    rule = FEATURES[feature]
    if "roles" in rule:
        return role in rule["roles"]
    page, action = rule["inherit"]
    flags = flags_by_pair.get((role, page)) or {}
    return bool(flags.get(f"can_{action}", False))
