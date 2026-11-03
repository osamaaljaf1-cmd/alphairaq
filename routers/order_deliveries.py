"""The delivery person closes an order at the customer's door.

- POST /api/v1/order-deliveries/{order_id}/deliver   photo of the stamped receipt (required) and notes
- POST /api/v1/order-deliveries/{order_id}/refuse    the customer refused: the order is returned at
  once (status "refused", so it no longer counts in the rep's sales) and the rep who owns the
  customer and the rep's direct manager are notified
- GET  /api/v1/order-deliveries/{order_id}[/photo]    what was recorded
- GET  /api/v1/order-deliveries/customer/{pharmacy_id}   the delivered-order photos of a customer
- GET  /api/v1/order-events/mine, POST /api/v1/order-events/read   the notifications

Both closing steps need the delivery action permission and an order that is out for delivery.
"""
import logging
from datetime import date, datetime, time, timedelta, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from core.database import get_db
from dependencies.auth import get_current_user
from models.app_users import App_users
from models.order_deliveries import OrderDeliveries, OrderEvents
from models.orders import Orders
from models.pharmacies import Pharmacies
from models.representative_areas import Representative_areas
from models.representatives import Representatives
from models.user_assignments import User_assignments
from schemas.auth import UserResponse
from services.area_scope import get_customer_scope, get_scoped_pharmacy_names
from services.customer_names import normalize_name
from services.feature_permissions import require_feature
from services.access import require_view_any
from services.permission_check import require_permission

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", tags=["order_deliveries"])

MAX_PHOTO_CHARS = 2_000_000  # a compressed photo is far below this; it only stops abuse
MAX_THUMB_CHARS = 120_000
MAX_NOTE_CHARS = 1000

_tables_ready = False


async def ensure_tables(db: AsyncSession) -> None:
    """Create the two tables on first use (idempotent); never touches the orders table."""
    global _tables_ready
    if _tables_ready:
        return
    conn = await db.connection()
    await conn.run_sync(lambda c: OrderDeliveries.__table__.create(c, checkfirst=True))
    await conn.run_sync(lambda c: OrderEvents.__table__.create(c, checkfirst=True))
    await db.commit()
    _tables_ready = True


class DeliverBody(BaseModel):
    photo: str  # data URL of the receipt photo
    thumb: Optional[str] = None
    notes: Optional[str] = None


class RefuseBody(BaseModel):
    reason: str
    notes: Optional[str] = None
    photo: Optional[str] = None
    thumb: Optional[str] = None


def _check_image(value: Optional[str], limit: int, label: str, required: bool) -> Optional[str]:
    if not value:
        if required:
            raise HTTPException(status_code=400, detail=f"{label} مطلوبة")
        return None
    if not value.startswith("data:image/"):
        raise HTTPException(status_code=400, detail=f"{label} غير صالحة")
    if len(value) > limit:
        raise HTTPException(status_code=400, detail=f"حجم {label} كبير جداً")
    return value


def _clean_note(value: Optional[str]) -> Optional[str]:
    text = (value or "").strip()
    if len(text) > MAX_NOTE_CHARS:
        raise HTTPException(status_code=400, detail="الملاحظة طويلة جداً")
    return text or None


async def _account_name(db: AsyncSession, user: UserResponse) -> str:
    name = (
        await db.execute(select(App_users.name).where(App_users.user_id == str(user.id)).order_by(App_users.id))
    ).scalars().first()
    return (name or "").strip() or user.name or str(user.id)


async def _load_order(db: AsyncSession, order_id: int) -> Orders:
    order = (await db.execute(select(Orders).where(Orders.id == order_id))).scalar_one_or_none()
    if order is None:
        raise HTTPException(status_code=404, detail="الطلب غير موجود")
    return order


