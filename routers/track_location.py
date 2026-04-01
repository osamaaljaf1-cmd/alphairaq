import logging
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from core.database import get_db
from services.agent_logs import Agent_logsService
from dependencies.auth import get_current_user
from schemas.auth import UserResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/track-location", tags=["track-location"])


class TrackLocationRequest(BaseModel):
    agent_id: str
    latitude: float
    longitude: float
    timestamp: str
    app_open: Optional[bool] = False
    app_close: Optional[bool] = False


class TrackLocationResponse(BaseModel):
    success: bool
    id: Optional[int] = None
    message: str = ""


@router.post("", response_model=TrackLocationResponse)
async def track_location(
    data: TrackLocationRequest,
    current_user: UserResponse = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Track agent location - inserts a new row into agent_logs"""
    logger.info(f"Track location: agent={data.agent_id}, lat={data.latitude}, lng={data.longitude}, app_open={data.app_open}, app_close={data.app_close}")

    service = Agent_logsService(db)
    try:
        # Parse timestamp
        try:
            ts = datetime.fromisoformat(data.timestamp.replace("Z", "+00:00"))
        except (ValueError, AttributeError):
            ts = datetime.now(timezone.utc)

        result = await service.create(
            {
                "agent_id": data.agent_id,
                "latitude": data.latitude,
                "longitude": data.longitude,
                "timestamp": ts,
                "app_open": data.app_open or False,
                "app_close": data.app_close or False,
            },
            user_id=str(current_user.id),
        )

        if not result:
            raise HTTPException(status_code=400, detail="Failed to create agent log")

        logger.info(f"Agent log created: id={result.id}")
        return TrackLocationResponse(success=True, id=result.id, message="Location tracked successfully")

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error tracking location: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")