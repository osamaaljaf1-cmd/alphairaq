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


class AppointmentStep(BaseModel):
    """One appointment of the chain (from the first one after the last payment to the current one)."""
    taken_at: Optional[str] = None  # when the rep took it
    date: Optional[str] = None  # the collection date it set
    rep_name: Optional[str] = None


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
    history: List[AppointmentStep] = []  # every appointment of the chain, the current one last
    first_taken_at: Optional[str] = None  # when the first appointment of the chain was taken

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
                "chain": chain,
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
    reps = await _rep_names(db, {l.user_id for r in resolved for l in r.get("chain", [r["log"]])})
    for r in resolved:
        log = r["log"]
        due_has_come = log.collection_date <= today
        r["visit_at"] = visited_at.get(log.id)
        r["visited"] = (r["visit_at"] is not None) if due_has_come else None
        r["missed"] = bool(log.collection_date < today and r["visit_at"] is None)
        r["rep_name"] = reps.get(log.user_id)
        chain = r.get("chain") or [log]
        r["history"] = [
            AppointmentStep(
                taken_at=_as_utc(l.timestamp).isoformat() if l.timestamp else None,
                date=l.collection_date.isoformat() if l.collection_date else None,
                rep_name=reps.get(l.user_id),
            )
            for l in chain
        ]
        r["first_taken_at"] = r["history"][0].taken_at if r["history"] else None
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
                history=r["history"],
                first_taken_at=r["first_taken_at"],
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



class UnvisitedDebt(BaseModel):
    customer_name: str
    pharmacy_id: Optional[int] = None
    area: Optional[str] = None
    rep_name: Optional[str] = None  # the customer's own rep
    remaining: float
    invoices: int
    oldest_invoice_date: Optional[str] = None
    days_open: Optional[int] = None  # days since the oldest unpaid invoice
    last_visit_at: Optional[str] = None  # the last visit / order / collection ever (before the debt)


class UnvisitedDebtsResponse(BaseModel):
    items: List[UnvisitedDebt]
    total: int
    total_remaining: float


