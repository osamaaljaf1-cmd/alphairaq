"""Editing an order while it is still in the hands of the people who can still change it.

Who may edit, and what, depends on where the order is:

    pending               the rep who made it (everything: customer, doctor, notes, the lines),
                          or whoever holds the manager stage (quantity, price, gift, bonus)
    manager_approved      whoever holds the accounting stage (quantity, price, gift, bonus)
    accounting_approved   whoever holds the printing stage  (quantity, price, gift, bonus)
                          - the last chance: what is printed is what reaches the customer
    printed and later     nobody

Everyone also needs the "edit" action on the orders page. The caller sends the status it was looking
at: if the order moved on meanwhile (approved, printed ...) nothing is changed. The lines and the
totals are written in one transaction, the totals are recomputed here from the lines, and every edit
is kept in the order_edit_log table with the lines before and after. When someone other than the rep
changes the numbers of a rep's order, the rep gets a notification in the bell (kind "order_edited").

- PUT /api/v1/order-edits/{order_id}
- GET /api/v1/order-edits/{order_id}/log
"""
import json
import logging
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from core.database import get_db
from dependencies.auth import get_current_user
from models.doctors import Doctors
from models.order_deliveries import OrderEvents
from models.order_edits import OrderEditLog
from models.order_items import Order_items
from models.pharmacies import Pharmacies
from models.products import Products
from routers.order_deliveries import _account_name, _load_order, _order_visible, ensure_tables as ensure_event_tables
from schemas.auth import UserResponse
from services.feature_permissions import require_feature
from services.permission_check import require_permission, resolve_user_role

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/order-edits", tags=["order_edits"])

# the stage an order is in -> the action permission of whoever holds it
STAGE_EDITORS = {
    "pending": "orders_manager_approval",
    "manager_approved": "orders_accounting_approval",
    "accounting_approved": "orders_printing",
}
ROLE_LABELS = {"manager": "المدير", "accounting": "الحسابات", "printing": "الطباعة", "admin": "المسؤول"}
LOCKED = "لا يمكن تعديل الطلب بعد طباعته (أو بعد رفضه أو توصيله)"
MAX_QTY = 1_000_000
MAX_MONEY = 1_000_000_000_000
MAX_NOTES = 2000

_table_ready = False


async def ensure_table(db: AsyncSession) -> None:
    """Create the log table on first use (idempotent); never touches the orders tables."""
    global _table_ready
    if _table_ready:
        return
    conn = await db.connection()
    await conn.run_sync(lambda c: OrderEditLog.__table__.create(c, checkfirst=True))
    await db.commit()
    _table_ready = True


class LineIn(BaseModel):
    id: Optional[int] = None  # an existing line (required when a stage holder edits)
    product_id: Optional[int] = None  # required for the rep's lines
    quantity: int
    unit_price: float
    gift_qty: int = 0
    bonus_amount: float = 0
    has_deal: bool = False
    agreement_id: Optional[int] = None


class EditBody(BaseModel):
    status: str  # the status the editor was looking at
    items: List[LineIn]
    # only the rep who made the order may change these
    pharmacy_id: Optional[int] = None
    doctor_id: Optional[int] = None
    notes: Optional[str] = None


def _line_dict(item) -> dict:
    return {
        "id": getattr(item, "id", None),
        "product_id": item.product_id,
        "quantity": item.quantity,
        "unit_price": item.unit_price,
        "gift_qty": item.gift_qty or 0,
        "bonus_amount": item.bonus_amount or 0,
    }


def _plain(lines: List[dict]) -> List[dict]:
    """The lines without their ids (a rep's edit writes the lines again), to see if anything changed."""
    return [{k: v for k, v in l.items() if k != "id"} for l in lines]


