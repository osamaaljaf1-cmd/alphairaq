"""Upcoming collection dates API - fetches debt_follow_up visits with collection_date."""
import logging
from datetime import date, datetime, timedelta
from typing import List, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import select, and_, or_, cast, Date
from sqlalchemy.ext.asyncio import AsyncSession

from core.database import get_db
from dependencies.auth import get_current_user
from models.activity_logs import Activity_logs
from schemas.auth import UserResponse

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

    query = query.order_by(Activity_logs.collection_date.asc())

    result = await db.execute(query)
    logs = result.scalars().all()

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
    """Get notification summary for current user - overdue + today's collections."""
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

    result = await db.execute(query)
    logs = result.scalars().all()

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