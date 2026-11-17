"""Placing a new order in one request, at most once.

The app used to create the order and then each line with separate requests, and the confirm button
stayed active meanwhile: a second tap (or a retry on a slow phone network) made a second order, and a
connection lost half-way left an order without some of its lines.

POST /api/v1/order-submit saves the order and all its lines in one transaction. The phone sends a key
made for this order (client_request_id); the same key again returns the order already made instead of
a new one. An identical order (same customer, lines and total) from the same person in the last two
minutes under another key is answered 409 unless the person confirms it is really a second order.
"""
import logging
from datetime import datetime, timedelta, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from core.database import get_db
from dependencies.auth import get_current_user
from models.order_items import Order_items
from models.order_submissions import Order_submissions
from models.orders import Orders
from schemas.auth import UserResponse
from services.permission_check import require_permission

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/order-submit", tags=["order_submit"])

DUPLICATE_WINDOW = timedelta(minutes=2)


class SubmitItem(BaseModel):
    product_id: int
    quantity: int = Field(gt=0)
    unit_price: float = Field(ge=0)
    gift_qty: Optional[int] = Field(default=0, ge=0)
    bonus_amount: Optional[float] = 0
    has_deal: Optional[bool] = False
    agreement_id: Optional[int] = None


class SubmitOrder(BaseModel):
    client_request_id: str = Field(min_length=8, max_length=80)
    pharmacy_id: int
    doctor_id: Optional[int] = None
    representative_id: Optional[int] = None
    total_amount: float = Field(ge=0)
    bonus_total: Optional[float] = 0
    notes: Optional[str] = None
    items: List[SubmitItem] = Field(min_length=1, max_length=300)
    # the person was told an identical order was just made and still wants this one
    confirm_duplicate: bool = False


def _fingerprint(pharmacy_id: int, total: float, items) -> tuple:
    lines = sorted((int(i.product_id), int(i.quantity), round(float(i.unit_price or 0), 2), int(i.gift_qty or 0)) for i in items)
    return (int(pharmacy_id), round(float(total or 0), 2), tuple(lines))


async def _existing(db: AsyncSession, key: str, user_id: str) -> Optional[int]:
    row = (
        await db.execute(select(Order_submissions.order_id).where(Order_submissions.client_request_id == key, Order_submissions.user_id == user_id))
    ).first()
    return row[0] if row else None


@router.post("")
async def submit_order(
    data: SubmitOrder,
    db: AsyncSession = Depends(get_db),
    current_user: UserResponse = Depends(get_current_user),
):
    await require_permission(db, current_user, "orders", "add")
    me = str(current_user.id)
    key = data.client_request_id.strip()

    # 1. the same key again (a double tap, a retry): the order that was already made
    done = await _existing(db, key, me)
    if done is not None:
        return {"order_id": done, "duplicate": True}

    # 2. an identical order from the same person moments ago, under another key
    if not data.confirm_duplicate:
        since = datetime.now(timezone.utc) - DUPLICATE_WINDOW
        recent = (
            await db.execute(
                select(Orders).where(Orders.user_id == me, Orders.pharmacy_id == data.pharmacy_id, Orders.created_at >= since).order_by(Orders.id.desc()).limit(5)
            )
        ).scalars().all()
        wanted = _fingerprint(data.pharmacy_id, data.total_amount, data.items)
        for o in recent:
            lines = (await db.execute(select(Order_items).where(Order_items.order_id == o.id))).scalars().all()
            if lines and _fingerprint(o.pharmacy_id, o.total_amount, lines) == wanted:
                raise HTTPException(
                    status_code=409,
                    detail={
                        "code": "duplicate_order",
                        "order_id": o.id,
                        "message": f"طلبية مطابقة لهذه الصيدلية سُجّلت قبل لحظات (الطلب #{o.id})",
                    },
                )

    # 3. the order and all its lines together
    now = datetime.now(timezone.utc)
    try:
        order = Orders(
            user_id=me,
            representative_id=data.representative_id,
            pharmacy_id=data.pharmacy_id,
            doctor_id=data.doctor_id,
            status="pending",
            total_amount=data.total_amount,
            bonus_total=data.bonus_total or 0,
            notes=(data.notes or "").strip() or None,
            created_at=now,
        )
        db.add(order)
        await db.flush()
        for it in data.items:
            db.add(
                Order_items(
                    user_id=me,
                    order_id=order.id,
                    product_id=it.product_id,
                    quantity=it.quantity,
                    unit_price=it.unit_price,
                    gift_qty=it.gift_qty or 0,
                    bonus_amount=it.bonus_amount or 0,
                    has_deal=bool(it.has_deal),
                    agreement_id=it.agreement_id,
                )
            )
        db.add(Order_submissions(client_request_id=key, user_id=me, order_id=order.id))
        await db.commit()
    except IntegrityError:
        # the same key arrived twice at the same moment: the other request made the order
        await db.rollback()
        done = await _existing(db, key, me)
        if done is not None:
            return {"order_id": done, "duplicate": True}
        raise
    except Exception:
        await db.rollback()
        logger.error("Error submitting order", exc_info=True)
        raise HTTPException(status_code=500, detail="تعذر حفظ الطلب")
    logger.info("Order %s submitted with %s lines", order.id, len(data.items))
    return {"order_id": order.id, "duplicate": False}
