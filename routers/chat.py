import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import or_, and_, select, func, update, delete
from sqlalchemy.ext.asyncio import AsyncSession

from core.database import get_db
from dependencies.auth import get_current_user
from schemas.auth import UserResponse
from models.chat_messages import Chat_messages
from models.user_presence import User_presence
from models.chat_groups import Chat_groups
from models.chat_group_members import Chat_group_members

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


TEAM_CHANNEL_ID = "team"
GROUP_PREFIX = "group:"


async def _is_group_member(db: AsyncSession, group_id: int, user_id: str) -> bool:
    q = select(Chat_group_members).where(
        and_(
            Chat_group_members.group_id == group_id,
            Chat_group_members.user_id == user_id,
        )
    )
    result = await db.execute(q)
    return result.scalar_one_or_none() is not None


# ---------- Group schemas ----------
class CreateGroupRequest(BaseModel):
    name: str
    member_ids: list[str] = []
    member_names: dict[str, str] = {}


class GroupMemberResponse(BaseModel):
    user_id: str
    user_name: Optional[str] = None


class GroupResponse(BaseModel):
    id: int
    name: str
    created_by: str
    created_by_name: Optional[str] = None
    created_at: Optional[str] = None
    member_count: int
    members: list[GroupMemberResponse] = []

    class Config:
        from_attributes = True


class AddMemberRequest(BaseModel):
    user_id: str
    user_name: Optional[str] = None


