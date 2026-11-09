"""What each doctor is owed from the deals, per order.

A doctor's deal amount is the bonus of the order lines that carry one of the doctor's agreements (the
line stores the agreement it was sold under and the bonus amount computed then). It goes directly to the
doctor, whoever the rep named on the order. Cancelled orders (rejected, or refused by the customer) are left
out. The month is an Iraq month.

- GET /api/v1/doctor-deals                 per doctor for a month: orders and amount
- GET /api/v1/doctor-deals/{doctor_id}     one doctor: each order, its lines and the amount

A rep or manager only sees the lines of the orders they may see (the same scoping as the orders list).
"""
import logging
from collections import defaultdict
from datetime import date, datetime, time, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.database import get_db
from dependencies.auth import get_current_user
from models.agreements import Agreements
from models.doctors import Doctors
from models.order_items import Order_items
from models.orders import Orders
from models.pharmacies import Pharmacies
from models.products import Products
from schemas.auth import UserResponse
from services.access import require_view_any, visible_user_ids

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/doctor-deals", tags=["doctor_deals"])

_LOCAL_TZ = timezone(timedelta(hours=3))
_CANCELLED = ("rejected", "refused")


def _month_bounds(month: Optional[str]):
    today = datetime.now(_LOCAL_TZ).date()
    if month:
        try:
            year, mon = (int(x) for x in month.split("-"))
            first = date(year, mon, 1)
        except (ValueError, TypeError):
            raise HTTPException(status_code=400, detail="الشهر غير صالح (السنة-الشهر مثل 2026-11)")
    else:
        first = date(today.year, today.month, 1)
    nxt = date(first.year + (first.month // 12), first.month % 12 + 1, 1)
    to_utc = lambda d: datetime.combine(d, time.min, tzinfo=_LOCAL_TZ).astimezone(timezone.utc)
    return first, to_utc(first), to_utc(nxt)


async def _rows(db: AsyncSession, current_user: UserResponse, start_utc, end_utc, doctor_id: Optional[int]):
    query = (
        select(Order_items, Orders, Agreements)
        .join(Orders, Orders.id == Order_items.order_id)
        .join(Agreements, Agreements.id == Order_items.agreement_id)
        .where(Order_items.agreement_id.isnot(None), Orders.status.notin_(_CANCELLED), Orders.created_at >= start_utc, Orders.created_at < end_utc)
        .order_by(Orders.id.desc(), Order_items.id)
    )
    if doctor_id is not None:
        query = query.where(Agreements.doctor_id == doctor_id)
    visible = await visible_user_ids(db, current_user)
    if visible is not None:
        query = query.where(Orders.user_id.in_(list(visible) or [""]))
    return (await db.execute(query)).all()


@router.get("")
async def summary(
    month: Optional[str] = Query(None, description="YYYY-MM (Iraq month); the current month by default"),
    db: AsyncSession = Depends(get_db),
    current_user: UserResponse = Depends(get_current_user),
):
    await require_view_any(db, current_user, ["agreements"])
    first, start, end = _month_bounds(month)
    per_doctor = defaultdict(lambda: {"orders": set(), "amount": 0.0, "lines": 0})
    for item, order, agreement in await _rows(db, current_user, start, end, None):
        d = per_doctor[agreement.doctor_id]
        d["orders"].add(order.id)
        d["amount"] += float(item.bonus_amount or 0)
        d["lines"] += 1
    names = {d.id: d.name for d in (await db.execute(select(Doctors).where(Doctors.id.in_(list(per_doctor) or [0])))).scalars().all()}
    rows = [
        {"doctor_id": did, "doctor_name": names.get(did, f"#{did}"), "orders": len(v["orders"]), "lines": v["lines"], "amount": round(v["amount"], 2)}
        for did, v in per_doctor.items()
    ]
    rows.sort(key=lambda r: (-r["amount"], r["doctor_name"]))
    return {"month": first.strftime("%Y-%m"), "total": round(sum(r["amount"] for r in rows), 2), "doctors": rows}


@router.get("/{doctor_id}")
async def doctor_ledger(
    doctor_id: int,
    month: Optional[str] = Query(None, description="YYYY-MM (Iraq month); the current month by default"),
    db: AsyncSession = Depends(get_db),
    current_user: UserResponse = Depends(get_current_user),
):
    await require_view_any(db, current_user, ["agreements"])
    doctor = (await db.execute(select(Doctors).where(Doctors.id == doctor_id))).scalar_one_or_none()
    if doctor is None:
        raise HTTPException(status_code=404, detail="الطبيب غير موجود")
    first, start, end = _month_bounds(month)
    rows = await _rows(db, current_user, start, end, doctor_id)
    pharmacy_ids = {o.pharmacy_id for _, o, _ in rows}
    product_ids = {i.product_id for i, _, _ in rows}
    pharmacies = {p.id: p.name for p in (await db.execute(select(Pharmacies).where(Pharmacies.id.in_(list(pharmacy_ids) or [0])))).scalars().all()}
    products = {p.id: p.name for p in (await db.execute(select(Products).where(Products.id.in_(list(product_ids) or [0])))).scalars().all()}
    orders: dict = {}
    for item, order, agreement in rows:
        o = orders.setdefault(
            order.id,
            {
                "order_id": order.id,
                "created_at": order.created_at.isoformat() if order.created_at else None,
                "status": order.status,
                "pharmacy_id": order.pharmacy_id,
                "pharmacy_name": pharmacies.get(order.pharmacy_id, f"#{order.pharmacy_id}"),
                "amount": 0.0,
                "lines": [],
            },
        )
        amount = float(item.bonus_amount or 0)
        o["amount"] = round(o["amount"] + amount, 2)
        o["lines"].append(
            {
                "product_id": item.product_id,
                "product_name": products.get(item.product_id, f"#{item.product_id}"),
                "quantity": item.quantity,
                "unit_price": item.unit_price,
                "amount": round(amount, 2),
                "agreement_id": agreement.id,
                "bonus_value": agreement.bonus_value,
                "bonus_type": agreement.bonus_type,
            }
        )
    out = list(orders.values())
    return {
        "doctor_id": doctor.id,
        "doctor_name": doctor.name,
        "month": first.strftime("%Y-%m"),
        "total": round(sum(o["amount"] for o in out), 2),
        "orders": out,
    }
