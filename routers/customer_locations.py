"""Customer locations: one map point per customer, set once and opened by the delivery person.

- GET    /api/v1/customer-locations                 the locations of the customers the caller may see
- PUT    /api/v1/customer-locations/{pharmacy_id}   set it. A customer with no location can be given
  one by anyone who can see the customer (the rep, or the delivery person standing at the door);
  changing an existing one needs the edit permission on customers
- DELETE /api/v1/customer-locations/{pharmacy_id}   remove it (edit permission on customers)
- POST   /api/v1/customer-locations/backfill-from-visits   fill the customers that have none from
  the GPS point of their latest pharmacy visit (edit permission on customers)
"""
import logging
import math
from datetime import datetime, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from core.database import get_db
from dependencies.auth import get_current_user
from models.activity_logs import Activity_logs
from models.app_users import App_users
from models.customer_locations import CustomerLocations
from models.pharmacies import Pharmacies
from schemas.auth import UserResponse
from services.access import require_view_any
from services.area_scope import get_customer_scope, get_scoped_pharmacy_names
from services.customer_names import normalize_name
from services.permission_check import require_permission

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/customer-locations", tags=["customer_locations"])

_tables_ready = False
SOURCES = {"gps", "manual", "visit"}


async def ensure_table(db: AsyncSession) -> None:
    global _tables_ready
    if _tables_ready:
        return
    conn = await db.connection()
    await conn.run_sync(lambda c: CustomerLocations.__table__.create(c, checkfirst=True))
    await db.commit()
    _tables_ready = True


class LocationBody(BaseModel):
    latitude: float
    longitude: float
    accuracy_m: Optional[float] = None
    source: str = "gps"


def _valid_point(lat: float, lng: float) -> bool:
    return (
        math.isfinite(lat)
        and math.isfinite(lng)
        and -90 <= lat <= 90
        and -180 <= lng <= 180
        and not (abs(lat) < 0.0001 and abs(lng) < 0.0001)  # (0, 0): a phone that has no fix
    )


def _row(loc: CustomerLocations) -> dict:
    return {
        "pharmacy_id": loc.pharmacy_id,
        "latitude": loc.latitude,
        "longitude": loc.longitude,
        "accuracy_m": loc.accuracy_m,
        "source": loc.source,
        "set_by_name": loc.set_by_name,
        "set_at": loc.set_at.isoformat() if loc.set_at else None,
    }


async def _account_name(db: AsyncSession, user: UserResponse) -> str:
    name = (await db.execute(select(App_users.name).where(App_users.user_id == str(user.id)).order_by(App_users.id))).scalars().first()
    return (name or "").strip() or user.name or str(user.id)


async def _visible_pharmacy(db: AsyncSession, user: UserResponse, pharmacy_id: int) -> Pharmacies:
    pharmacy = (await db.execute(select(Pharmacies).where(Pharmacies.id == pharmacy_id))).scalar_one_or_none()
    if pharmacy is None:
        raise HTTPException(status_code=404, detail="الزبون غير موجود")
    scope = await get_customer_scope(db, user)
    visible = await get_scoped_pharmacy_names(db, scope)
    if visible is not None and normalize_name(pharmacy.name) not in visible:
        raise HTTPException(status_code=403, detail="هذا الزبون خارج نطاق صلاحياتك")
    return pharmacy


@router.get("")
async def list_locations(
    db: AsyncSession = Depends(get_db),
    current_user: UserResponse = Depends(get_current_user),
):
    await require_view_any(db, current_user, ["customers", "orders"])
    await ensure_table(db)
    scope = await get_customer_scope(db, current_user)
    visible = await get_scoped_pharmacy_names(db, scope)
    rows = (await db.execute(select(CustomerLocations))).scalars().all()
    if visible is not None:
        allowed = {
            pid
            for pid, name in (await db.execute(select(Pharmacies.id, Pharmacies.name))).all()
            if normalize_name(name) in visible
        }
        rows = [r for r in rows if r.pharmacy_id in allowed]
    return [_row(r) for r in rows]


