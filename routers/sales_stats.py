"""Sales today and this month, per representative (the dashboard's sales cards).

A sale = an order from manager approval onwards (the same statuses the dashboard and the target page
count), by the day it was created, on Iraq days (not UTC). Shown to the admin, accounting and managers;
a manager only sees their own reps. Every representative in scope is listed, also those who sold
nothing yet.
"""
import logging
from datetime import date, datetime, time, timedelta, timezone
from typing import Dict, Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.database import get_db
from dependencies.auth import get_current_user
from models.orders import Orders
from models.representatives import Representatives
from schemas.auth import UserResponse
from services.area_scope import get_customer_scope
from services.permission_check import resolve_user_role

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/sales-stats", tags=["sales_stats"])

_ALLOWED_ROLES = {"admin", "accounting", "manager"}
_COUNTED_STATUSES = ["manager_approved", "accounting_approved", "printed", "delivering", "delivered"]
_LOCAL_TZ = timezone(timedelta(hours=3))


def _as_utc(ts: datetime) -> datetime:
    return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)


@router.get("/summary")
async def sales_summary(
    db: AsyncSession = Depends(get_db),
    current_user: UserResponse = Depends(get_current_user),
):
    role = await resolve_user_role(db, current_user)
    if role is not None and role not in _ALLOWED_ROLES:
        raise HTTPException(status_code=403, detail="ليس لديك صلاحية لعرض المبيعات")

    scope = await get_customer_scope(db, current_user)

    today = datetime.now(_LOCAL_TZ).date()
    month_start = date(today.year, today.month, 1)
    month_start_utc = datetime.combine(month_start, time.min, tzinfo=_LOCAL_TZ).astimezone(timezone.utc)

    query = select(Orders.representative_id, Orders.total_amount, Orders.created_at).where(
        Orders.status.in_(_COUNTED_STATUSES),
        Orders.created_at >= month_start_utc,
    )
    if not scope.unrestricted:
        query = query.where(Orders.representative_id.in_(scope.rep_ids))
    orders = (await db.execute(query)).all()

    reps: Dict[int, dict] = {}

    def entry(rep_id: int, name: Optional[str] = None) -> dict:
        return reps.setdefault(
            rep_id,
            {"rep_id": rep_id, "name": name, "today": 0.0, "month": 0.0, "today_count": 0, "month_count": 0},
        )

    # every representative in scope is listed first
    reps_query = select(Representatives.id, Representatives.name).where(Representatives.role == "rep")
    for rid, name in (await db.execute(reps_query.order_by(Representatives.id))).all():
        if not scope.unrestricted and rid not in scope.rep_ids:
            continue
        entry(rid, (name or "").strip() or None)

    for rep_id, amount, created in orders:
        if created is None:
            continue
        e = entry(rep_id if rep_id else -1, None if rep_id else "طلبات بدون مندوب محدد")
        value = float(amount or 0)
        e["month"] += value
        e["month_count"] += 1
        if _as_utc(created).astimezone(_LOCAL_TZ).date() == today:
            e["today"] += value
            e["today_count"] += 1

    # a representative id that is not (any more) a representative in the list still keeps its sales
    unknown = [rid for rid, e in reps.items() if rid != -1 and not e["name"]]
    if unknown:
        for rid, name in (await db.execute(select(Representatives.id, Representatives.name).where(Representatives.id.in_(unknown)))).all():
            reps[rid]["name"] = (name or "").strip() or None
    for rid, e in reps.items():
        e["name"] = e["name"] or f"مندوب #{rid}"
        e["today"] = round(e["today"], 2)
        e["month"] = round(e["month"], 2)

    rows = sorted(reps.values(), key=lambda e: (e["rep_id"] == -1, -e["month"], -e["today"], e["name"]))
    return {
        "today": today.isoformat(),
        "month_start": month_start.isoformat(),
        "today_total": round(sum(e["today"] for e in rows), 2),
        "month_total": round(sum(e["month"] for e in rows), 2),
        "today_count": sum(e["today_count"] for e in rows),
        "month_count": sum(e["month_count"] for e in rows),
        "reps": rows,
    }