async def _order_visible(db: AsyncSession, user: UserResponse, order: Orders) -> bool:
    """A rep sees their own orders, a manager their reps' ones, everyone else all of them."""
    scope = await get_customer_scope(db, user)
    if scope.unrestricted:
        return True
    return order.user_id == str(user.id) or (order.representative_id is not None and order.representative_id in scope.rep_ids)


async def _recipients(db: AsyncSession, order: Orders, pharmacy: Optional[Pharmacies]) -> dict:
    """The reps who own this customer (the order's rep, the customer's rep, the reps of its
    area) and their direct managers: {"reps": [user ids], "managers": [user ids]}."""
    rep_ids = set()
    if order.representative_id:
        rep_ids.add(order.representative_id)
    if pharmacy is not None:
        if pharmacy.representative_id:
            rep_ids.add(pharmacy.representative_id)
        if getattr(pharmacy, "area_id", None):
            for (rid,) in (
                await db.execute(select(Representative_areas.representative_id).where(Representative_areas.area_id == pharmacy.area_id))
            ).all():
                rep_ids.add(rid)
    reps: List[str] = []
    for uid in (
        await db.execute(select(Representatives.user_id).where(Representatives.id.in_(rep_ids), Representatives.role == "rep"))
    ).scalars().all() if rep_ids else []:
        if uid and uid not in reps:
            reps.append(uid)
    managers: List[str] = []
    if rep_ids:
        rows = await db.execute(
            select(User_assignments.user_id, User_assignments.manager_rep_id).where(
                User_assignments.assigned_rep_id.in_(rep_ids), User_assignments.assignment_type == "manager"
            )
        )
        for assign_uid, mgr_rep_id in rows.all():
            mgr_uid = (
                await db.execute(select(Representatives.user_id).where(Representatives.id == mgr_rep_id))
            ).scalar_one_or_none() or assign_uid
            if mgr_uid and mgr_uid not in managers and mgr_uid not in reps:
                managers.append(mgr_uid)
    return {"reps": reps, "managers": managers}


def _event(uid: str, kind: str, order: Orders, pharmacy_name: Optional[str], message: str) -> OrderEvents:
    return OrderEvents(
        target_user_id=uid, kind=kind, order_id=order.id, pharmacy_id=order.pharmacy_id, pharmacy_name=pharmacy_name, message=message
    )


@router.post("/order-deliveries/{order_id}/deliver")
async def deliver_order(
    order_id: int,
    data: DeliverBody,
    db: AsyncSession = Depends(get_db),
    current_user: UserResponse = Depends(get_current_user),
):
    await require_feature(db, current_user, "orders_delivery")
    photo = _check_image(data.photo, MAX_PHOTO_CHARS, "صورة الوصل", True)
    thumb = _check_image(data.thumb, MAX_THUMB_CHARS, "الصورة المصغّرة", False)
    notes = _clean_note(data.notes)
    await ensure_tables(db)

    order = await _load_order(db, order_id)
    if order.status != "delivering":
        raise HTTPException(status_code=409, detail="الطلب ليس قيد التوصيل")
    if (await db.execute(select(OrderDeliveries.id).where(OrderDeliveries.order_id == order_id))).first():
        raise HTTPException(status_code=409, detail="سُجّل توصيل هذا الطلب مسبقاً")

    name = await _account_name(db, current_user)
    pharmacy = (await db.execute(select(Pharmacies).where(Pharmacies.id == order.pharmacy_id))).scalar_one_or_none()
    pharmacy_name = pharmacy.name if pharmacy else None
    try:
        record = OrderDeliveries(
            order_id=order_id,
            pharmacy_id=order.pharmacy_id,
            representative_id=order.representative_id,
            outcome="delivered",
            notes=notes,
            photo=photo,
            thumb=thumb,
            order_amount=order.total_amount,
            recorded_by=str(current_user.id),
            recorded_by_name=name,
        )
        db.add(record)
        order.status = "delivered"
        order.delivered_at = datetime.now(timezone.utc)
        order.delivered_by = name
        who = await _recipients(db, order, pharmacy)
        text = f"تم توصيل الطلب #{order_id} إلى {pharmacy_name or 'الزبون'} بنجاح"
        if notes:
            text += f" — ملاحظة الموصل: {notes}"
        for uid in who["reps"]:
            db.add(_event(uid, "order_delivered", order, pharmacy_name, text))
        await db.commit()
    except Exception:
        await db.rollback()
        raise
    return {"order_id": order_id, "status": "delivered", "notified": len(who["reps"])}


