"""Upcoming collection dates API - fetches debt_follow_up visits with collection_date.

An appointment is a pharmacy visit of type debt_follow_up that carries a collection date.
The rules that decide which ones are still open (see _resolve_appointments):

- only the LATEST appointment of a customer counts: when the rep visits on the collection
  day, the customer has not paid and the rep gives a new date, the new date replaces the old;
- an appointment ends when the customer pays after it was taken, or when the customer owes
  nothing any more (the debt was settled in the accounting program, or never existed);
- each open appointment carries its number (how many appointments the customer went through
  since the last payment, so 2 = did not pay at the first date), when it was taken, and, once
  its day has come, whether the rep visited the customer that day.
"""
import logging
from datetime import date, datetime, timedelta, timezone
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import select, and_, or_, false, func
from sqlalchemy.ext.asyncio import AsyncSession

from core.database import get_db
from dependencies.auth import get_current_user
from models.activity_logs import Activity_logs
from models.app_users import App_users
from models.debts import Debts
from models.payments import Payments
from models.pharmacies import Pharmacies
from models.representatives import Representatives
from schemas.auth import UserResponse
from services.area_scope import get_customer_scope, get_scoped_pharmacy_names
from services.customer_names import name_sql, normalize_name

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/collections", tags=["collections"])

# "the day" of a visit or an appointment is the local (Iraq) day, not the UTC one
_LOCAL_TZ = timezone(timedelta(hours=3))


class CollectionItem(BaseModel):
    id: int
    user_id: str
    rep_name: Optional[str] = None
    pharmacy_id: Optional[int] = None
    pharmacy_name: Optional[str] = None
    collection_date: str
    details: Optional[str] = None
    timestamp: Optional[str] = None  # when the rep took this appointment
    status: str  # overdue, today, upcoming
    appointment_number: int = 1  # 2 = the customer did not pay at the previous date
    previous_dates: List[str] = []  # the dates this appointment replaced
    # days between the previous appointment's date and the day this one was taken:
    # 0 = given during the visit on the previous due day, > 0 = taken that many days after it
    # passed, < 0 = renewed before it came. None for a first appointment.
    renewal_gap_days: Optional[int] = None
    visited_on_due_date: Optional[bool] = None  # None until the day has come
    visit_at: Optional[str] = None  # when the rep visited the customer on that day
    missed_visit: bool = False  # the day has passed and the rep did not go to the customer

    class Config:
        from_attributes = True


class CollectionsResponse(BaseModel):
    items: List[CollectionItem]
    overdue_count: int
    today_count: int
    upcoming_count: int
    total: int


def _as_utc(ts: Optional[datetime]) -> Optional[datetime]:
    if ts is None:
        return None
    return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)


def _local_day(ts: Optional[datetime]) -> Optional[date]:
    ts = _as_utc(ts)
    return ts.astimezone(_LOCAL_TZ).date() if ts else None


def _customer_key(log) -> str:
    return f"id:{log.pharmacy_id}" if log.pharmacy_id else f"n:{normalize_name(log.pharmacy_name)}"


async def _resolve_appointments(db: AsyncSession, logs: List[Activity_logs]) -> List[dict]:
    """The open appointments among `logs` (every appointment of the customers in scope, any
    date): [{"log", "number", "previous_dates"}], one per customer, the latest one."""
    if not logs:
        return []

    # who owes something now, and when each customer last paid (non-cancelled payments)
    owing = {
        normalize_name(name)
        for (name,) in (
            await db.execute(select(Debts.customer_name).where(Debts.remaining_amount > 0.005).distinct())
        ).all()
        if name
    }
    last_paid: Dict[str, datetime] = {}
    pay_rows = await db.execute(
        select(Payments.customer_name, func.max(Payments.created_at))
        .where(or_(Payments.status.is_(None), Payments.status != "canceled"))
        .group_by(Payments.customer_name)
    )
    for name, created in pay_rows.all():
        if not name or created is None:
            continue
        key = normalize_name(name)
        if key not in last_paid or _as_utc(created) > _as_utc(last_paid[key]):
            last_paid[key] = created

    # Payments and debts are often saved under the customer record that carries the area
    # suffix ("صيدلية X زيونه") while the appointment was logged on the same pharmacy without
    # it ("صيدلية X"), so also try name + that pharmacy's own area.
    ids = {l.pharmacy_id for l in logs if l.pharmacy_id}
    area_by_id: Dict[int, str] = {}
    if ids:
        area_rows = await db.execute(select(Pharmacies.id, Pharmacies.area).where(Pharmacies.id.in_(ids)))
        area_by_id = {pid: (area or "").strip().lower() for pid, area in area_rows.all()}

    def name_keys(log) -> List[str]:
        key = normalize_name(log.pharmacy_name)
        keys = [key]
        area = area_by_id.get(log.pharmacy_id)
        if area:
            if not key.endswith(area):
                keys.append(f"{key} {area}")
            else:
                bare = key[: -len(area)].strip()
                if bare:
                    keys.append(bare)
        return keys

    groups: Dict[str, List[Activity_logs]] = {}
    for log in logs:
        groups.setdefault(_customer_key(log), []).append(log)

    resolved: List[dict] = []
    for group in groups.values():
        group.sort(key=lambda l: (l.timestamp is not None, _as_utc(l.timestamp), l.id))
        latest = group[-1]
        keys = name_keys(latest)
        if not any(k in owing for k in keys):
            continue  # nothing left to collect from this customer
        paid_times = [_as_utc(last_paid[k]) for k in keys if k in last_paid]
        paid_at = max(paid_times) if paid_times else None
        if paid_at is not None and latest.timestamp is not None and paid_at > _as_utc(latest.timestamp):
            continue  # paid after the latest appointment was taken
        # the chain: the appointments taken since the customer last paid
        chain = [l for l in group if paid_at is None or l.timestamp is None or _as_utc(l.timestamp) > paid_at]
        gap = None
        if len(chain) > 1 and chain[-2].collection_date and latest.timestamp is not None:
            gap = (_local_day(latest.timestamp) - chain[-2].collection_date).days
        resolved.append(
            {
                "log": latest,
                "keys": keys,
                "gap": gap,
                "number": len(chain),
                "previous_dates": [l.collection_date.isoformat() for l in chain[:-1] if l.collection_date],
            }
        )
    return resolved