def _check_line(line: LineIn) -> None:
    if not (1 <= line.quantity <= MAX_QTY):
        raise HTTPException(status_code=400, detail="الكمية يجب أن تكون رقماً موجباً")
    if not (0 <= line.gift_qty <= MAX_QTY):
        raise HTTPException(status_code=400, detail="كمية الهدية غير صالحة")
    if not (0 <= line.unit_price <= MAX_MONEY):
        raise HTTPException(status_code=400, detail="السعر غير صالح")
    if not (0 <= line.bonus_amount <= MAX_MONEY):
        raise HTTPException(status_code=400, detail="البونص غير صالح")


async def _lines_of(db: AsyncSession, order_id: int) -> List[Order_items]:
    return list((await db.execute(select(Order_items).where(Order_items.order_id == order_id).order_by(Order_items.id))).scalars().all())


@router.put("/{order_id}")
async def edit_order(
    order_id: int,
    body: EditBody,
    current_user: UserResponse = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await ensure_table(db)
    await ensure_event_tables(db)
    order = await _load_order(db, order_id)
    if order.status not in STAGE_EDITORS:
        raise HTTPException(status_code=409, detail=LOCKED)
    if body.status != order.status:
        raise HTTPException(status_code=409, detail="تغيّرت حالة الطلب منذ فتحته، حدّث الصفحة وحاول مرة ثانية")

    role = await resolve_user_role(db, current_user)
    # a rep edits only their own order, and only until anybody has approved it; everyone else edits
    # the lines while the order is at their stage
    by_owner = role == "rep"
    if by_owner:
        if order.status != "pending" or order.user_id != str(current_user.id):
            raise HTTPException(status_code=403, detail="يمكنك تعديل طلبك فقط وهو معلّق قبل أي موافقة")
    else:
        await require_feature(db, current_user, STAGE_EDITORS[order.status])
        if not await _order_visible(db, current_user, order):
            raise HTTPException(status_code=403, detail="هذا الطلب خارج نطاق صلاحياتك")
    await require_permission(db, current_user, "orders", "edit")

    if not body.items:
        raise HTTPException(status_code=400, detail="الطلب يجب أن يحتوي على صنف واحد على الأقل")
    for line in body.items:
        _check_line(line)

    existing = await _lines_of(db, order.id)
    before = [_line_dict(i) for i in existing]
    header_before = {"pharmacy_id": order.pharmacy_id, "doctor_id": order.doctor_id, "notes": order.notes}

    if by_owner:
        if any(l.product_id is None for l in body.items):
            raise HTTPException(status_code=400, detail="كل سطر يحتاج صنفاً")
        product_ids = {l.product_id for l in body.items}
        found = set((await db.execute(select(Products.id).where(Products.id.in_(product_ids)))).scalars().all())
        if found != product_ids:
            raise HTTPException(status_code=400, detail="يوجد صنف غير موجود")
        if body.pharmacy_id is not None:
            if (await db.execute(select(Pharmacies.id).where(Pharmacies.id == body.pharmacy_id))).first() is None:
                raise HTTPException(status_code=400, detail="الصيدلية غير موجودة")
            order.pharmacy_id = body.pharmacy_id
        if body.doctor_id is not None:
            if (await db.execute(select(Doctors.id).where(Doctors.id == body.doctor_id))).first() is None:
                raise HTTPException(status_code=400, detail="الطبيب غير موجود")
            order.doctor_id = body.doctor_id
        if body.notes is not None:
            if len(body.notes) > MAX_NOTES:
                raise HTTPException(status_code=400, detail="الملاحظات طويلة جداً")
            order.notes = body.notes
        await db.execute(delete(Order_items).where(Order_items.order_id == order.id))
        new_lines = []
        for l in body.items:
            item = Order_items(
                user_id=order.user_id, order_id=order.id, product_id=l.product_id, quantity=l.quantity,
                unit_price=l.unit_price, gift_qty=l.gift_qty, bonus_amount=l.bonus_amount,
                has_deal=l.has_deal, agreement_id=l.agreement_id,
            )
            db.add(item)
            new_lines.append(item)
    else:
        # the holder of a stage changes quantity / price / gift / bonus of the lines that are there:
        # no line is added or removed, and the customer, doctor and notes stay
        by_id = {i.id: i for i in existing}
        sent = [l.id for l in body.items]
        if None in sent or len(set(sent)) != len(sent) or set(sent) != set(by_id):
            raise HTTPException(status_code=400, detail="أرسل كل أصناف الطلب الحالية كما هي (دون إضافة أو حذف)")
        for l in body.items:
            item = by_id[l.id]
            item.quantity = l.quantity
            item.unit_price = l.unit_price
            item.gift_qty = l.gift_qty
            item.bonus_amount = l.bonus_amount
        new_lines = existing

    total_before = order.total_amount
    order.total_amount = sum((i.quantity or 0) * (i.unit_price or 0) for i in new_lines)
    order.bonus_total = sum((i.bonus_amount or 0) for i in new_lines)
    await db.flush()

    after = [_line_dict(i) for i in new_lines]
    header_after = {"pharmacy_id": order.pharmacy_id, "doctor_id": order.doctor_id, "notes": order.notes}
    unchanged = _plain(before) == _plain(after) and header_before == header_after
    if not unchanged:
        editor_name = await _account_name(db, current_user)
        db.add(OrderEditLog(
            order_id=order.id, stage=order.status, edited_by=str(current_user.id),
            edited_by_name=editor_name, edited_by_role=role,
            total_before=total_before, total_after=order.total_amount,
            details=json.dumps({"before": before, "after": after, "header_before": header_before, "header_after": header_after}, ensure_ascii=False),
        ))
        if order.user_id and order.user_id != str(current_user.id):
            pharmacy_name = (await db.execute(select(Pharmacies.name).where(Pharmacies.id == order.pharmacy_id))).scalar_one_or_none()
            bonus_before = sum((l["bonus_amount"] or 0) for l in before)
            parts = []
            if (total_before or 0) != (order.total_amount or 0):
                parts.append(f"الإجمالي من {total_before or 0:,.0f} إلى {order.total_amount or 0:,.0f} د.ع")
            if bonus_before != (order.bonus_total or 0):
                parts.append(f"البونص من {bonus_before:,.0f} إلى {order.bonus_total or 0:,.0f} د.ع")
            what = "، ".join(parts) or "الكمية أو السعر أو الهدية"
            db.add(OrderEvents(
                target_user_id=order.user_id, kind="order_edited", order_id=order.id, pharmacy_id=order.pharmacy_id,
                pharmacy_name=pharmacy_name,
                message=f"عدّل {editor_name} ({ROLE_LABELS.get(role or '', 'المسؤول')}) الطلب #{order.id}" + (f" ({pharmacy_name})" if pharmacy_name else "") + f": {what}",
            ))
    await db.commit()
    return {
        "id": order.id, "status": order.status, "total_amount": order.total_amount, "bonus_total": order.bonus_total,
        "changed": not unchanged, "items": [_line_dict(i) for i in await _lines_of(db, order.id)],
    }


@router.get("/{order_id}/log")
async def edit_log(
    order_id: int,
    current_user: UserResponse = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await ensure_table(db)
    await require_permission(db, current_user, "orders", "view")
    order = await _load_order(db, order_id)
    if not await _order_visible(db, current_user, order):
        raise HTTPException(status_code=403, detail="هذا الطلب خارج نطاق صلاحياتك")
    rows = (await db.execute(select(OrderEditLog).where(OrderEditLog.order_id == order_id).order_by(OrderEditLog.id.desc()))).scalars().all()
    out = []
    for r in rows:
        try:
            details = json.loads(r.details)
        except ValueError:
            details = {}
        out.append({
            "id": r.id, "stage": r.stage, "edited_by_name": r.edited_by_name, "edited_by_role": r.edited_by_role,
            "total_before": r.total_before, "total_after": r.total_after,
            "created_at": r.created_at.isoformat() if r.created_at else None,
            "before": details.get("before", []), "after": details.get("after", []),
        })
    return out
