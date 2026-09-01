import logging
from typing import List

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from core.database import get_db
from dependencies.auth import get_current_user
from schemas.auth import UserResponse
from services.permission_check import require_permission

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/custom-roles", tags=["custom-roles"])

# Every page a role needs a (blank) permission row for when it's created.
# Kept in sync with routers/migrate.py's _SEED_PAGES.
_ALL_PAGES = (
    "dashboard", "orders", "returns", "agreements", "target", "customers",
    "items", "map", "chat", "admin_users", "permissions", "areas",
    "rep_operations", "doctor_visits", "pharmacy_visits", "debts",
    "rep_activity_map",
)

_FIXED_ROLES = {"admin", "manager", "rep", "accounting", "delivery", "scientific", "sales"}


class CustomRoleItem(BaseModel):
    id: int
    name: str


class CreateCustomRoleRequest(BaseModel):
    name: str


@router.get("", response_model=List[CustomRoleItem])
async def list_custom_roles(
    db: AsyncSession = Depends(get_db),
    current_user: UserResponse = Depends(get_current_user),
):
    """List custom (non-fixed) roles, so the frontend can offer them alongside
    the built-in admin/manager/rep/... roles."""
    result = await db.execute(text("SELECT id, name FROM custom_roles ORDER BY name"))
    return [CustomRoleItem(id=row[0], name=row[1]) for row in result.fetchall()]


@router.post("", response_model=CustomRoleItem)
async def create_custom_role(
    data: CreateCustomRoleRequest,
    db: AsyncSession = Depends(get_db),
    current_user: UserResponse = Depends(get_current_user),
):
    """Create a new internal role and immediately seed blank (all-False)
    permission rows for it across every page, so it's ready to configure
    from the Permissions page right away. Requires can_add on the
    permissions page — role management is a permissions-page action."""
    await require_permission(db, current_user, "permissions", "add")

    name = data.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="اسم الدور مطلوب")
    if len(name) > 50:
        raise HTTPException(status_code=400, detail="اسم الدور طويل جداً")
    if name in _FIXED_ROLES:
        raise HTTPException(status_code=400, detail="هذا الدور موجود مسبقاً ضمن الأدوار الأساسية")

    existing = await db.execute(text("SELECT id FROM custom_roles WHERE name = :name"), {"name": name})
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="هذا الدور موجود مسبقاً")

    result = await db.execute(
        text("INSERT INTO custom_roles (name) VALUES (:name) RETURNING id"),
        {"name": name},
    )
    new_id = result.scalar_one()

    # No unique constraint exists on permissions(role, page), but this role is
    # brand new so there's nothing to conflict with — a plain insert is safe.
    for page in _ALL_PAGES:
        await db.execute(
            text(
                "INSERT INTO permissions (role, page, can_view, can_add, can_edit, can_delete, can_import, can_export) "
                "VALUES (:role, :page, false, false, false, false, false, false)"
            ),
            {"role": name, "page": page},
        )
    await db.commit()

    logger.info(f"Custom role created: {name}")
    return CustomRoleItem(id=new_id, name=name)
