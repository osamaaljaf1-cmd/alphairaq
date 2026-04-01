import logging
from datetime import date
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from core.database import get_db
from dependencies.auth import get_current_user
from schemas.auth import UserResponse
from services.usage_tracking import UsageTrackingService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/usage-tracking", tags=["usage-tracking"])


class LoginRequest(BaseModel):
    pass


class LogoutRequest(BaseModel):
    pass


class UsageStatsRequest(BaseModel):
    start_date: Optional[str] = None
    end_date: Optional[str] = None


class UsageStatsResponse(BaseModel):
    daily_minutes: int
    weekly_minutes: int
    monthly_minutes: int
    custom_minutes: int


@router.post("/record-login")
async def record_login(
    current_user: UserResponse = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Record user login time"""
    try:
        service = UsageTrackingService(db)
        result = await service.record_login(current_user.id)
        return result
    except Exception as e:
        logger.error(f"Error recording login: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/record-logout")
async def record_logout(
    current_user: UserResponse = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Record user logout time and calculate duration"""
    try:
        service = UsageTrackingService(db)
        result = await service.record_logout(current_user.id)
        return result
    except Exception as e:
        logger.error(f"Error recording logout: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/stats", response_model=UsageStatsResponse)
async def get_usage_stats(
    data: UsageStatsRequest,
    current_user: UserResponse = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get usage statistics for the current user"""
    try:
        start_date = None
        end_date = None
        if data.start_date:
            start_date = date.fromisoformat(data.start_date)
        if data.end_date:
            end_date = date.fromisoformat(data.end_date)

        service = UsageTrackingService(db)
        result = await service.get_usage_stats(
            user_id=current_user.id,
            start_date=start_date,
            end_date=end_date,
        )
        return result
    except Exception as e:
        logger.error(f"Error getting usage stats: {e}")
        raise HTTPException(status_code=500, detail=str(e))