@router.get("/unvisited-debts", response_model=UnvisitedDebtsResponse)
async def get_unvisited_debts(
    limit: int = Query(200, ge=1, le=2000),
    db: AsyncSession = Depends(get_db),
    current_user: UserResponse = Depends(get_current_user),
):
    """ديون لم تُزر: the customers (in the caller's scope) who owe money, have no open collection
    appointment, and were not visited since their oldest unpaid invoice: no visit logged, no order
    and no collection at them from that day on, by anyone. The oldest debts first."""
    from models.orders import Orders

    scope = await get_customer_scope(db, current_user)
    scoped = await get_scoped_pharmacy_names(db, scope)
    if scoped is not None and not scoped:
        return UnvisitedDebtsResponse(items=[], total=0, total_remaining=0)

    # who owes, how much, since when
    owing: Dict[str, dict] = {}
    for name, remaining, inv_date, created in (
        await db.execute(
            select(Debts.customer_name, Debts.remaining_amount, Debts.invoice_date, Debts.created_at).where(Debts.remaining_amount > 0.005)
        )
    ).all():
        key = normalize_name(name)
        if not key or (scoped is not None and key not in scoped):
            continue
        day: Optional[date] = None
        try:
            day = date.fromisoformat((inv_date or "")[:10])
        except ValueError:
            day = _local_day(created)
        o = owing.setdefault(key, {"name": (name or "").strip(), "remaining": 0.0, "invoices": 0, "oldest": None})
        o["remaining"] += float(remaining or 0)
        o["invoices"] += 1
        if day and (o["oldest"] is None or day < o["oldest"]):
            o["oldest"] = day
    if not owing:
        return UnvisitedDebtsResponse(items=[], total=0, total_remaining=0)

    # the customers' records: id, area name, rep, area id
    pharmacy: Dict[str, tuple] = {}
    for pid, pname, area, rep_id, area_id in (
        await db.execute(select(Pharmacies.id, Pharmacies.name, Pharmacies.area, Pharmacies.representative_id, Pharmacies.area_id))
    ).all():
        k = normalize_name(pname)
        if k in owing and k not in pharmacy:
            pharmacy[k] = (pid, area, rep_id, area_id)
    id_to_key = {v[0]: k for k, v in pharmacy.items()}

    # customers with an open appointment are followed already
    # (an appointment may be logged under the name without the area suffix the debt carries: both count)
    followed = {k for r in await _resolve_appointments(db, await _scoped_appointment_logs(db, current_user)) for k in r["keys"]}

    # the last contact with each customer: a visit logged, an order, a collection (anyone's)
    last: Dict[str, datetime] = {}

    def seen(key: Optional[str], ts: Optional[datetime]):
        ts = _as_utc(ts)
        if key and ts and (key not in last or ts > last[key]):
            last[key] = ts

    for pid, pname, ts in (
        await db.execute(
            select(Activity_logs.pharmacy_id, Activity_logs.pharmacy_name, Activity_logs.timestamp).where(Activity_logs.type == "pharmacy_visit")
        )
    ).all():
        seen(id_to_key.get(pid) if pid in id_to_key else normalize_name(pname), ts)
    if id_to_key:
        for pid, ts in (
            await db.execute(select(Orders.pharmacy_id, func.max(Orders.created_at)).where(Orders.pharmacy_id.in_(list(id_to_key))).group_by(Orders.pharmacy_id))
        ).all():
            seen(id_to_key.get(pid), ts)
    for cname, ts in (
        await db.execute(
            select(Payments.customer_name, func.max(Payments.created_at))
            .where(or_(Payments.status.is_(None), Payments.status != "canceled"))
            .group_by(Payments.customer_name)
        )
    ).all():
        seen(normalize_name(cname), ts)

    # the rep(s) of each customer: those linked to its area or to a level above it in the tree,
    # else the rep written on the customer record
    from models.areas import Areas
    from models.representative_areas import Representative_areas

    rep_name_by_id = {rid: (name or "").strip() for rid, name in (await db.execute(select(Representatives.id, Representatives.name).where(Representatives.role == "rep"))).all()}
    parents = dict((await db.execute(select(Areas.id, Areas.parent_area_id))).all())
    reps_on_area: Dict[int, List[int]] = {}
    for rid, aid in (await db.execute(select(Representative_areas.representative_id, Representative_areas.area_id))).all():
        if rid in rep_name_by_id:
            reps_on_area.setdefault(aid, []).append(rid)

    def reps_of(area_id: Optional[int], own_rep: Optional[int]) -> Optional[str]:
        found: List[int] = []
        cur, seen_areas = area_id, set()
        while cur is not None and cur not in seen_areas:
            seen_areas.add(cur)
            for rid in reps_on_area.get(cur, []):
                if rid not in found:
                    found.append(rid)
            cur = parents.get(cur)
        if not found and own_rep in rep_name_by_id:
            found = [own_rep]
        return "، ".join(rep_name_by_id[r] for r in found if rep_name_by_id.get(r)) or None

    today = datetime.now(_LOCAL_TZ).date()
    items: List[UnvisitedDebt] = []
    for key, o in owing.items():
        if key in followed:
            continue
        lv = last.get(key)
        if lv is not None and o["oldest"] is not None and _local_day(lv) >= o["oldest"]:
            continue  # visited since the debt
        if lv is not None and o["oldest"] is None:
            continue
        pid, area, rep_id, area_id = pharmacy.get(key, (None, None, None, None))
        items.append(
            UnvisitedDebt(
                customer_name=o["name"],
                pharmacy_id=pid,
                area=area,
                rep_name=reps_of(area_id, rep_id),
                remaining=round(o["remaining"], 2),
                invoices=o["invoices"],
                oldest_invoice_date=o["oldest"].isoformat() if o["oldest"] else None,
                days_open=(today - o["oldest"]).days if o["oldest"] else None,
                last_visit_at=lv.isoformat() if lv else None,
            )
        )
    # the oldest unpaid debt first, then the larger amount
    items.sort(key=lambda i: (i.oldest_invoice_date or "9999-12-31", -i.remaining, i.customer_name))
    return UnvisitedDebtsResponse(items=items[:limit], total=len(items), total_remaining=round(sum(i.remaining for i in items), 2))
