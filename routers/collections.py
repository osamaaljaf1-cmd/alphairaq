"""Upcoming collection dates API - fetches debt_follow_up visits with collection_date."""
import logging
from datetime import date, datetime, timedelta
from typing import List, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import select, and_, or_, cast, Date, func, false
from sqlalchemy.ext.asyncio import AsyncSession

from core.database import get_db
from dependencies.auth import get_current_user
from models.activity_logs import Activity_logs
from models.payments import Payments
from schemas.auth import UserResponse
from services.area_scope import get_customer_scope, get_scoped_pharmacy_names

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/collections", tags=["collections"])


class CollectionItem(BaseModel):
    id: int
    user_id: str
    pharmacy_id: Optional[int] = None
    pharmacy_name: Optional[str] = None
    collection_date: str
    details: Optional[str] = None
    timestamp: Optional[str] = None
    status: str  # overdue, today, upcoming

    class Config:
        from_attributes = True


class CollectionsResponse(BaseModel):
    items: List[CollectionItem]
    overdue_count: int
    today_count: int
    upcoming_count: int
    total: int


async def _drop_paid_appointments(db: AsyncSession, logs):
    """A collection appointment is resolved once the customer pays: drop any
    appointment for which a non-cancelled payment was recorded for the same
    customer AFTER the appointment was logged. Non-destructive — the visit
    record stays, it just stops counting as an outstanding/overdue
    appointment (and comes back if that payment is later cancelled)."""
    if not logs:
        return logs
    earliest = min((l.timestamp for l in logs if l.timestamp), default=None)
    query = select(Payments.customer_name, Payments.created_at).where(
        or_(Payments.status.is_(None), Payments.status != "canceled")
    )
    if earliest is not None:
        query = query.where(Payments.created_at >= earliest)
    rows = (await db.execute(query)).all()

    last_paid = {}
    for name, created in rows:
        if not name or created is None:
            continue
        key = name.strip().lower()
        if key not in last_paid or created > last_paid[key]:
            last_paid[key] = created

    kept = []
    for log in logs:
        key = (log.pharmacy_name or "").strip().lower()
        paid_at = last_paid.get(key)
        if paid_at is not None and log.timestamp is not None and paid_at > log.timestamp:
            continue
        kept.append(log)
    return kept


@router.get("/upcoming", response_model=CollectionsResponse)
async def get_upcoming_collections(
    days_ahead: int = Query(7, ge=1, le=30),
    user_id: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
    current_user: UserResponse = Depends(get_current_user),
):
    """Get upcoming collection dates from pharmacy visits with debt_follow_up type.
    Returns overdue, today, and upcoming collections within days_ahead days.
    """
    today = date.today()
    end_date = today + timedelta(days=days_ahead)

    # Build query - get all debt_follow_up visits with collection_date
    query = select(Activity_logs).where(
        and_(
            Activity_logs.visit_type == "debt_follow_up",
            Activity_logs.collection_date.isnot(None),
            # Get overdue (past) + upcoming within range
            Activity_logs.collection_date <= end_date,
        )
    )

    # Filter by user_id if provided
    if user_id:
        query = query.where(Activity_logs.user_id == user_id)

    # Restrict to the caller's own customers when they're a rep or manager
    # (matches the same area/rep scoping used for debts and customer lists)
    # — admin/accounting and other roles see everything, unrestricted.
    scope = await get_customer_scope(db, current_user)
    scoped_names = await get_scoped_pharmacy_names(db, scope)
    if scoped_names is not None:
        if not scoped_names:
            query = query.where(false())
        else:
            query = query.where(func.lower(func.trim(Activity_logs.pharmacy_name)).in_(scoped_names))

    query = query.order_by(Activity_logs.collection_date.asc())

    result = await db.execute(query)
    logs = result.scalars().all()
    logs = await _drop_paid_appointments(db, logs)

    items: List[CollectionItem] = []
    overdue_count = 0
    today_count = 0
    upcoming_count = 0

    for log in logs:
        col_date = log.collection_date
        if col_date is None:
            continue

        # Determine status
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
                pharmacy_id=log.pharmacy_id,
                pharmacy_name=log.pharmacy_name,
                collection_date=col_date.isoformat(),
                details=log.details,
                timestamp=log.timestamp.isoformat() if log.timestamp else None,
                status=status,
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

    Restricted to the caller's own customers when they're a rep or manager
    (same scoping as /upcoming) — admin/accounting and other roles see
    everything, unrestricted."""
    today = date.today()
    tomorrow = today + timedelta(days=1)

    # Get overdue and today's collections
    query = select(Activity_logs).where(
        and_(
            Activity_logs.visit_type == "debt_follow_up",
            Activity_logs.collection_date.isnot(None),
            Activity_logs.collection_date <= tomorrow,
        )
    )

    scope = await get_customer_scope(db, current_user)
    scoped_names = await get_scoped_pharmacy_names(db, scope)
    if scoped_names is not None:
        if not scoped_names:
            query = query.where(false())
        else:
            query = query.where(func.lower(func.trim(Activity_logs.pharmacy_name)).in_(scoped_names))

    result = await db.execute(query)
    logs = result.scalars().all()
    logs = await _drop_paid_appointments(db, logs)

    notifications = []
    for log in logs:
        col_date = log.collection_date
        if col_date is None:
            continue

        if col_date < today:
            notif_type = "overdue"
            message = f"موعد استحصال متأخر: {log.pharmacy_name or 'صيدلية'}"
        elif col_date == today:
            notif_type = "today"
            message = f"موعد استحصال اليوم: {log.pharmacy_name or 'صيدلية'}"
        else:
            notif_type = "tomorrow"
            message = f"موعد استحصال غداً: {log.pharmacy_name or 'صيدلية'}"

        notifications.append({
            "id": log.id,
            "type": notif_type,
            "message": message,
            "pharmacy_name": log.pharmacy_name,
            "pharmacy_id": log.pharmacy_id,
            "collection_date": col_date.isoformat(),
            "details": log.details,
        })

    return {
        "notifications": notifications,
        "overdue_count": sum(1 for n in notifications if n["type"] == "overdue"),
        "today_count": sum(1 for n in notifications if n["type"] == "today"),
        "tomorrow_count": sum(1 for n in notifications if n["type"] == "tomorrow"),
        "total": len(notifications),
    }