async def _visits_on_due_dates(db: AsyncSession, resolved: List[dict], today: date) -> Dict[int, Optional[datetime]]:
    """For each open appointment whose day has come: the time of the latest visit the customer
    got that day (any pharmacy visit logged for it, apart from the appointment's own record),
    or None when nobody visited. Appointments of future days are left out."""
    due = [r for r in resolved if r["log"].collection_date and r["log"].collection_date <= today]
    if not due:
        return {}
    # A customer can sit in the app twice ("صيدلية X" and "صيدلية X زيونه"), so a visit counts
    # when it was logged on the same pharmacy record OR under any of the customer's names.
    ids = {r["log"].pharmacy_id for r in due if r["log"].pharmacy_id}
    names = {k for r in due for k in r["keys"] if k}
    first = min(r["log"].collection_date for r in due) - timedelta(days=1)
    conds = []
    if ids:
        conds.append(Activity_logs.pharmacy_id.in_(ids))
    if names:
        conds.append(name_sql(Activity_logs.pharmacy_name).in_(names))
    rows = (
        await db.execute(
            select(Activity_logs.id, Activity_logs.pharmacy_id, Activity_logs.pharmacy_name, Activity_logs.timestamp).where(
                Activity_logs.type == "pharmacy_visit",
                Activity_logs.timestamp.isnot(None),
                Activity_logs.timestamp >= datetime.combine(first, datetime.min.time(), tzinfo=timezone.utc),
                or_(*conds),
            )
        )
    ).all()
    by_id: Dict[int, list] = {}
    by_name: Dict[str, list] = {}
    for vid, pid, pname, ts in rows:
        if pid:
            by_id.setdefault(pid, []).append((vid, ts))
        if pname:
            by_name.setdefault(normalize_name(pname), []).append((vid, ts))

    out: Dict[int, Optional[datetime]] = {}
    for r in due:
        log = r["log"]
        candidates = list(by_id.get(log.pharmacy_id, [])) if log.pharmacy_id else []
        for k in r["keys"]:
            candidates += by_name.get(k, [])
        same_day = {
            vid: ts for vid, ts in candidates if vid != log.id and _local_day(ts) == log.collection_date
        }
        out[log.id] = max(same_day.values(), key=_as_utc) if same_day else None
    return out


async def _rep_names(db: AsyncSession, user_ids: set) -> Dict[str, str]:
    """The name of the ACCOUNT that took each appointment (its login account), falling back to
    the representative profile only when the account has no name."""
    if not user_ids:
        return {}
    names: Dict[str, str] = {}
    for uid, name in (
        await db.execute(select(App_users.user_id, App_users.name).where(App_users.user_id.in_(user_ids)).order_by(App_users.id))
    ).all():
        if name and name.strip():
            names.setdefault(uid, name.strip())
    missing = user_ids - set(names)
    if missing:
        for uid, name in (
            await db.execute(
                select(Representatives.user_id, Representatives.name).where(Representatives.user_id.in_(missing)).order_by(Representatives.id)
            )
        ).all():
            if name and name.strip():
                names.setdefault(uid, name.strip())
    return names


async def _scoped_appointment_logs(db: AsyncSession, current_user: UserResponse) -> List[Activity_logs]:
    """Every appointment (any date) of the customers the caller may see — admin/accounting and
    other roles see everything; a rep/manager only their own scope."""
    query = select(Activity_logs).where(
        and_(Activity_logs.visit_type == "debt_follow_up", Activity_logs.collection_date.isnot(None))
    )
    scope = await get_customer_scope(db, current_user)
    scoped_names = await get_scoped_pharmacy_names(db, scope)
    if scoped_names is not None:
        if not scoped_names:
            query = query.where(false())
        else:
            query = query.where(name_sql(Activity_logs.pharmacy_name).in_(scoped_names))
    return (await db.execute(query)).scalars().all()


