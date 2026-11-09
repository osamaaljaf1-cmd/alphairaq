"""One agreement screen for a doctor: several pharmacies and several items at once.

- POST /api/v1/agreements-bulk   a doctor, one or more pharmacies, one or more items (each with its agreed
                                 price and bonus) -> one agreement per pharmacy and item. Pairs that already
                                 have an active agreement (with this or another doctor) are skipped and listed
                                 with the reason; the rest are created together.

Needs "add" on the agreements page. At least one pharmacy and one item are required: an agreement is
always about a doctor, the pharmacies where it applies and the items it covers.
"""
import logging
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.database import get_db
from dependencies.auth import get_current_user
from models.agreements import Agreements
from models.doctors import Doctors
from models.pharmacies import Pharmacies
from models.products import Products
from schemas.auth import UserResponse
from services.permission_check import require_permission

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/agreements-bulk", tags=["agreements"])

MAX_COMBINATIONS = 2000


class BulkItem(BaseModel):
    product_id: int
    agreed_price: float = Field(..., ge=0)
    bonus_value: float = Field(0, ge=0)
    bonus_type: str = "fixed"  # fixed (an amount per unit) | percentage (of the line total)
    bonus_qty_threshold: Optional[int] = Field(None, ge=1)
    bonus_qty: Optional[int] = Field(None, ge=1)


class BulkBody(BaseModel):
    doctor_id: int
    pharmacy_ids: List[int]
    items: List[BulkItem]
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    notes: Optional[str] = None
    # the same for every agreement of the request (the "agreement details" of the form)
    agreed_quantity: Optional[float] = Field(None, ge=0)
    agreed_amount: Optional[float] = Field(None, ge=0)
    agreed_bonus: Optional[float] = Field(None, ge=0)


@router.post("")
async def create_bulk(
    data: BulkBody,
    db: AsyncSession = Depends(get_db),
    current_user: UserResponse = Depends(get_current_user),
):
    await require_permission(db, current_user, "agreements", "add")
    pharmacy_ids = list(dict.fromkeys(data.pharmacy_ids))
    items = {}
    for it in data.items:
        items.setdefault(it.product_id, it)  # one line per item: the first one wins
    if not pharmacy_ids:
        raise HTTPException(status_code=400, detail="اختر صيدلية واحدة على الأقل")
    if not items:
        raise HTTPException(status_code=400, detail="اختر صنفاً واحداً على الأقل")
    if len(pharmacy_ids) * len(items) > MAX_COMBINATIONS:
        raise HTTPException(status_code=400, detail=f"العدد كبير: الحد الأقصى {MAX_COMBINATIONS} اتفاقية في المرة الواحدة")
    for it in items.values():
        if it.bonus_type not in ("fixed", "percentage"):
            raise HTTPException(status_code=400, detail="نوع البونص غير صالح")

    doctor = (await db.execute(select(Doctors).where(Doctors.id == data.doctor_id))).scalar_one_or_none()
    if doctor is None:
        raise HTTPException(status_code=400, detail="الطبيب غير موجود")
    pharmacies = {p.id: p.name for p in (await db.execute(select(Pharmacies).where(Pharmacies.id.in_(pharmacy_ids)))).scalars().all()}
    products = {p.id: p.name for p in (await db.execute(select(Products).where(Products.id.in_(list(items))))).scalars().all()}
    if len(pharmacies) != len(pharmacy_ids):
        raise HTTPException(status_code=400, detail="توجد صيدلية غير موجودة")
    if len(products) != len(items):
        raise HTTPException(status_code=400, detail="يوجد صنف غير موجود")

    active = (
        await db.execute(
            select(Agreements).where(
                Agreements.status == "active", Agreements.pharmacy_id.in_(pharmacy_ids), Agreements.product_id.in_(list(items))
            )
        )
    ).scalars().all()
    taken = {(a.pharmacy_id, a.product_id): a for a in active}
    doctor_names = {
        d.id: d.name
        for d in (await db.execute(select(Doctors).where(Doctors.id.in_({a.doctor_id for a in active} or {0})))).scalars().all()
    }

    created, skipped = 0, []
    for pid in pharmacy_ids:
        for product_id, it in items.items():
            other = taken.get((pid, product_id))
            if other is not None:
                same = other.doctor_id == data.doctor_id
                skipped.append(
                    {
                        "pharmacy_id": pid,
                        "pharmacy_name": pharmacies[pid],
                        "product_id": product_id,
                        "product_name": products[product_id],
                        "reason": "اتفاق فعّال موجود مسبقاً لهذا الطبيب" if same else f"اتفاق فعّال مع الطبيب «{doctor_names.get(other.doctor_id, other.doctor_id)}»",
                    }
                )
                continue
            db.add(
                Agreements(
                    doctor_id=data.doctor_id,
                    pharmacy_id=pid,
                    product_id=product_id,
                    agreed_price=it.agreed_price,
                    bonus_value=it.bonus_value,
                    bonus_type=it.bonus_type,
                    bonus_qty_threshold=it.bonus_qty_threshold,
                    bonus_qty=it.bonus_qty,
                    agreed_quantity=data.agreed_quantity,
                    agreed_amount=data.agreed_amount,
                    agreed_bonus=data.agreed_bonus,
                    start_date=data.start_date or None,
                    end_date=data.end_date or None,
                    status="active",
                    notes=(data.notes or "").strip() or None,
                )
            )
            created += 1
    await db.commit()
    logger.info(f"agreements-bulk: doctor {data.doctor_id}: {created} created, {len(skipped)} skipped")
    return {"created": created, "skipped": skipped, "doctor_name": doctor.name}
