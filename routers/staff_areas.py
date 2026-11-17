"""ربط الموظفين بالفروع: which branch / governorate / areas each staff member works in.

- GET /api/v1/staff-areas        every staff member (not the admin) with the areas they are linked to
- PUT /api/v1/staff-areas        (admin) {user_id, area_ids}: replace one member's links
- GET /api/v1/staff-areas/me     what the caller is restricted to (for the app's own filters)

A rep's links are their representative_areas (the same as the «مناطق المندوبين» page), and their
branch (set when the account is made, or here) is a staff_areas row that only says where they work;
everyone else's links are in staff_areas and decide what they see. Linking a branch covers everything under it. A member with no link is not
restricted by area (a rep then sees only their own customers, a manager their reps', the others all).
"""
import logging
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from core.database import get_db
from dependencies.auth import get_current_user
from models.app_users import App_users
from models.areas import Areas
from models.representative_areas import Representative_areas
from models.representatives import Representatives
from models.staff_areas import Staff_areas
from schemas.auth import UserResponse
from services.access import require_admin, require_view_any
from services.area_scope import get_customer_scope

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/staff-areas", tags=["staff_areas"])


class LinkBody(BaseModel):
    user_id: str
    area_ids: List[int] = []
    # a rep only: their branch (kept apart from their areas, which decide what they see)
    set_rep_branch: bool = False
    rep_branch_id: Optional[int] = None


async def _members(db: AsyncSession) -> dict:
    """Every account by user id: name, role (the representatives row wins, like resolve_user_role)."""
    out: dict = {}
    for u in (await db.execute(select(App_users))).scalars().all():
        out[str(u.user_id)] = {"user_id": str(u.user_id), "name": u.name, "email": u.email, "role": u.role, "status": u.status, "rep_id": None}
    for r in (await db.execute(select(Representatives))).scalars().all():
        m = out.setdefault(str(r.user_id), {"user_id": str(r.user_id), "name": r.name, "email": None, "status": "active"})
        m["role"] = r.role or m.get("role")
        m["rep_id"] = r.id
        m["name"] = m.get("name") or r.name
    return out


@router.get("")
async def list_links(db: AsyncSession = Depends(get_db), current_user: UserResponse = Depends(get_current_user)):
    await require_view_any(db, current_user, ["permissions"])
    members = await _members(db)
    rep_links: dict = {}
    for rep_id, area_id in (await db.execute(select(Representative_areas.representative_id, Representative_areas.area_id))).all():
        rep_links.setdefault(rep_id, []).append(area_id)
    staff_links: dict = {}
    for user_id, area_id in (await db.execute(select(Staff_areas.user_id, Staff_areas.area_id))).all():
        staff_links.setdefault(str(user_id), []).append(area_id)
    staff = []
    for m in members.values():
        if m.get("role") == "admin":
            continue
        is_rep = m.get("role") == "rep" and m.get("rep_id") is not None
        ids = rep_links.get(m["rep_id"], []) if is_rep else staff_links.get(m["user_id"], [])
        staff.append({
            **m,
            "area_ids": sorted(set(ids)),
            "source": "rep" if is_rep else "staff",
            # the person's branch row(s): for a rep their branch, for anyone else the same as area_ids
            "branch_ids": sorted(set(staff_links.get(m["user_id"], []))),
        })
    staff.sort(key=lambda m: (m.get("role") or "", m.get("name") or ""))
    return {"staff": staff}


@router.put("")
async def save_links(body: LinkBody, db: AsyncSession = Depends(get_db), current_user: UserResponse = Depends(get_current_user)):
    await require_admin(db, current_user, strict=True)
    members = await _members(db)
    m = members.get(body.user_id)
    if not m:
        raise HTTPException(status_code=404, detail="الموظف غير موجود")
    if m.get("role") == "admin":
        raise HTTPException(status_code=400, detail="المسؤول يرى كل الفروع دائماً")
    wanted = sorted(set(body.area_ids))
    check = wanted + ([body.rep_branch_id] if body.set_rep_branch and body.rep_branch_id is not None else [])
    if check:
        found = {row[0] for row in (await db.execute(select(Areas.id).where(Areas.id.in_(check)))).all()}
        missing = [a for a in check if a not in found]
        if missing:
            raise HTTPException(status_code=400, detail="منطقة غير موجودة")
    if m.get("role") == "rep" and m.get("rep_id") is not None:
        await db.execute(delete(Representative_areas).where(Representative_areas.representative_id == m["rep_id"]))
        for a in wanted:
            db.add(Representative_areas(representative_id=m["rep_id"], area_id=a))
        if body.set_rep_branch:
            await db.execute(delete(Staff_areas).where(Staff_areas.user_id == body.user_id))
            if body.rep_branch_id is not None:
                db.add(Staff_areas(user_id=body.user_id, area_id=body.rep_branch_id))
    else:
        await db.execute(delete(Staff_areas).where(Staff_areas.user_id == body.user_id))
        for a in wanted:
            db.add(Staff_areas(user_id=body.user_id, area_id=a))
    await db.commit()
    logger.info("Staff areas: %s -> %s by %s", body.user_id, wanted, current_user.id)
    return {"user_id": body.user_id, "area_ids": wanted}


@router.get("/me")
async def my_scope(db: AsyncSession = Depends(get_db), current_user: UserResponse = Depends(get_current_user)):
    scope = await get_customer_scope(db, current_user)
    if scope.unrestricted:
        return {"restricted": False, "by_branch": False, "area_ids": [], "rep_ids": []}
    return {"restricted": True, "by_branch": scope.by_branch, "area_ids": sorted(scope.area_ids), "rep_ids": list(scope.rep_ids)}