@router.post("/order-deliveries/{order_id}/refuse")
async def refuse_order(
    order_id: int,
    data: RefuseBody,
    db: AsyncSession = Depends(get_db),
    current_user: UserResponse = Depends(get_current_user),
):
    await require_feature(db, current_user, "orders_delivery")
    reason = (data.reason or "").strip()
    if not reason:
        raise HTTPException(status_code=400, detail="سبب الرفض مطلوب")
    if len(reason) > 200:
        raise HTTPException(status_code=400, detail="سبب الرفض طويل جداً")
    notes = _clean_note(data.notes)
    photo = _check_image(data.photo, MAX_PHOTO_CHARS, "الصورة", False)
    thumb = _check_image(data.thumb, MAX_THUMB_CHARS, "الصورة المصغّرة", False)
    await ensure_tables(db)

    order = await _load_order(db, order_id)
    if order.status != "delivering":
        raise HTTPException(status_code=409, detail="الطلب ليس قيد التوصيل")
    if (await db.execute(select(OrderDeliveries.id).where(OrderDeliveries.order_id == order_id))).first():
        raise HTTPException(status_code=409, detail="سُجّلت نتيجة هذا الطلب مسبقاً")

    name = await _account_name(db, current_user)
    pharmacy = (await db.execute(select(Pharmacies).where(Pharmacies.id == order.pharmacy_id))).scalar_one_or_none()
    pharmacy_name = pharmacy.name if pharmacy else None
    amount = order.total_amount or 0
    try:
        db.add(
            OrderDeliveries(
                order_id=order_id,
                pharmacy_id=order.pharmacy_id,
                representative_id=order.representative_id,
                outcome="refused",
                reason=reason,
                notes=notes,
                photo=photo,
                thumb=thumb,
                order_amount=order.total_amount,
                recorded_by=str(current_user.id),
                recorded_by_name=name,
            )
        )
        # returned at once: it stops counting as a sale (the sales figures only count the
        # statuses from manager approval up to delivered)
        order.status = "refused"
        who = await _recipients(db, order, pharmacy)
        base = f"رفض الزبون {pharmacy_name or ''} استلام الطلب #{order_id} ({amount:,.0f} د.ع). السبب: {reason}"
        if notes:
            base += f" — ملاحظة الموصل ({name}): {notes}"
        for uid in who["reps"]:
            db.add(_event(uid, "order_refused", order, pharmacy_name, base + " — أُرجع الطلب وحُذف من مبيعاتك."))
        for uid in who["managers"]:
            db.add(_event(uid, "order_refused", order, pharmacy_name, base + " — أُرجع الطلب وحُذف من مبيعات المندوب."))
        await db.commit()
    except Exception:
        await db.rollback()
        raise
    return {"order_id": order_id, "status": "refused", "notified_reps": len(who["reps"]), "notified_managers": len(who["managers"])}


