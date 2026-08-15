import logging
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from core.database import get_db
from dependencies.auth import get_current_user
from schemas.auth import UserResponse
from models.orders import Orders
from models.returns import Returns
from models.debts import Debts
from models.pharmacies import Pharmacies
from models.doctors import Doctors
from models.items import Products
from models.representatives import Representatives

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/customer-activity", tags=["customer-activity"])


class ActivityEvent(BaseModel):
    type: str  # "sale" | "return"
    id: int
    date: Optional[datetime] = None
    status: Optional[str] = None
    amount: Optional[float] = None
    rep_name: Optional[str] = None
    product_name: Optional[str] = None
    quantity: Optional[int] = None
    reason: Optional[str] = None
    notes: Optional[str] = None

    class Config:
        from_attributes = True


class DebtSummary(BaseModel):
    has_debt: bool
    total_remaining: float
    unpaid_count: int


class CustomerActivityResponse(BaseModel):
    customer_name: str
    debt_summary: DebtSummary
    events: List[ActivityEvent]


async def _debt_summary(db: AsyncSession, customer_name: str) -> DebtSummary:
    result = await db.execute(
        select(
            func.coalesce(func.sum(Debts.remaining_amount), 0.0),
            func.count(Debts.id).filter(Debts.status != "paid"),
        ).where(Debts.customer_name == customer_name)
    )
    total_remaining, unpaid_count = result.one()
    total_remaining = float(total_remaining or 0)
    return DebtSummary(
        has_debt=total_remaining > 0.01,
        total_remaining=round(total_remaining, 2),
        unpaid_count=unpaid_count or 0,
    )


async def _build_timeline(
    db: AsyncSession, pharmacy_id: Optional[int] = None, doctor_id: Optional[int] = None
) -> List[ActivityEvent]:
    order_q = select(Orders)
    return_q = select(Returns)
    if pharmacy_id is not None:
        order_q = order_q.where(Orders.pharmacy_id == pharmacy_id)
        return_q = return_q.where(Returns.pharmacy_id == pharmacy_id)
    else:
        order_q = order_q.where(Orders.doctor_id == doctor_id)
        return_q = return_q.where(Returns.doctor_id == doctor_id)

    orders = (await db.execute(order_q.order_by(Orders.created_at.desc()).limit(300))).scalars().all()
    returns = (await db.execute(return_q.order_by(Returns.created_at.desc()).limit(300))).scalars().all()

    rep_ids = {o.representative_id for o in orders if o.representative_id}
    rep_ids |= {r.representative_id for r in returns if r.representative_id}
    rep_map = {}
    if rep_ids:
        rep_result = await db.execute(select(Representatives).where(Representatives.id.in_(rep_ids)))
        rep_map = {r.id: r.name for r in rep_result.scalars().all()}

    product_ids = {r.product_id for r in returns if r.product_id}
    product_map = {}
    if product_ids:
        prod_result = await db.execute(select(Products).where(Products.id.in_(product_ids)))
        product_map = {p.id: p.name for p in prod_result.scalars().all()}

    events: List[ActivityEvent] = []
    for o in orders:
        events.append(ActivityEvent(
            type="sale",
            id=o.id,
            date=o.created_at,
            status=o.status,
            amount=o.total_amount,
            rep_name=rep_map.get(o.representative_id),
            notes=o.notes,
        ))
    for r in returns:
        events.append(ActivityEvent(
            type="return",
            id=r.id,
            date=r.created_at,
            status=r.status,
            rep_name=rep_map.get(r.representative_id),
            product_name=product_map.get(r.product_id),
            quantity=r.quantity,
            reason=r.reason,
        ))

    events.sort(key=lambda e: e.date or datetime.min, reverse=True)
    return events


@router.get("/pharmacy/{pharmacy_id}", response_model=CustomerActivityResponse)
async def get_pharmacy_activity(
    pharmacy_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: UserResponse = Depends(get_current_user),
):
    """Chronological sales + returns timeline for one pharmacy, with its current debt total."""
    result = await db.execute(select(Pharmacies).where(Pharmacies.id == pharmacy_id))
    pharmacy = result.scalar_one_or_none()
    if not pharmacy:
        raise HTTPException(status_code=404, detail="الصيدلية غير موجودة")

    events = await _build_timeline(db, pharmacy_id=pharmacy_id)
    debt = await _debt_summary(db, pharmacy.name)
    return CustomerActivityResponse(customer_name=pharmacy.name, debt_summary=debt, events=events)


@router.get("/doctor/{doctor_id}", response_model=CustomerActivityResponse)
async def get_doctor_activity(
    doctor_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: UserResponse = Depends(get_current_user),
):
    """Chronological sales + returns timeline for one doctor, with its current debt total."""
    result = await db.execute(select(Doctors).where(Doctors.id == doctor_id))
    doctor = result.scalar_one_or_none()
    if not doctor:
        raise HTTPException(status_code=404, detail="الطبيب غير موجود")

    events = await _build_timeline(db, doctor_id=doctor_id)
    debt = await _debt_summary(db, doctor.name)
    return CustomerActivityResponse(customer_name=doctor.name, debt_summary=debt, events=events)
