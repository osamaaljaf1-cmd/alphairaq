"""QR link on the payment receipt.

Two routes:
- POST /api/v1/debts/payments/{id}/receipt-link  (logged in) creates / returns the
  random token of a payment's receipt and records which print template it uses.
- GET  /api/v1/public/receipts/{token}           (public, read-only) returns what is
  printed on the receipt — customer, invoices, amounts, the customer-facing note —
  and nothing internal: no internal note, no representative, no ids besides the
  receipt number, no customer balance, phone or address.
"""
import logging
import re
import secrets
import time
from collections import deque
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.database import get_db
from dependencies.auth import get_current_user
from models.debts import Debts
from models.payment_details import PaymentDetails
from models.payment_receipt_links import PaymentReceiptLinks
from models.payment_receipt_notes import PaymentReceiptNotes
from models.payments import Payments
from schemas.auth import UserResponse
from services.area_scope import get_customer_scope, get_scoped_pharmacy_names
from services.customer_names import normalize_name

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", tags=["receipt_links"])

_TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{16,64}$")
_TEMPLATE_RE = re.compile(r"^[a-z0-9_-]{1,32}$")

# Public endpoint: at most this many requests per client address per window
_RATE_LIMIT = 40
_RATE_WINDOW_SECONDS = 60
_hits: Dict[str, deque] = {}


def _client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _check_rate_limit(request: Request) -> None:
    now = time.monotonic()
    ip = _client_ip(request)
    window = _hits.setdefault(ip, deque())
    while window and now - window[0] > _RATE_WINDOW_SECONDS:
        window.popleft()
    if len(window) >= _RATE_LIMIT:
        raise HTTPException(status_code=429, detail="طلبات كثيرة، حاول بعد قليل")
    window.append(now)
    if len(_hits) > 5000:  # keep the table bounded
        stale = [k for k, v in _hits.items() if not v or now - v[-1] > _RATE_WINDOW_SECONDS]
        for key in stale[:2500]:
            _hits.pop(key, None)


_links_table_ready = False


async def _ensure_links_table(db: AsyncSession) -> None:
    """Create the links table on first use (idempotent); never touches payments."""
    global _links_table_ready
    if _links_table_ready:
        return
    conn = await db.connection()
    await conn.run_sync(lambda c: PaymentReceiptLinks.__table__.create(c, checkfirst=True))
    await db.commit()
    _links_table_ready = True


class ReceiptLinkRequest(BaseModel):
    template_id: Optional[str] = None


class ReceiptLinkResponse(BaseModel):
    token: str


class PublicReceiptItem(BaseModel):
    invoice_number: str
    original_amount: float
    amount_applied: float


class PublicReceipt(BaseModel):
    receipt_number: str
    template_id: Optional[str] = None
    customer_name: str
    date: Optional[str] = None
    items: List[PublicReceiptItem]
    total_selected: float
    discount: float
    returns_amount: float
    net_received: float
    receipt_notes: Optional[str] = None
    canceled: bool = False


@router.post("/debts/payments/{payment_id}/receipt-link", response_model=ReceiptLinkResponse)
async def create_receipt_link(
    payment_id: int,
    data: ReceiptLinkRequest,
    db: AsyncSession = Depends(get_db),
    current_user: UserResponse = Depends(get_current_user),
):
    """The receipt's public token (created on first call). The caller must be
    allowed to see the payment's customer, like everywhere else for reps/managers."""
    payment = (await db.execute(select(Payments).where(Payments.id == payment_id))).scalar_one_or_none()
    if not payment:
        raise HTTPException(status_code=404, detail="الدفعة غير موجودة")

    scope = await get_customer_scope(db, current_user)
    names = await get_scoped_pharmacy_names(db, scope)
    if names is not None and normalize_name(payment.customer_name) not in names:
        raise HTTPException(status_code=403, detail="هذا الزبون خارج نطاق صلاحياتك")

    template_id = data.template_id if data.template_id and _TEMPLATE_RE.match(data.template_id) else None
    await _ensure_links_table(db)
    link = (await db.execute(select(PaymentReceiptLinks).where(PaymentReceiptLinks.payment_id == payment_id))).scalar_one_or_none()
    if link is None:
        link = PaymentReceiptLinks(token=secrets.token_urlsafe(16), payment_id=payment_id, template_id=template_id)
        db.add(link)
    elif template_id and link.template_id != template_id:
        link.template_id = template_id
    await db.commit()
    return ReceiptLinkResponse(token=link.token)


@router.get("/public/receipts/{token}", response_model=PublicReceipt)
async def public_receipt(
    token: str,
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db),
):
    """Public, read-only receipt. Unknown or malformed tokens all answer the same 404."""
    _check_rate_limit(request)
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Robots-Tag"] = "noindex, nofollow"

    not_found = HTTPException(status_code=404, detail="الوصل غير موجود")
    if not _TOKEN_RE.match(token):
        raise not_found
    await _ensure_links_table(db)
    link = (await db.execute(select(PaymentReceiptLinks).where(PaymentReceiptLinks.token == token))).scalar_one_or_none()
    if link is None:
        raise not_found
    payment = (await db.execute(select(Payments).where(Payments.id == link.payment_id))).scalar_one_or_none()
    if payment is None:
        raise not_found

    rows = await db.execute(
        select(Debts.invoice_number, Debts.amount, PaymentDetails.amount_applied)
        .join(PaymentDetails, PaymentDetails.debt_id == Debts.id)
        .where(PaymentDetails.payment_id == payment.id)
        .order_by(PaymentDetails.id)
    )
    items = [
        PublicReceiptItem(invoice_number=str(inv), original_amount=amt, amount_applied=applied)
        for inv, amt, applied in rows.all()
    ]

    # The note for the customer lives in its own table, which may not exist yet
    from routers.debts import _ensure_receipt_notes_table

    await _ensure_receipt_notes_table(db)
    note = (
        await db.execute(select(PaymentReceiptNotes.note).where(PaymentReceiptNotes.payment_id == payment.id))
    ).scalar_one_or_none()

    return PublicReceipt(
        receipt_number=f"PAY-{payment.id}",
        template_id=link.template_id,
        customer_name=payment.customer_name,
        date=payment.created_at.isoformat() if payment.created_at else None,
        items=items,
        total_selected=payment.total_selected,
        discount=payment.discount or 0,
        returns_amount=payment.returns_amount or 0,
        net_received=payment.net_received,
        receipt_notes=note,
        canceled=(payment.status == "canceled"),
    )
