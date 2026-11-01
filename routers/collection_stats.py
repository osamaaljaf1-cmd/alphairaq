"""How much was collected today and this month, per user (the dashboard's collection cards).

Collected = the money received on the payments recorded in the app (net received, after any
discount and returns), cancelled payments left out. The day and the month are Iraq days, not UTC
ones. Shown to the admin, accounting and managers; a manager only sees payments of the customers
in their scope (their reps' areas / customers) and only their own reps in the list.
"""
import logging
from datetime import date, datetime, time, timedelta, timezone
from typing import Dict, Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from core.database import get_db
from dependencies.auth import get_current_user
from models.app_users import App_users
from models.payments import Payments
from models.representatives import Representatives
from schemas.auth import UserResponse
from services.area_scope import get_customer_scope, get_scoped_pharmacy_names
from services.customer_names import normalize_name
from services.permission_check import resolve_user_role

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/collection-stats", tags=["collection_stats"])

_ALLOWED_ROLES = {"admin", "accounting", "manager"}
_LOCAL_TZ = timezone(timedelta(hours=3))


def _as_utc(ts: datetime) -> datetime:
    return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)


@router.get("/summary")
async def collection_summary(
    db: AsyncSession = Depends(get_db),
    current_user: UserResponse = Depends(get_current_user),
):
    role = await resolve_user_role(db, current_user)
    if role is not None and role not in _ALLOWED_ROLES:
        raise HTTPException(status_code=403, detail="ليس لديك صلاحية لعرض التحصيل")

    scope = await get_customer_scope(db, current_user)
    visible_names = await get_scoped_pharmacy_names(db, scope)  # None = every customer

    today = datetime.now(_LOCAL_TZ).date()
    month_start = date(today.year, today.month, 1)
    month_start_utc = datetime.combine(month_start, time.min, tzinfo=_LOCAL_TZ).astimezone(timezone.utc)

    payments = (
        await db.execute(
            select(Payments.user_id, Payments.customer_name, Payments.net_received, Payments.created_at, Payments.rep_name)
            .where(or_(Payments.status.is_(None), Payments.status != "canceled"))
            .where(Payments.created_at >= month_start_utc)
        )
    ).all()

    users: Dict[str, dict] = {}

    def entry(uid: str, fallback_name: Optional[str] = None) -> dict:
        return users.setdefault(
            uid, {"user_id": uid, "name": fallback_name, "today": 0.0, "month": 0.0, "today_count": 0, "month_count": 0}
        )

    for uid, customer, amount, created, rep_name in payments:
        if created is None or not uid:
            continue
        if visible_names is not None and normalize_name(customer) not in visible_names:
            continue
        e = entry(uid, rep_name)
        value = float(amount or 0)
        e["month"] += value
        e["month_count"] += 1
        if _as_utc(created).astimezone(_LOCAL_TZ).date() == today:
            e["today"] += value
            e["today_count"] += 1

    # every representative in scope is listed, also the ones who collected nothing yet
    reps_query = select(Representatives.id, Representatives.user_id, Representatives.name).where(Representatives.role == "rep")
    for rid, uid, name in (await db.execute(reps_query.order_by(Representatives.id))).all():
        if not uid:
            continue
        if not scope.unrestricted and rid not in scope.rep_ids:
            continue
        entry(uid, name)

    # the name shown is the login account's, then the representative profile's
    if users:
        account_names = {
            uid: (name or "").strip()
            for uid, name in (await db.execute(select(App_users.user_id, App_users.name).where(App_users.user_id.in_(list(users))))).all()
            if name and name.strip()
        }
        profile_names = {
            uid: (name or "").strip()
            for uid, name in (
                await db.execute(select(Representatives.user_id, Representatives.name).where(Representatives.user_id.in_(list(users))).order_by(Representatives.id.desc()))
            ).all()
            if name and name.strip()
        }
        for uid, e in users.items():
            e["name"] = account_names.get(uid) or profile_names.get(uid) or e["name"] or uid
            e["today"] = round(e["today"], 2)
            e["month"] = round(e["month"], 2)

    rows = sorted(users.values(), key=lambda e: (-e["month"], -e["today"], e["name"]))
    return {
        "today": today.isoformat(),
        "month_start": month_start.isoformat(),
        "today_total": round(sum(e["today"] for e in rows), 2),
        "month_total": round(sum(e["month"] for e in rows), 2),
        "today_count": sum(e["today_count"] for e in rows),
        "month_count": sum(e["month_count"] for e in rows),
        "users": rows,
    }
