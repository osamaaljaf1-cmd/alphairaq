import json
import logging
from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import or_, and_, select, func, update
from sqlalchemy.ext.asyncio import AsyncSession

from core.database import get_db
from dependencies.auth import get_current_user
from schemas.auth import UserResponse
from models.chat_messages import Chat_messages
from models.user_presence import User_presence

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/chat", tags=["chat"])


# ---------- Schemas ----------
class SendMessageRequest(BaseModel):
    receiver_id: str
    receiver_name: str
    message_text: str


class MessageResponse(BaseModel):
    id: int
    user_id: str
    sender_name: str
    receiver_id: str
    receiver_name: Optional[str] = None
    message_text: str
    is_read: Optional[bool] = False
    created_at: Optional[str] = None

    class Config:
        from_attributes = True


class ConversationListResponse(BaseModel):
    items: list[MessageResponse]
    total: int


class PresenceResponse(BaseModel):
    user_id: str
    display_name: str
    is_online: bool
    last_active_at: Optional[str] = None

    class Config:
        from_attributes = True


class UnreadCountResponse(BaseModel):
    unread_count: int


# ---------- Routes ----------
@router.post("/send", response_model=MessageResponse, status_code=201)
async def send_message(
    data: SendMessageRequest,
    current_user: UserResponse = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Send a chat message to another user"""
    try:
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        msg = Chat_messages(
            user_id=current_user.id,
            sender_name=current_user.name or current_user.id[:8],
            receiver_id=data.receiver_id,
            receiver_name=data.receiver_name,
            message_text=data.message_text,
            is_read=False,
            created_at=now,
        )
        db.add(msg)
        await db.commit()
        await db.refresh(msg)

        return MessageResponse(
            id=msg.id,
            user_id=msg.user_id,
            sender_name=msg.sender_name,
            receiver_id=msg.receiver_id,
            receiver_name=msg.receiver_name,
            message_text=msg.message_text,
            is_read=msg.is_read,
            created_at=str(msg.created_at) if msg.created_at else now,
        )
    except Exception as e:
        await db.rollback()
        logger.error(f"Error sending message: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/conversation", response_model=ConversationListResponse)
async def get_conversation(
    peer_id: str = Query(..., description="The other user's ID"),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    current_user: UserResponse = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get conversation between current user and a peer"""
    try:
        condition = or_(
            and_(Chat_messages.user_id == current_user.id, Chat_messages.receiver_id == peer_id),
            and_(Chat_messages.user_id == peer_id, Chat_messages.receiver_id == current_user.id),
        )

        # Count
        count_q = select(func.count()).select_from(Chat_messages).where(condition)
        total_result = await db.execute(count_q)
        total = total_result.scalar() or 0

        # Fetch messages ordered by created_at
        q = (
            select(Chat_messages)
            .where(condition)
            .order_by(Chat_messages.created_at.asc())
            .offset(skip)
            .limit(limit)
        )
        result = await db.execute(q)
        messages = result.scalars().all()

        # Mark received messages as read
        mark_read = (
            update(Chat_messages)
            .where(
                and_(
                    Chat_messages.user_id == peer_id,
                    Chat_messages.receiver_id == current_user.id,
                    Chat_messages.is_read == False,
                )
            )
            .values(is_read=True)
        )
        await db.execute(mark_read)
        await db.commit()

        items = [
            MessageResponse(
                id=m.id,
                user_id=m.user_id,
                sender_name=m.sender_name,
                receiver_id=m.receiver_id,
                receiver_name=m.receiver_name,
                message_text=m.message_text,
                is_read=m.is_read,
                created_at=str(m.created_at) if m.created_at else None,
            )
            for m in messages
        ]

        return ConversationListResponse(items=items, total=total)
    except Exception as e:
        logger.error(f"Error getting conversation: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/recent-contacts", response_model=list[dict])
async def get_recent_contacts(
    current_user: UserResponse = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get list of users the current user has chatted with, with last message and unread count"""
    try:
        # Get all messages involving current user
        condition = or_(
            Chat_messages.user_id == current_user.id,
            Chat_messages.receiver_id == current_user.id,
        )
        q = select(Chat_messages).where(condition).order_by(Chat_messages.created_at.desc())
        result = await db.execute(q)
        all_msgs = result.scalars().all()

        # Group by peer
        contacts: dict = {}
        for msg in all_msgs:
            peer_id = msg.receiver_id if msg.user_id == current_user.id else msg.user_id
            peer_name = msg.receiver_name if msg.user_id == current_user.id else msg.sender_name
            if peer_id not in contacts:
                contacts[peer_id] = {
                    "peer_id": peer_id,
                    "peer_name": peer_name or peer_id[:8],
                    "last_message": msg.message_text,
                    "last_message_at": str(msg.created_at) if msg.created_at else None,
                    "unread_count": 0,
                }
            # Count unread messages from this peer
            if msg.user_id == peer_id and msg.receiver_id == current_user.id and not msg.is_read:
                contacts[peer_id]["unread_count"] += 1

        return list(contacts.values())
    except Exception as e:
        logger.error(f"Error getting recent contacts: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/unread-count", response_model=UnreadCountResponse)
async def get_unread_count(
    current_user: UserResponse = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get total unread message count for current user"""
    try:
        q = select(func.count()).select_from(Chat_messages).where(
            and_(
                Chat_messages.receiver_id == current_user.id,
                Chat_messages.is_read == False,
            )
        )
        result = await db.execute(q)
        count = result.scalar() or 0
        return UnreadCountResponse(unread_count=count)
    except Exception as e:
        logger.error(f"Error getting unread count: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


# ---------- Presence ----------
@router.post("/presence/heartbeat")
async def heartbeat(
    current_user: UserResponse = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Update user's online presence (call every 30s from frontend)"""
    try:
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        display_name = current_user.name or current_user.id[:8]

        # Check if presence record exists
        q = select(User_presence).where(User_presence.user_id == current_user.id)
        result = await db.execute(q)
        presence = result.scalar_one_or_none()

        if presence:
            presence.is_online = True
            presence.last_active_at = now
            presence.display_name = display_name
        else:
            presence = User_presence(
                user_id=current_user.id,
                display_name=display_name,
                is_online=True,
                last_active_at=now,
            )
            db.add(presence)

        await db.commit()
        return {"status": "ok"}
    except Exception as e:
        await db.rollback()
        logger.error(f"Error updating presence: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/presence/online", response_model=list[PresenceResponse])
async def get_online_users(
    current_user: UserResponse = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get all users with their online status. Users active within last 60s are considered online."""
    try:
        # Get all presence records
        q = select(User_presence).order_by(User_presence.display_name)
        result = await db.execute(q)
        all_presence = result.scalars().all()

        cutoff = datetime.now() - timedelta(seconds=60)

        items = []
        for p in all_presence:
            is_online = False
            if p.last_active_at:
                try:
                    last_active = p.last_active_at if isinstance(p.last_active_at, datetime) else datetime.strptime(str(p.last_active_at), "%Y-%m-%d %H:%M:%S")
                    is_online = last_active > cutoff
                except Exception:
                    is_online = False

            items.append(PresenceResponse(
                user_id=p.user_id,
                display_name=p.display_name or p.user_id[:8],
                is_online=is_online,
                last_active_at=str(p.last_active_at) if p.last_active_at else None,
            ))

        return items
    except Exception as e:
        logger.error(f"Error getting online users: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))