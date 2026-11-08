"""The returns (credit notes) of the customers, mirrored from Phenix, as negative figures.

- GET /api/v1/debt-credits   the open credits per customer, scoped like the debts list: a rep or manager
                             only sees their own customers (and ``rep_id`` narrows to one representative)

Written only by the Phenix upload (services/debt_sync.py); nothing here changes a debt.
"""
from collections import defaultdict
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.database import get_db
from dependencies.auth import get_current_user
from models.debt_credits import CustomerCredits
from routers.debts import _names_for_request
from schemas.auth import UserResponse
from services.access import require_view_any
from services.customer_names import normalize_name
from services.debt_sync import ensure_tables

router = APIRouter(prefix="/api/v1/debt-credits", tags=["debt_credits"])


@router.get("")
async def list_credits(
    rep_id: Optional[int] = Query(None, description="Only this representative's customers (areas + assigned)"),
    db: AsyncSession = Depends(get_db),
    current_user: UserResponse = Depends(get_current_user),
):
    await require_view_any(db, current_user, ["debts"])
    await ensure_tables(db)
    scoped = await _names_for_request(db, current_user, rep_id)
    rows = (
        await db.execute(
            select(CustomerCredits).where(CustomerCredits.status == "open", CustomerCredits.remaining_amount < 0).order_by(CustomerCredits.invoice_date.desc(), CustomerCredits.id.desc())
        )
    ).scalars().all()
    by_customer = defaultdict(list)
    for c in rows:
        if scoped is not None and normalize_name(c.customer_name) not in scoped:
            continue
        by_customer[c.customer_name].append(c)
    return [
        {
            "customer_name": name,
            "total": round(sum(c.remaining_amount for c in items), 2),
            "count": len(items),
            "items": [
                {"invoice_number": c.invoice_number, "amount": c.amount, "remaining": c.remaining_amount, "invoice_date": c.invoice_date}
                for c in items
            ],
        }
        for name, items in sorted(by_customer.items())
    ]