@router.put("/{pharmacy_id}")
async def set_location(
    pharmacy_id: int,
    data: LocationBody,
    db: AsyncSession = Depends(get_db),
    current_user: UserResponse = Depends(get_current_user),
):
    await require_view_any(db, current_user, ["customers", "orders"])
    if not _valid_point(data.latitude, data.longitude):
        raise HTTPException(status_code=400, detail="الإحداثيات غير صالحة")
    source = data.source if data.source in SOURCES else "manual"
    await ensure_table(db)
    await _visible_pharmacy(db, current_user, pharmacy_id)
    existing = (await db.execute(select(CustomerLocations).where(CustomerLocations.pharmacy_id == pharmacy_id))).scalar_one_or_none()
    if existing is not None:
        # a location that is already there is only changed by someone who may edit customers
        await require_permission(db, current_user, "customers", "edit")
    name = await _account_name(db, current_user)
    try:
        if existing is None:
            existing = CustomerLocations(pharmacy_id=pharmacy_id)
            db.add(existing)
        existing.latitude = data.latitude
        existing.longitude = data.longitude
        existing.accuracy_m = data.accuracy_m
        existing.source = source
        existing.set_by = str(current_user.id)
        existing.set_by_name = name
        existing.set_at = datetime.now(timezone.utc)
        await db.commit()
        await db.refresh(existing)
    except Exception:
        await db.rollback()
        raise
    return _row(existing)


@router.delete("/{pharmacy_id}")
async def delete_location(
    pharmacy_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: UserResponse = Depends(get_current_user),
):
    await require_permission(db, current_user, "customers", "edit")
    await ensure_table(db)
    await _visible_pharmacy(db, current_user, pharmacy_id)
    existing = (await db.execute(select(CustomerLocations).where(CustomerLocations.pharmacy_id == pharmacy_id))).scalar_one_or_none()
    if existing is None:
        raise HTTPException(status_code=404, detail="لا يوجد موقع لهذا الزبون")
    await db.delete(existing)
    await db.commit()
    return {"pharmacy_id": pharmacy_id, "deleted": True}


@router.post("/backfill-from-visits")
async def backfill_from_visits(
    db: AsyncSession = Depends(get_db),
    current_user: UserResponse = Depends(get_current_user),
):
    """Customers without a location get the GPS point of their latest pharmacy visit that has one
    (marked as coming from a visit, so it can be told apart from a point set at the door)."""
    await require_permission(db, current_user, "customers", "edit")
    await ensure_table(db)
    have = {pid for (pid,) in (await db.execute(select(CustomerLocations.pharmacy_id))).all()}
    latest_id = (
        select(func.max(Activity_logs.id))
        .where(
            Activity_logs.type == "pharmacy_visit",
            Activity_logs.pharmacy_id.isnot(None),
            Activity_logs.latitude.isnot(None),
            Activity_logs.longitude.isnot(None),
        )
        .group_by(Activity_logs.pharmacy_id)
    )
    visits = (await db.execute(select(Activity_logs).where(Activity_logs.id.in_(latest_id)))).scalars().all()
    existing_pharmacies = {pid for (pid,) in (await db.execute(select(Pharmacies.id))).all()}
    name = await _account_name(db, current_user)
    added: List[int] = []
    skipped_invalid = 0
    try:
        for v in visits:
            if v.pharmacy_id in have or v.pharmacy_id not in existing_pharmacies:
                continue
            if not _valid_point(v.latitude, v.longitude):
                skipped_invalid += 1
                continue
            db.add(
                CustomerLocations(
                    pharmacy_id=v.pharmacy_id, latitude=v.latitude, longitude=v.longitude, source="visit", set_by=str(current_user.id), set_by_name=name
                )
            )
            added.append(v.pharmacy_id)
        await db.commit()
    except Exception:
        await db.rollback()
        raise
    return {"added": len(added), "skipped_invalid": skipped_invalid, "customers_with_location": len(have) + len(added)}