@router.get("/order-deliveries/customer/{pharmacy_id}")
async def customer_deliveries(
    pharmacy_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: UserResponse = Depends(get_current_user),
):
    """The receipt photos of a customer's delivered (and refused) orders, newest first."""
    await require_permission(db, current_user, "customers", "view")
    pharmacy = (await db.execute(select(Pharmacies).where(Pharmacies.id == pharmacy_id))).scalar_one_or_none()
    if pharmacy is None:
        raise HTTPException(status_code=404, detail="الزبون غير موجود")
    scope = await get_customer_scope(db, current_user)
    visible = await get_scoped_pharmacy_names(db, scope)
    if visible is not None and normalize_name(pharmacy.name) not in visible:
        raise HTTPException(status_code=403, detail="هذا الزبون خارج نطاق صلاحياتك")
    await ensure_tables(db)
    rows = (
        await db.execute(
            select(OrderDeliveries, Orders.total_amount, Orders.created_at)
            .join(Orders, Orders.id == OrderDeliveries.order_id)
            .where(OrderDeliveries.pharmacy_id == pharmacy_id)
            .order_by(OrderDeliveries.recorded_at.desc(), OrderDeliveries.id.desc())
            .limit(60)
        )
    ).all()
    return [
        {
            "order_id": d.order_id,
            "outcome": d.outcome,
            "reason": d.reason,
            "notes": d.notes,
            "thumb": d.thumb,
            "has_photo": bool(d.photo),
            "amount": total,
            "recorded_by_name": d.recorded_by_name,
            "recorded_at": d.recorded_at.isoformat() if d.recorded_at else None,
        }
        for d, total, _ in rows
    ]


@router.get("/order-deliveries/customers-with-photos")
async def customers_with_photos(
    db: AsyncSession = Depends(get_db),
    current_user: UserResponse = Depends(get_current_user),
):
    """The customers that have delivered orders with a receipt photo (and how many): the debts
    page shows a photo icon next to them. Only the customers the caller may see."""
    await require_view_any(db, current_user, ["customers", "orders", "debts"])
    await ensure_tables(db)
    counts = (
        await db.execute(
            select(OrderDeliveries.pharmacy_id, func.count(OrderDeliveries.id))
            .where(OrderDeliveries.outcome == "delivered", OrderDeliveries.photo.isnot(None), OrderDeliveries.pharmacy_id.isnot(None))
            .group_by(OrderDeliveries.pharmacy_id)
        )
    ).all()
    if not counts:
        return []
    scope = await get_customer_scope(db, current_user)
    visible = await get_scoped_pharmacy_names(db, scope)
    names = {pid: name for pid, name in (await db.execute(select(Pharmacies.id, Pharmacies.name).where(Pharmacies.id.in_([c[0] for c in counts])))).all()}
    return [
        {"pharmacy_id": pid, "name": names[pid], "count": n}
        for pid, n in counts
        if pid in names and (visible is None or normalize_name(names[pid]) in visible)
    ]


@router.get("/order-deliveries/index")
async def deliveries_index(
    limit: int = 1500,
    db: AsyncSession = Depends(get_db),
    current_user: UserResponse = Depends(get_current_user),
):
    """A compact list of what was recorded per order (no photos): which orders have a delivery
    record and a receipt photo, so the orders list can show a photo icon on them. A rep/manager
    only gets the records of their own scope."""
    await require_permission(db, current_user, "orders", "view")
    await ensure_tables(db)
    query = select(OrderDeliveries.order_id, OrderDeliveries.outcome, OrderDeliveries.photo.isnot(None))
    scope = await get_customer_scope(db, current_user)
    if not scope.unrestricted:
        query = query.where(OrderDeliveries.representative_id.in_(scope.rep_ids or [-1]))
    rows = (await db.execute(query.order_by(OrderDeliveries.id.desc()).limit(max(1, min(limit, 5000))))).all()
    return [{"order_id": oid, "outcome": outcome, "has_photo": bool(has_photo)} for oid, outcome, has_photo in rows]


