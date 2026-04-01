import logging
from datetime import datetime, date, timedelta, timezone
from typing import Optional, Dict, Any
from sqlalchemy import select, func, update, and_, cast, Date
from sqlalchemy.ext.asyncio import AsyncSession
from models.user_usage import User_usage

logger = logging.getLogger(__name__)


class UsageTrackingService:
    """Service for tracking user login/logout usage"""

    def __init__(self, db: AsyncSession):
        self.db = db

    async def record_login(self, user_id: str) -> Optional[Dict[str, Any]]:
        """Record a new login event"""
        now = datetime.now(timezone.utc)
        record = User_usage(
            user_id=user_id,
            login_time=now,
            date=now.date(),
        )
        self.db.add(record)
        await self.db.commit()
        await self.db.refresh(record)
        return {
            "id": record.id,
            "user_id": record.user_id,
            "login_time": str(record.login_time),
            "date": str(record.date),
        }

    async def record_logout(self, user_id: str) -> Optional[Dict[str, Any]]:
        """Update the latest open session with logout time and duration"""
        # Find the latest record where logout_time is null
        stmt = (
            select(User_usage)
            .where(
                and_(
                    User_usage.user_id == user_id,
                    User_usage.logout_time.is_(None),
                )
            )
            .order_by(User_usage.login_time.desc())
            .limit(1)
        )
        result = await self.db.execute(stmt)
        record = result.scalar_one_or_none()

        if not record:
            return {"message": "No open session found"}

        now = datetime.now(timezone.utc)
        # Ensure login_time is also timezone-aware for subtraction
        login_time = record.login_time
        if login_time.tzinfo is None:
            login_time = login_time.replace(tzinfo=timezone.utc)
        duration = int((now - login_time).total_seconds() / 60)

        record.logout_time = now
        record.duration_minutes = max(duration, 1)  # At least 1 minute

        await self.db.commit()
        await self.db.refresh(record)
        return {
            "id": record.id,
            "user_id": record.user_id,
            "login_time": str(record.login_time),
            "logout_time": str(record.logout_time),
            "duration_minutes": record.duration_minutes,
        }

    async def get_usage_stats(
        self,
        user_id: str,
        start_date: Optional[date] = None,
        end_date: Optional[date] = None,
    ) -> Dict[str, Any]:
        """Get usage statistics for a user within a date range"""
        today = date.today()

        # Daily usage
        daily_stmt = select(
            func.coalesce(func.sum(User_usage.duration_minutes), 0)
        ).where(
            and_(
                User_usage.user_id == user_id,
                cast(User_usage.date, Date) == today,
            )
        )
        daily_result = await self.db.execute(daily_stmt)
        daily_minutes = daily_result.scalar() or 0

        # Weekly usage (last 7 days)
        week_ago = today - timedelta(days=7)
        weekly_stmt = select(
            func.coalesce(func.sum(User_usage.duration_minutes), 0)
        ).where(
            and_(
                User_usage.user_id == user_id,
                cast(User_usage.date, Date) >= week_ago,
            )
        )
        weekly_result = await self.db.execute(weekly_stmt)
        weekly_minutes = weekly_result.scalar() or 0

        # Monthly usage (from start of current month)
        month_start = today.replace(day=1)
        monthly_stmt = select(
            func.coalesce(func.sum(User_usage.duration_minutes), 0)
        ).where(
            and_(
                User_usage.user_id == user_id,
                cast(User_usage.date, Date) >= month_start,
            )
        )
        monthly_result = await self.db.execute(monthly_stmt)
        monthly_minutes = monthly_result.scalar() or 0

        # Custom range
        custom_minutes = 0
        if start_date and end_date:
            custom_stmt = select(
                func.coalesce(func.sum(User_usage.duration_minutes), 0)
            ).where(
                and_(
                    User_usage.user_id == user_id,
                    cast(User_usage.date, Date) >= start_date,
                    cast(User_usage.date, Date) <= end_date,
                )
            )
            custom_result = await self.db.execute(custom_stmt)
            custom_minutes = custom_result.scalar() or 0

        return {
            "daily_minutes": int(daily_minutes),
            "weekly_minutes": int(weekly_minutes),
            "monthly_minutes": int(monthly_minutes),
            "custom_minutes": int(custom_minutes),
        }