# ---------- Routes ----------
@router.post("/send", response_model=MessageResponse, status_code=201)
async def send_message(
    data: SendMessageRequest,
    current_user: UserResponse = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Send a chat message to another user, the team channel, or a group.
    Group sends are rejected for non-members."""
    if data.receiver_id.startswith(GROUP_PREFIX):
        group_id_str = data.receiver_id[len(GROUP_PREFIX):]
        if not group_id_str.isdigit() or not await _is_group_member(db, int(group_id_str), current_user.id):
            raise HTTPException(status_code=403, detail="لست عضواً في هذه المجموعة")

    try:
        now_dt = datetime.now(timezone.utc)
        msg = Chat_messages(
            user_id=current_user.id,
            sender_name=current_user.name or current_user.id[:8],
            receiver_id=data.receiver_id,
            receiver_name=data.receiver_name,
            message_text=data.message_text,
            is_read=False,
            created_at=now_dt,
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
            created_at=str(msg.created_at) if msg.created_at else str(now_dt),
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


# ---------- Groups ----------
@router.post("/groups", response_model=GroupResponse, status_code=201)
async def create_group(
    data: CreateGroupRequest,
    current_user: UserResponse = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Create a private group. The creator is always a member; any
    member_ids given are added alongside them. Only members can ever see
    the group or its messages."""
    if not data.name.strip():
        raise HTTPException(status_code=400, detail="اسم المجموعة مطلوب")
    try:
        group = Chat_groups(
            name=data.name.strip(),
            created_by=current_user.id,
            created_by_name=current_user.name or current_user.id[:8],
        )
        db.add(group)
        await db.flush()

        member_ids = {current_user.id, *data.member_ids}
        for uid in member_ids:
            db.add(Chat_group_members(
                group_id=group.id,
                user_id=uid,
                user_name=current_user.name if uid == current_user.id else data.member_names.get(uid, uid[:8]),
            ))

        await db.commit()
        await db.refresh(group)

        return GroupResponse(
            id=group.id,
            name=group.name,
            created_by=group.created_by,
            created_by_name=group.created_by_name,
            created_at=str(group.created_at) if group.created_at else None,
            member_count=len(member_ids),
            members=[],
        )
    except HTTPException:
        raise
    except Exception as e:
        await db.rollback()
        logger.error(f"Error creating group: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/groups", response_model=list[GroupResponse])
async def list_my_groups(
    current_user: UserResponse = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List every group the current user is a member of."""
    try:
        my_group_ids_q = select(Chat_group_members.group_id).where(
            Chat_group_members.user_id == current_user.id
        )
        result = await db.execute(my_group_ids_q)
        group_ids = [r[0] for r in result.all()]
        if not group_ids:
            return []

        groups_q = select(Chat_groups).where(Chat_groups.id.in_(group_ids))
        groups_result = await db.execute(groups_q)
        groups = groups_result.scalars().all()

        members_q = select(Chat_group_members).where(Chat_group_members.group_id.in_(group_ids))
        members_result = await db.execute(members_q)
        all_members = members_result.scalars().all()
        members_by_group: dict[int, list] = {}
        for m in all_members:
            members_by_group.setdefault(m.group_id, []).append(
                GroupMemberResponse(user_id=m.user_id, user_name=m.user_name)
            )

        return [
            GroupResponse(
                id=g.id,
                name=g.name,
                created_by=g.created_by,
                created_by_name=g.created_by_name,
                created_at=str(g.created_at) if g.created_at else None,
                member_count=len(members_by_group.get(g.id, [])),
                members=members_by_group.get(g.id, []),
            )
            for g in groups
        ]
    except Exception as e:
        logger.error(f"Error listing groups: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/groups/{group_id}/messages", response_model=ConversationListResponse)
async def get_group_messages(
    group_id: int,
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=200),
    current_user: UserResponse = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get a group's messages. 403s if the current user isn't a member —
    non-members can never read a group's messages."""
    if not await _is_group_member(db, group_id, current_user.id):
        raise HTTPException(status_code=403, detail="لست عضواً في هذه المجموعة")

    try:
        receiver_id = f"{GROUP_PREFIX}{group_id}"
        condition = Chat_messages.receiver_id == receiver_id

        count_q = select(func.count()).select_from(Chat_messages).where(condition)
        total_result = await db.execute(count_q)
        total = total_result.scalar() or 0

        q = (
            select(Chat_messages)
            .where(condition)
            .order_by(Chat_messages.created_at.asc())
            .offset(skip)
            .limit(limit)
        )
        result = await db.execute(q)
        messages = result.scalars().all()

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
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting group messages: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/groups/{group_id}/members", response_model=GroupMemberResponse, status_code=201)
async def add_group_member(
    group_id: int,
    data: AddMemberRequest,
    current_user: UserResponse = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Add a member to a group. Only existing members can add people."""
    if not await _is_group_member(db, group_id, current_user.id):
        raise HTTPException(status_code=403, detail="لست عضواً في هذه المجموعة")

    try:
        if await _is_group_member(db, group_id, data.user_id):
            raise HTTPException(status_code=400, detail="المستخدم عضو بالفعل")

        member = Chat_group_members(
            group_id=group_id,
            user_id=data.user_id,
            user_name=data.user_name or data.user_id[:8],
        )
        db.add(member)
        await db.commit()
        return GroupMemberResponse(user_id=member.user_id, user_name=member.user_name)
    except HTTPException:
        raise
    except Exception as e:
        await db.rollback()
        logger.error(f"Error adding group member: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/groups/{group_id}/members/{user_id}", status_code=204)
async def remove_group_member(
    group_id: int,
    user_id: str,
    current_user: UserResponse = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Leave a group, or (if you're the creator) remove another member."""
    group_q = select(Chat_groups).where(Chat_groups.id == group_id)
    group_result = await db.execute(group_q)
    group = group_result.scalar_one_or_none()
    if not group:
        raise HTTPException(status_code=404, detail="المجموعة غير موجودة")

    is_self = user_id == current_user.id
    is_creator = group.created_by == current_user.id
    if not is_self and not is_creator:
        raise HTTPException(status_code=403, detail="غير مصرح لك بإزالة هذا العضو")

    try:
        await db.execute(
            delete(Chat_group_members).where(
                and_(
                    Chat_group_members.group_id == group_id,
                    Chat_group_members.user_id == user_id,
                )
            )
        )
        await db.commit()
        return None
    except Exception as e:
        await db.rollback()
        logger.error(f"Error removing group member: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/team-messages", response_model=ConversationListResponse)
async def get_team_messages(
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=200),
    current_user: UserResponse = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get the team-wide broadcast channel: every message ever sent with
    receiver_id == 'team', from any sender. Not a 1-on-1 thread, so there's
    no read-marking here (a broadcast has many readers)."""
    try:
        condition = Chat_messages.receiver_id == TEAM_CHANNEL_ID

        count_q = select(func.count()).select_from(Chat_messages).where(condition)
        total_result = await db.execute(count_q)
        total = total_result.scalar() or 0

        q = (
            select(Chat_messages)
            .where(condition)
            .order_by(Chat_messages.created_at.asc())
            .offset(skip)
            .limit(limit)
        )
        result = await db.execute(q)
        messages = result.scalars().all()

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
        logger.error(f"Error getting team messages: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/recent-contacts", response_model=list[dict])
async def get_recent_contacts(
    current_user: UserResponse = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get list of users the current user has chatted with, with last message and unread count"""
    try:
        # Get all messages involving current user, excluding the team broadcast channel
        condition = and_(
            or_(
                Chat_messages.user_id == current_user.id,
                Chat_messages.receiver_id == current_user.id,
            ),
            Chat_messages.receiver_id != TEAM_CHANNEL_ID,
            Chat_messages.receiver_id.notlike(f"{GROUP_PREFIX}%"),
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
        now_dt = datetime.now(timezone.utc)
        display_name = current_user.name or current_user.id[:8]

        # Check if presence record exists. There's no unique constraint on
        # user_id, so a race between concurrent heartbeats can leave
        # duplicate rows for the same user — tolerate that (keep the
        # newest, delete the rest) instead of crashing on it.
        q = (
            select(User_presence)
            .where(User_presence.user_id == current_user.id)
            .order_by(User_presence.id.desc())
        )
        result = await db.execute(q)
        rows = result.scalars().all()
        presence = rows[0] if rows else None
        for extra in rows[1:]:
            await db.delete(extra)

        if presence:
            presence.is_online = True
            presence.last_active_at = now_dt
            presence.display_name = display_name
        else:
            presence = User_presence(
                user_id=current_user.id,
                display_name=display_name,
                is_online=True,
                last_active_at=now_dt,
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

        cutoff = datetime.now(timezone.utc) - timedelta(seconds=60)

        items = []
        for p in all_presence:
            is_online = False
            if p.last_active_at:
                try:
                    last_active = p.last_active_at
                    if not isinstance(last_active, datetime):
                        last_active = datetime.strptime(str(last_active), "%Y-%m-%d %H:%M:%S")
                    if last_active.tzinfo is None:
                        last_active = last_active.replace(tzinfo=timezone.utc)
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