async def _open_appointments(db: AsyncSession, current_user: UserResponse, last_day: date, user_id: Optional[str] = None):
    """Open appointments due on or before `last_day`, with their extras, ordered by date."""
    logs = await _scoped_appointment_logs(db, current_user)
    resolved = await _resolve_appointments(db, logs)
    resolved = [r for r in resolved if r["log"].collection_date <= last_day]
    if user_id:
        resolved = [r for r in resolved if r["log"].user_id == user_id]
    resolved.sort(key=lambda r: (r["log"].collection_date, r["log"].id))
    today = date.today()
    visited_at = await _visits_on_due_dates(db, resolved, today)
    reps = await _rep_names(db, {r["log"].user_id for r in resolved})
    for r in resolved:
        log = r["log"]
        due_has_come = log.collection_date <= today
        r["visit_at"] = visited_at.get(log.id)
        r["visited"] = (r["visit_at"] is not None) if due_has_come else None
        r["missed"] = bool(log.collection_date < today and r["visit_at"] is None)
        r["rep_name"] = reps.get(log.user_id)
    return resolved


@router.get("/upcoming", response_model=CollectionsResponse)
async def get_upcoming_collections(
    days_ahead: int = Query(7, ge=1, le=30),
    user_id: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
    current_user: UserResponse = Depends(get_current_user),
):
    """Open collection appointments: overdue, today's and those within days_ahead days."""
    today = date.today()
    resolved = await _open_appointments(db, current_user, today + timedelta(days=days_ahead), user_id)

    items: List[CollectionItem] = []
    overdue_count = 0
    today_count = 0
    upcoming_count = 0

    for r in resolved:
        log, col_date = r["log"], r["log"].collection_date
        if col_date < today:
            status = "overdue"
            overdue_count += 1
        elif col_date == today:
            status = "today"
            today_count += 1
        else:
            status = "upcoming"
            upcoming_count += 1

        items.append(
            CollectionItem(
                id=log.id,
                user_id=log.user_id,
                rep_name=r["rep_name"],
                pharmacy_id=log.pharmacy_id,
                pharmacy_name=log.pharmacy_name,
                collection_date=col_date.isoformat(),
                details=log.details,
                timestamp=log.timestamp.isoformat() if log.timestamp else None,
                status=status,
                appointment_number=r["number"],
                previous_dates=r["previous_dates"],
                renewal_gap_days=r["gap"],
                visited_on_due_date=r["visited"],
                visit_at=r["visit_at"].isoformat() if r["visit_at"] else None,
                missed_visit=r["missed"],
            )
        )

    return CollectionsResponse(
        items=items,
        overdue_count=overdue_count,
        today_count=today_count,
        upcoming_count=upcoming_count,
        total=len(items),
    )


@router.get("/notifications")
async def get_collection_notifications(
    db: AsyncSession = Depends(get_db),
    current_user: UserResponse = Depends(get_current_user),
):
    """Get notification summary for current user - overdue + today's collections.

    Same open appointments and scoping as /upcoming (a rep/manager only their own
    customers; admin/accounting and other roles everything)."""
    today = date.today()
    tomorrow = today + timedelta(days=1)
    resolved = await _open_appointments(db, current_user, tomorrow)

    notifications = []
    for r in resolved:
        log, col_date = r["log"], r["log"].collection_date
        if col_date < today:
            notif_type = "overdue"
            message = f"موعد استحصال متأخر: {log.pharmacy_name or 'صيدلية'}"
        elif col_date == today:
            notif_type = "today"
            message = f"موعد استحصال اليوم: {log.pharmacy_name or 'صيدلية'}"
        else:
            notif_type = "tomorrow"
            message = f"موعد استحصال غداً: {log.pharmacy_name or 'صيدلية'}"
        if r["number"] > 1:
            message += f" (موعد رقم {r['number']})"
        if r["missed"]:
            message += " — لم يذهب المندوب للزبون بالموعد"

        notifications.append({
            "id": log.id,
            "type": notif_type,
            "message": message,
            "pharmacy_name": log.pharmacy_name,
            "pharmacy_id": log.pharmacy_id,
            "collection_date": col_date.isoformat(),
            "details": log.details,
            "appointment_number": r["number"],
            "missed_visit": r["missed"],
        })

    return {
        "notifications": notifications,
        "overdue_count": sum(1 for n in notifications if n["type"] == "overdue"),
        "today_count": sum(1 for n in notifications if n["type"] == "today"),
        "tomorrow_count": sum(1 for n in notifications if n["type"] == "tomorrow"),
        "total": len(notifications),
    }
