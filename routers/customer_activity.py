import logging
from datetime import date as date_type, datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from core.database import get_db
from dependencies.auth import get_current_user
from schemas.auth import UserResponse
from models.activity_logs import Activity_logs
from models.orders import Orders
from models.payments import Payments
from models.returns import Returns
from models.debts import Debts
from models.pharmacies import Pharmacies
from models.doctors import Doctors
from models.products import Products
from models.representatives import Representatives
from services.access import require_view_any
from services.area_scope import get_customer_scope
from services.customer_names import name_sql, normalize_name

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/customer-activity", tags=["customer-activity"])


class ActivityEvent(BaseModel):
    type: str  # "sale" | "return" | "payment" | "debt" | "visit" | "appointment"
    id: int
    date: Optional[datetime] = None
    status: Optional[str] = None
    amount: Optional[float] = None
    rep_name: Optional[str] = None
    product_name: Optional[str] = None
    quantity: Optional[int] = None
    reason: Optional[str] = None
    notes: Optional[str] = None
    reference: Optional[str] = None  # invoice number for debts
    remaining_amount: Optional[float] = None
    visit_type: Optional[str] = None
    collection_date: Optional[date_type] = None

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


async def _ensure_in_scope(db: AsyncSession, current_user: UserResponse, area_id, representative_id) -> None:
    """A rep/manager may only open the activity log of a customer in their own
    scope (assigned areas or reps) — the same rule the customer lists use."""
    scope = await get_customer_scope(db, current_user)
    if scope.unrestricted:
        return
    if (area_id is not None and area_id in scope.area_ids) or (
        representative_id is not None and representative_id in scope.rep_ids
    ):
        return
    raise HTTPException(status_code=403, detail="هذا الزبون خارج نطاق صلاحياتك")


async def _debt_summary(db: AsyncSession, customer_name: str) -> DebtSummary:
    result = await db.execute(
        select(
            func.coalesce(func.sum(Debts.remaining_amount), 0.0),
            func.count(Debts.id).filter(Debts.status != "paid"),
        ).where(name_sql(Debts.customer_name) == normalize_name(customer_name))
    )
    total_remaining, unpaid_count = result.one()
    total_remaining = float(total_remaining or 0)
    return DebtSummary(
        has_debt=total_remaining > 0.01,
        total_remaining=round(total_remaining, 2),
        unpaid_count=unpaid_count or 0,
    )


def _sort_key(e: "ActivityEvent") -> float:
    return e.date.timestamp() if e.date else 0.0


async def _build_timeline(
    db: AsyncSession,
    customer_name: str,
    pharmacy_id: Optional[int] = None,
    doctor_id: Optional[int] = None,
) -> List[ActivityEvent]:
    """Everything recorded against one customer: sales, returns, payments,
    debt invoices, visit reports and collection appointments."""
    order_q = select(Orders)
    return_q = select(Returns)
    if pharmacy_id is not None:
        order_q = order_q.where(Orders.pharmacy_id == pharmacy_id)
        return_q = return_q.where(Returns.pharmacy_id == pharmacy_id)
        log_q = select(Activity_logs).where(
            Activity_logs.type == "pharmacy_visit",
            or_(
                Activity_logs.pharmacy_id == pharmacy_id,
                func.lower(func.trim(Activity_logs.pharmacy_name)) == customer_name.strip().lower(),
            ),
        )
    else:
        order_q = order_q.where(Orders.doctor_id == doctor_id)
        return_q = return_q.where(Returns.doctor_id == doctor_id)
        log_q = select(Activity_logs).where(
            Activity_logs.type == "doctor_visit",
            Activity_logs.doctor_id == doctor_id,
        )

    name_key = normalize_name(customer_name)
    payment_q = select(Payments).where(name_sql(Payments.customer_name) == name_key)
    debt_q = select(Debts).where(name_sql(Debts.customer_name) == name_key)

    orders = (await db.execute(order_q.order_by(Orders.created_at.desc()).limit(300))).scalars().all()
    returns = (await db.execute(return_q.order_by(Returns.created_at.desc()).limit(300))).scalars().all()
    logs = (await db.execute(log_q.order_by(Activity_logs.timestamp.desc()).limit(300))).scalars().all()
    payments = (await db.execute(payment_q.order_by(Payments.created_at.desc()).limit(300))).scalars().all()
    debts = (await db.execute(debt_q.order_by(Debts.created_at.desc()).limit(300))).scalars().all()

    rep_ids = {o.representative_id for o in orders if o.representative_id}
    rep_ids |= {r.representative_id for r in returns if r.representative_id}
    rep_ids |= {l.rep_id for l in logs if l.rep_id}
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
    for p in payments:
        events.append(ActivityEvent(
            type="payment",
            id=p.id,
            date=p.created_at,
            status="canceled" if p.status == "canceled" else "active",
            amount=p.net_received,
            rep_name=p.rep_name,
            notes=p.notes,
        ))
    for d in debts:
        events.append(ActivityEvent(
            type="debt",
            id=d.id,
            date=d.created_at,
            status=d.status,
            amount=d.amount,
            remaining_amount=d.remaining_amount,
            reference=d.invoice_number,
            notes=d.notes,
        ))
    for l in logs:
        is_appointment = l.visit_type == "debt_follow_up" and l.collection_date is not None
        events.append(ActivityEvent(
            type="appointment" if is_appointment else "visit",
            id=l.id,
            date=l.timestamp,
            rep_name=rep_map.get(l.rep_id),
            notes=l.details,
            visit_type=l.visit_type,
            collection_date=l.collection_date,
        ))

    events.sort(key=_sort_key, reverse=True)
    return events


@router.get("/pharmacy/{pharmacy_id}", response_model=CustomerActivityResponse)
async def get_pharmacy_activity(
    pharmacy_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: UserResponse = Depends(get_current_user),
):
    """Chronological timeline for one pharmacy (sales, returns, payments, debts, visit reports,
    collection appointments), with its current debt total."""
    await require_view_any(db, current_user, ("customers",))
    result = await db.execute(select(Pharmacies).where(Pharmacies.id == pharmacy_id))
    pharmacy = result.scalar_one_or_none()
    if not pharmacy:
        raise HTTPException(status_code=404, detail="الصيدلية غير موجودة")
    await _ensure_in_scope(db, current_user, pharmacy.area_id, pharmacy.representative_id)

    events = await _build_timeline(db, pharmacy.name, pharmacy_id=pharmacy_id)
    debt = await _debt_summary(db, pharmacy.name)
    return CustomerActivityResponse(customer_name=pharmacy.name, debt_summary=debt, events=events)


@router.get("/doctor/{doctor_id}", response_model=CustomerActivityResponse)
async def get_doctor_activity(
    doctor_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: UserResponse = Depends(get_current_user),
):
    """Chronological timeline for one doctor (sales, returns, payments, debts, visit reports),
    with its current debt total."""
    await require_view_any(db, current_user, ("customers",))
    result = await db.execute(select(Doctors).where(Doctors.id == doctor_id))
    doctor = result.scalar_one_or_none()
    if not doctor:
        raise HTTPException(status_code=404, detail="الطبيب غير موجود")
    await _ensure_in_scope(db, current_user, doctor.area_id, doctor.representative_id)

    events = await _build_timeline(db, doctor.name, doctor_id=doctor_id)
    debt = await _debt_summary(db, doctor.name)
    return CustomerActivityResponse(customer_name=doctor.name, debt_summary=debt, events=events)