@router.get("/order-deliveries/month")
async def month_records(
    db: AsyncSession = Depends(get_db),
    current_user: UserResponse = Depends(get_current_user),
):
    """What was delivered or refused this (Iraq) month, per order, without the photos: the
    dashboard's delivered / refused totals. A rep/manager only gets their own scope's orders."""
    await require_permission(db, current_user, "orders", "view")
    await ensure_tables(db)
    local = timezone(timedelta(hours=3))
    today = datetime.now(local).date()
    month_start = datetime.combine(date(today.year, today.month, 1), time.min, tzinfo=local).astimezone(timezone.utc)
    query = select(OrderDeliveries).where(OrderDeliveries.recorded_at >= month_start)
    scope = await get_customer_scope(db, current_user)
    if not scope.unrestricted:
        query = query.where(OrderDeliveries.representative_id.in_(scope.rep_ids or [-1]))
    rows = (await db.execute(query.order_by(OrderDeliveries.id.desc()))).scalars().all()
    return [
        {
            "order_id": d.order_id,
            "outcome": d.outcome,
            "reason": d.reason,
            "amount": d.order_amount,
            "pharmacy_id": d.pharmacy_id,
            "representative_id": d.representative_id,
            "recorded_at": d.recorded_at.isoformat() if d.recorded_at else None,
        }
        for d in rows
    ]


async def _visible_record(db: AsyncSession, current_user: UserResponse, order_id: int) -> OrderDeliveries:
    await require_permission(db, current_user, "orders", "view")
    await ensure_tables(db)
    order = await _load_order(db, order_id)
    if not await _order_visible(db, current_user, order):
        raise HTTPException(status_code=403, detail="هذا الطلب خارج نطاق صلاحياتك")
    record = (await db.execute(select(OrderDeliveries).where(OrderDeliveries.order_id == order_id))).scalar_one_or_none()
    if record is None:
        raise HTTPException(status_code=404, detail="لا يوجد سجل توصيل لهذا الطلب")
    return record


@router.get("/order-deliveries/{order_id}")
async def get_delivery(
    order_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: UserResponse = Depends(get_current_user),
):
    d = await _visible_record(db, current_user, order_id)
    return {
        "order_id": d.order_id,
        "outcome": d.outcome,
        "reason": d.reason,
        "notes": d.notes,
        "has_photo": bool(d.photo),
        "recorded_by_name": d.recorded_by_name,
        "recorded_at": d.recorded_at.isoformat() if d.recorded_at else None,
    }


@router.get("/order-deliveries/{order_id}/photo")
async def get_delivery_photo(
    order_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: UserResponse = Depends(get_current_user),
):
    d = await _visible_record(db, current_user, order_id)
    if not d.photo:
        raise HTTPException(status_code=404, detail="لا توجد صورة")
    return {"order_id": d.order_id, "photo": d.photo}


@router.get("/order-events/mine")
async def my_order_events(
    db: AsyncSession = Depends(get_db),
    current_user: UserResponse = Depends(get_current_user),
):
    await ensure_tables(db)
    uid = str(current_user.id)
    rows = (
        await db.execute(
            select(OrderEvents).where(OrderEvents.target_user_id == uid).order_by(OrderEvents.id.desc()).limit(30)
        )
    ).scalars().all()
    return {
        "unread_count": sum(1 for e in rows if e.read_at is None),
        "items": [
            {
                "id": e.id,
                "kind": e.kind,
                "order_id": e.order_id,
                "pharmacy_id": e.pharmacy_id,
                "pharmacy_name": e.pharmacy_name,
                "message": e.message,
                "created_at": e.created_at.isoformat() if e.created_at else None,
                "read": e.read_at is not None,
            }
            for e in rows
        ],
    }


class ReadBody(BaseModel):
    ids: Optional[List[int]] = None  # none = all of mine


@router.post("/order-events/read")
async def mark_events_read(
    data: ReadBody,
    db: AsyncSession = Depends(get_db),
    current_user: UserResponse = Depends(get_current_user),
):
    await ensure_tables(db)
    stmt = update(OrderEvents).where(OrderEvents.target_user_id == str(current_user.id), OrderEvents.read_at.is_(None))
    if data.ids is not None:
        stmt = stmt.where(OrderEvents.id.in_(data.ids))
    result = await db.execute(stmt.values(read_at=datetime.now(timezone.utc)))
    await db.commit()
    return {"marked": result.rowcount}
