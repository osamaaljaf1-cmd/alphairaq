import logging
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text
from core.database import get_db
from dependencies.auth import get_current_user
from schemas.auth import UserResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/messages", tags=["messages"])


class MessageCreate(BaseModel):
    doctor_id: int
    pharmacy_id: int | None = None
    product_id: int | None = None
    return_id: int | None = None
    message_type: str = "whatsapp"
    message_content: str
    doctor_phone: str | None = None
    status: str = "sent"


class MessageResponse(BaseModel):
    id: int
    user_id: str
    doctor_id: int
    pharmacy_id: int | None = None
    product_id: int | None = None
    return_id: int | None = None
    message_type: str
    message_content: str
    doctor_phone: str | None = None
    status: str | None = None
    created_at: datetime | None = None


@router.post("/create", response_model=MessageResponse)
async def create_message(
    data: MessageCreate,
    current_user: UserResponse = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Create a new message record"""
    try:
        result = await db.execute(
            text("""
                INSERT INTO messages (user_id, doctor_id, pharmacy_id, product_id, return_id,
                    message_type, message_content, doctor_phone, status, created_at)
                VALUES (:user_id, :doctor_id, :pharmacy_id, :product_id, :return_id,
                    :message_type, :message_content, :doctor_phone, :status, :created_at)
                RETURNING id, user_id, doctor_id, pharmacy_id, product_id, return_id,
                    message_type, message_content, doctor_phone, status, created_at
            """),
            {
                "user_id": current_user.id,
                "doctor_id": data.doctor_id,
                "pharmacy_id": data.pharmacy_id,
                "product_id": data.product_id,
                "return_id": data.return_id,
                "message_type": data.message_type,
                "message_content": data.message_content,
                "doctor_phone": data.doctor_phone,
                "status": data.status,
                "created_at": datetime.now(timezone.utc),
            },
        )
        await db.commit()
        row = result.fetchone()
        return MessageResponse(
            id=row.id,
            user_id=row.user_id,
            doctor_id=row.doctor_id,
            pharmacy_id=row.pharmacy_id,
            product_id=row.product_id,
            return_id=row.return_id,
            message_type=row.message_type,
            message_content=row.message_content,
            doctor_phone=row.doctor_phone,
            status=row.status,
            created_at=row.created_at,
        )
    except Exception as e:
        await db.rollback()
        logger.error(f"Error creating message: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/list")
async def list_messages(
    current_user: UserResponse = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List all messages for the current user"""
    try:
        result = await db.execute(
            text("""
                SELECT id, user_id, doctor_id, pharmacy_id, product_id, return_id,
                    message_type, message_content, doctor_phone, status, created_at
                FROM messages
                WHERE user_id = :user_id
                ORDER BY created_at DESC
                LIMIT 200
            """),
            {"user_id": current_user.id},
        )
        rows = result.fetchall()
        return {
            "items": [
                {
                    "id": r.id,
                    "user_id": r.user_id,
                    "doctor_id": r.doctor_id,
                    "pharmacy_id": r.pharmacy_id,
                    "product_id": r.product_id,
                    "return_id": r.return_id,
                    "message_type": r.message_type,
                    "message_content": r.message_content,
                    "doctor_phone": r.doctor_phone,
                    "status": r.status,
                    "created_at": r.created_at.isoformat() if r.created_at else None,
                }
                for r in rows
            ]
        }
    except Exception as e:
        logger.error(f"Error listing messages: {e}")
        raise HTTPException(status_code=500, detail=str(e))