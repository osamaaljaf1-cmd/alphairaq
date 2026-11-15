"""WhatsApp-style chat: one list of every conversation (private chats, private groups, the team
channel) with the last message and how many are unread, a thread endpoint that pages back from the
newest message and marks what was shown as read, replies, "delete for everyone", "typing…", the
people directory and group renaming.

A thread is named by its channel: another user's id (a private chat), "team", or "group:<id>".
Private messages keep their own is_read flag; how far each person read the team channel and each
group is kept in chat_reads. Replies and deletions live in chat_message_extras.
"""
import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from core.database import get_db
from dependencies.auth import get_current_user
from models.app_users import App_users
from models.chat_extras import Chat_attachments, Chat_message_extras, Chat_reads
from models.chat_group_members import Chat_group_members
from models.chat_groups import Chat_groups
from models.chat_messages import Chat_messages
from models.user_presence import User_presence
from schemas.auth import UserResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/chat", tags=["chat"])

TEAM = "team"
GROUP_PREFIX = "group:"
ONLINE_SECONDS = 60
TYPING_SECONDS = 6
DELETE_WINDOW = timedelta(hours=48)

# who is typing where: {(thread key, user id): (monotonic time, name)} — one process, short-lived
_typing: Dict[Tuple[str, str], Tuple[float, str]] = {}


# ---------- helpers ----------
def _utc(dt: Optional[datetime]) -> Optional[datetime]:
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _iso(dt: Optional[datetime]) -> Optional[str]:
    dt = _utc(dt)
    return dt.isoformat() if dt else None


def _is_channel(channel: str) -> bool:
    return channel == TEAM or channel.startswith(GROUP_PREFIX)


def _kind(channel: str) -> str:
    if channel == TEAM:
        return "team"
    return "group" if channel.startswith(GROUP_PREFIX) else "direct"


def _thread_key(me: str, channel: str) -> str:
    return channel if _is_channel(channel) else "dm:" + "|".join(sorted([me, channel]))


def _thread_condition(me: str, channel: str):
    if _is_channel(channel):
        return Chat_messages.receiver_id == channel
    return or_(
        and_(Chat_messages.user_id == me, Chat_messages.receiver_id == channel),
        and_(Chat_messages.user_id == channel, Chat_messages.receiver_id == me),
    )


def _direct_condition(me: str):
    return and_(
        or_(Chat_messages.user_id == me, Chat_messages.receiver_id == me),
        Chat_messages.receiver_id != TEAM,
        Chat_messages.receiver_id.notlike(f"{GROUP_PREFIX}%"),
    )


async def _authorize(db: AsyncSession, me: str, channel: str) -> Optional[int]:
    """The group id for a group channel (403 unless a member); None otherwise."""
    if not channel:
        raise HTTPException(status_code=400, detail="المحادثة غير محددة")
    if channel.startswith(GROUP_PREFIX):
        raw = channel[len(GROUP_PREFIX):]
        if not raw.isdigit():
            raise HTTPException(status_code=400, detail="مجموعة غير صحيحة")
        gid = int(raw)
        member = (
            await db.execute(
                select(Chat_group_members.id).where(Chat_group_members.group_id == gid, Chat_group_members.user_id == me).limit(1)
            )
        ).first()
        if not member:
            raise HTTPException(status_code=403, detail="لست عضواً في هذه المجموعة")
        return gid
    if channel == me:
        raise HTTPException(status_code=400, detail="لا يمكن مراسلة نفسك")
    return None


async def _presence(db: AsyncSession) -> Dict[str, Tuple[bool, Optional[str]]]:
    cutoff = datetime.now(timezone.utc) - timedelta(seconds=ONLINE_SECONDS)
    out: Dict[str, Tuple[bool, Optional[str]]] = {}
    for uid, last in (await db.execute(select(User_presence.user_id, User_presence.last_active_at))).all():
        last = _utc(last)
        prev = out.get(uid)
        if prev and prev[1] and last and prev[1] >= _iso(last):
            continue
        out[uid] = (bool(last and last > cutoff), _iso(last))
    return out


async def _account_names(db: AsyncSession, ids) -> Dict[str, str]:
    ids = [i for i in set(ids) if i]
    if not ids:
        return {}
    rows = (await db.execute(select(App_users.user_id, App_users.name).where(App_users.user_id.in_(ids)))).all()
    return {uid: (name or "").strip() for uid, name in rows if name and name.strip()}


async def _my_channels(db: AsyncSession, me: str) -> Tuple[List[str], List[int]]:
    gids = [r[0] for r in (await db.execute(select(Chat_group_members.group_id).where(Chat_group_members.user_id == me))).all()]
    return [TEAM] + [f"{GROUP_PREFIX}{g}" for g in gids], gids


async def _latest_ids(db: AsyncSession, channels: List[str]) -> Dict[str, int]:
    if not channels:
        return {}
    rows = (
        await db.execute(
            select(Chat_messages.receiver_id, func.max(Chat_messages.id))
            .where(Chat_messages.receiver_id.in_(channels))
            .group_by(Chat_messages.receiver_id)
        )
    ).all()
    return {ch: mid for ch, mid in rows}


async def _cursors(db: AsyncSession, me: str, channels: List[str], latest: Dict[str, int]) -> Dict[str, int]:
    """How far I read each channel. A channel I never opened since this feature exists starts as read
    up to its latest message (its old history is not "new")."""
    if not channels:
        return {}
    rows = (await db.execute(select(Chat_reads.channel_id, Chat_reads.last_read_id).where(Chat_reads.user_id == me, Chat_reads.channel_id.in_(channels)))).all()
    cur = {ch: last for ch, last in rows}
    missing = [ch for ch in channels if ch not in cur]
    if missing:
        for ch in missing:
            db.add(Chat_reads(user_id=me, channel_id=ch, last_read_id=latest.get(ch, 0)))
            cur[ch] = latest.get(ch, 0)
        try:
            await db.commit()
        except IntegrityError:  # two requests created the same rows at once
            await db.rollback()
            rows = (await db.execute(select(Chat_reads.channel_id, Chat_reads.last_read_id).where(Chat_reads.user_id == me, Chat_reads.channel_id.in_(channels)))).all()
            cur = {ch: last for ch, last in rows}
    return cur


async def _channel_unread(db: AsyncSession, me: str, cursors: Dict[str, int]) -> Dict[str, int]:
    out: Dict[str, int] = {}
    for ch, last_read in cursors.items():
        n = (
            await db.execute(
                select(func.count(Chat_messages.id)).where(
                    Chat_messages.receiver_id == ch, Chat_messages.id > (last_read or 0), Chat_messages.user_id != me
                )
            )
        ).scalar() or 0
        out[ch] = int(n)
    return out


async def _mark_read(db: AsyncSession, me: str, channel: str, upto_id: int) -> None:
    if _is_channel(channel):
        row = (await db.execute(select(Chat_reads).where(Chat_reads.user_id == me, Chat_reads.channel_id == channel))).scalar_one_or_none()
        if row is None:
            db.add(Chat_reads(user_id=me, channel_id=channel, last_read_id=upto_id))
        elif (row.last_read_id or 0) < upto_id:
            row.last_read_id = upto_id
    else:
        await db.execute(
            update(Chat_messages)
            .where(
                Chat_messages.user_id == channel,
                Chat_messages.receiver_id == me,
                Chat_messages.id <= upto_id,
                Chat_messages.is_read == False,  # noqa: E712
            )
            .values(is_read=True)
        )
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()


async def _extras(db: AsyncSession, ids) -> Dict[int, Chat_message_extras]:
    ids = [i for i in set(ids) if i]
    if not ids:
        return {}
    rows = (await db.execute(select(Chat_message_extras).where(Chat_message_extras.message_id.in_(ids)))).scalars().all()
    return {r.message_id: r for r in rows}


IMAGE_PLACEHOLDER = "📷 صورة"
AUDIO_PLACEHOLDER = "🎤 رسالة صوتية"
IMAGE_TYPES = {"image/jpeg", "image/png", "image/webp", "image/gif"}
AUDIO_TYPES = {"audio/webm", "audio/ogg", "audio/mp4", "audio/mpeg", "audio/mp3", "audio/aac", "audio/x-m4a", "audio/m4a", "audio/wav", "audio/x-wav"}
MAX_IMAGE_BYTES = 4 * 1024 * 1024
MAX_AUDIO_BYTES = 6 * 1024 * 1024


def _looks_like_image(data: bytes) -> bool:
    return (
        data.startswith(b"\xff\xd8\xff")  # jpeg
        or data.startswith(b"\x89PNG\r\n\x1a\n")
        or data.startswith(b"GIF87a")
        or data.startswith(b"GIF89a")
        or (data[:4] == b"RIFF" and data[8:12] == b"WEBP")
    )


async def _attachment_meta(db: AsyncSession, ids) -> Dict[int, tuple]:
    """{message id: (kind, mime, size, duration_ms, width, height, has_caption)} — never the bytes."""
    ids = [i for i in set(ids) if i]
    if not ids:
        return {}
    rows = (
        await db.execute(
            select(
                Chat_attachments.message_id, Chat_attachments.kind, Chat_attachments.mime, Chat_attachments.size,
                Chat_attachments.duration_ms, Chat_attachments.width, Chat_attachments.height, Chat_attachments.has_caption,
            ).where(Chat_attachments.message_id.in_(ids))
        )
    ).all()
    return {r[0]: tuple(r[1:]) for r in rows}


async def _may_see_message(db: AsyncSession, me: str, msg: Chat_messages) -> bool:
    if msg.receiver_id == TEAM:
        return True
    if msg.receiver_id.startswith(GROUP_PREFIX):
        raw = msg.receiver_id[len(GROUP_PREFIX):]
        if not raw.isdigit():
            return False
        return (
            await db.execute(select(Chat_group_members.id).where(Chat_group_members.group_id == int(raw), Chat_group_members.user_id == me).limit(1))
        ).first() is not None
    return me in (msg.user_id, msg.receiver_id)


def _snippet(text: Optional[str], n: int = 90) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= n else text[: n - 1] + "…"


# ---------- schemas ----------
class ReplyPreview(BaseModel):
    id: int
    sender_id: str
    sender_name: str
    text: str
    deleted: bool = False


class Attachment(BaseModel):
    kind: str  # image | audio
    mime: str
    size: int = 0
    duration_ms: Optional[int] = None
    width: Optional[int] = None
    height: Optional[int] = None


class ThreadMessage(BaseModel):
    id: int
    user_id: str
    sender_name: str
    text: str
    created_at: Optional[str] = None
    mine: bool
    is_read: bool = False
    deleted: bool = False
    reply_to: Optional[ReplyPreview] = None
    attachment: Optional[Attachment] = None


class Member(BaseModel):
    user_id: str
    name: str
    is_online: bool = False
    last_active_at: Optional[str] = None


class ThreadResponse(BaseModel):
    channel: str
    kind: str
    name: str
    items: List[ThreadMessage]
    has_more: bool
    typing: List[str] = []
    is_online: bool = False
    last_active_at: Optional[str] = None
    members: List[Member] = []
    created_by: Optional[str] = None
    unread_before_open: int = 0
    first_unread_id: Optional[int] = None


class LastMessage(BaseModel):
    id: int
    text: str
    sender_name: str
    mine: bool
    created_at: Optional[str] = None
    deleted: bool = False
    is_read: bool = False
    attachment_kind: Optional[str] = None  # image | audio
    duration_ms: Optional[int] = None


class ChatItem(BaseModel):
    channel: str
    kind: str
    name: str
    unread: int = 0
    last: Optional[LastMessage] = None
    is_online: bool = False
    last_active_at: Optional[str] = None
    member_count: int = 0


class ReadBody(BaseModel):
    channel: str
    last_id: int


class TypingBody(BaseModel):
    channel: str


class RenameBody(BaseModel):
    name: str


# ---------- the list of conversations ----------
@router.get("/chats", response_model=List[ChatItem])
async def list_chats(current_user: UserResponse = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    me = str(current_user.id)
    presence = await _presence(db)

    # private chats: newest message first, one entry per person
    direct_rows = (await db.execute(select(Chat_messages).where(_direct_condition(me)).order_by(Chat_messages.id.desc()))).scalars().all()
    peers: Dict[str, dict] = {}
    for m in direct_rows:
        peer = m.receiver_id if m.user_id == me else m.user_id
        if not peer or peer == me:
            continue
        p = peers.get(peer)
        if p is None:
            p = peers[peer] = {"last": m, "unread": 0, "name": (m.receiver_name if m.user_id == me else m.sender_name) or ""}
        if m.user_id == peer and m.receiver_id == me and not m.is_read:
            p["unread"] += 1
    names = await _account_names(db, peers.keys())

    channels, gids = await _my_channels(db, me)
    latest = await _latest_ids(db, channels)
    cursors = await _cursors(db, me, channels, latest)
    unread = await _channel_unread(db, me, cursors)
    groups = {g.id: g for g in (await db.execute(select(Chat_groups).where(Chat_groups.id.in_(gids)))).scalars().all()} if gids else {}
    counts = dict((await db.execute(select(Chat_group_members.group_id, func.count(Chat_group_members.id)).where(Chat_group_members.group_id.in_(gids)).group_by(Chat_group_members.group_id))).all()) if gids else {}
    channel_last = {m.receiver_id: m for m in (await db.execute(select(Chat_messages).where(Chat_messages.id.in_(list(latest.values()))))).scalars().all()} if latest else {}

    last_ids = [p["last"].id for p in peers.values()] + [m.id for m in channel_last.values()]
    extras = await _extras(db, last_ids)
    media = await _attachment_meta(db, last_ids)

    def last_of(m: Optional[Chat_messages]) -> Optional[LastMessage]:
        if m is None:
            return None
        deleted = bool(extras.get(m.id) and extras[m.id].deleted_at)
        att = media.get(m.id)
        text = "" if deleted or (att and not att[6]) else _snippet(m.message_text)
        return LastMessage(
            id=m.id,
            text=text,
            sender_name=m.sender_name or "",
            mine=m.user_id == me,
            created_at=_iso(m.created_at),
            deleted=deleted,
            is_read=bool(m.is_read),
            attachment_kind=att[0] if att and not deleted else None,
            duration_ms=att[3] if att and not deleted else None,
        )

    items: List[ChatItem] = []
    for peer, p in peers.items():
        online, last_active = presence.get(peer, (False, None))
        items.append(ChatItem(channel=peer, kind="direct", name=names.get(peer) or p["name"] or peer[:8], unread=p["unread"], last=last_of(p["last"]), is_online=online, last_active_at=last_active))
    for ch in channels:
        if ch == TEAM:
            items.append(ChatItem(channel=TEAM, kind="team", name="قناة الفريق", unread=unread.get(ch, 0), last=last_of(channel_last.get(ch))))
        else:
            gid = int(ch[len(GROUP_PREFIX):])
            g = groups.get(gid)
            if g is None:
                continue
            items.append(ChatItem(channel=ch, kind="group", name=g.name, unread=unread.get(ch, 0), last=last_of(channel_last.get(ch)), member_count=int(counts.get(gid, 0))))
    items.sort(key=lambda it: (it.last.id if it.last else 0), reverse=True)
    return items


@router.get("/unread-summary")
async def unread_summary(current_user: UserResponse = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    """What the badge needs: unread private messages and, per team / group channel, unread messages."""
    me = str(current_user.id)
    direct = (
        await db.execute(select(func.count(Chat_messages.id)).where(Chat_messages.receiver_id == me, Chat_messages.is_read == False))  # noqa: E712
    ).scalar() or 0
    channels, _ = await _my_channels(db, me)
    latest = await _latest_ids(db, channels)
    cursors = await _cursors(db, me, channels, latest)
    unread = {ch: n for ch, n in (await _channel_unread(db, me, cursors)).items() if n}
    return {"direct_unread": int(direct), "channels_unread": unread, "total": int(direct) + sum(unread.values())}


# ---------- one conversation ----------
@router.get("/thread", response_model=ThreadResponse)
async def get_thread(
    channel: str = Query(...),
    limit: int = Query(50, ge=1, le=200),
    before_id: Optional[int] = Query(None),
    mark_read: bool = Query(True),
    current_user: UserResponse = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    me = str(current_user.id)
    gid = await _authorize(db, me, channel)
    kind = _kind(channel)

    # what was unread when the thread was opened (for the "unread messages" divider)
    unread_before = 0
    first_unread: Optional[int] = None
    if before_id is None:
        if kind == "direct":
            q = select(func.count(Chat_messages.id), func.min(Chat_messages.id)).where(
                Chat_messages.user_id == channel, Chat_messages.receiver_id == me, Chat_messages.is_read == False  # noqa: E712
            )
            n, first = (await db.execute(q)).one()
        else:
            row = (await db.execute(select(Chat_reads.last_read_id).where(Chat_reads.user_id == me, Chat_reads.channel_id == channel))).first()
            last_read = row[0] if row else None
            if last_read is None:
                n, first = 0, None
            else:
                n, first = (
                    await db.execute(
                        select(func.count(Chat_messages.id), func.min(Chat_messages.id)).where(
                            Chat_messages.receiver_id == channel, Chat_messages.id > last_read, Chat_messages.user_id != me
                        )
                    )
                ).one()
        unread_before, first_unread = int(n or 0), first

    q = select(Chat_messages).where(_thread_condition(me, channel))
    if before_id is not None:
        q = q.where(Chat_messages.id < before_id)
    rows = (await db.execute(q.order_by(Chat_messages.id.desc()).limit(limit + 1))).scalars().all()
    has_more = len(rows) > limit
    rows = list(reversed(rows[:limit]))

    extras = await _extras(db, [m.id for m in rows])
    media = await _attachment_meta(db, [m.id for m in rows])
    reply_ids = [e.reply_to_id for e in extras.values() if e.reply_to_id]
    replied = {m.id: m for m in (await db.execute(select(Chat_messages).where(Chat_messages.id.in_(reply_ids)))).scalars().all()} if reply_ids else {}
    replied_extras = await _extras(db, replied.keys())

    items: List[ThreadMessage] = []
    for m in rows:
        ex = extras.get(m.id)
        deleted = bool(ex and ex.deleted_at)
        reply = None
        if ex and ex.reply_to_id and ex.reply_to_id in replied and not deleted:
            r = replied[ex.reply_to_id]
            r_deleted = bool(replied_extras.get(r.id) and replied_extras[r.id].deleted_at)
            reply = ReplyPreview(id=r.id, sender_id=r.user_id, sender_name=r.sender_name or "", text="" if r_deleted else _snippet(r.message_text, 120), deleted=r_deleted)
        att = media.get(m.id)
        text = "" if deleted else (m.message_text or "")
        if att and not att[6]:
            text = ""  # a photo / voice note without a caption: the placeholder is for old screens only
        items.append(
            ThreadMessage(
                id=m.id,
                user_id=m.user_id,
                sender_name=m.sender_name or "",
                text=text,
                created_at=_iso(m.created_at),
                mine=m.user_id == me,
                is_read=bool(m.is_read),
                deleted=deleted,
                reply_to=reply,
                attachment=None if (deleted or not att) else Attachment(kind=att[0], mime=att[1], size=att[2] or 0, duration_ms=att[3], width=att[4], height=att[5]),
            )
        )

    if mark_read and before_id is None and rows:
        await _mark_read(db, me, channel, rows[-1].id)

    # who is typing here (not me), seen in the last few seconds
    key = _thread_key(me, channel)
    now = time.monotonic()
    typing = [name for (k, uid), (ts, name) in list(_typing.items()) if k == key and uid != me and now - ts < TYPING_SECONDS]

    presence = await _presence(db)
    resp = ThreadResponse(channel=channel, kind=kind, name="", items=items, has_more=has_more, typing=typing, unread_before_open=unread_before, first_unread_id=first_unread)
    if kind == "direct":
        names = await _account_names(db, [channel])
        resp.name = names.get(channel) or next((m.sender_name for m in rows if m.user_id == channel), "") or next((m.receiver_name for m in rows if m.receiver_id == channel and m.receiver_name), "") or channel[:8]
        resp.is_online, resp.last_active_at = presence.get(channel, (False, None))
    elif kind == "group":
        g = (await db.execute(select(Chat_groups).where(Chat_groups.id == gid))).scalar_one_or_none()
        resp.name = g.name if g else ""
        resp.created_by = g.created_by if g else None
        members = (await db.execute(select(Chat_group_members).where(Chat_group_members.group_id == gid))).scalars().all()
        names = await _account_names(db, [m.user_id for m in members])
        resp.members = sorted(
            [Member(user_id=m.user_id, name=names.get(m.user_id) or m.user_name or m.user_id[:8], is_online=presence.get(m.user_id, (False, None))[0], last_active_at=presence.get(m.user_id, (False, None))[1]) for m in members],
            key=lambda x: (x.user_id != me, x.name),
        )
    else:
        resp.name = "قناة الفريق"
    return resp


@router.post("/read")
async def mark_read(data: ReadBody, current_user: UserResponse = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    me = str(current_user.id)
    await _authorize(db, me, data.channel)
    await _mark_read(db, me, data.channel, data.last_id)
    return {"ok": True}


@router.post("/typing")
async def typing(data: TypingBody, current_user: UserResponse = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    me = str(current_user.id)
    await _authorize(db, me, data.channel)
    if len(_typing) > 2000:
        _typing.clear()
    _typing[(_thread_key(me, data.channel), me)] = (time.monotonic(), current_user.name or me[:8])
    return {"ok": True}


@router.delete("/messages/{message_id}")
async def delete_message(message_id: int, current_user: UserResponse = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    """Delete for everyone: only the sender, within 48 hours. The text is hidden from everybody."""
    me = str(current_user.id)
    msg = (await db.execute(select(Chat_messages).where(Chat_messages.id == message_id))).scalar_one_or_none()
    if msg is None:
        raise HTTPException(status_code=404, detail="الرسالة غير موجودة")
    if msg.user_id != me:
        raise HTTPException(status_code=403, detail="يمكنك حذف رسائلك فقط")
    created = _utc(msg.created_at)
    if created and datetime.now(timezone.utc) - created > DELETE_WINDOW:
        raise HTTPException(status_code=409, detail="لا يمكن حذف رسالة مضى عليها أكثر من 48 ساعة")
    ex = (await db.execute(select(Chat_message_extras).where(Chat_message_extras.message_id == message_id))).scalar_one_or_none()
    if ex is None:
        db.add(Chat_message_extras(message_id=message_id, deleted_at=datetime.now(timezone.utc)))
    elif ex.deleted_at is None:
        ex.deleted_at = datetime.now(timezone.utc)
    # a deleted photo / voice note is erased, not just hidden
    await db.execute(update(Chat_attachments).where(Chat_attachments.message_id == message_id).values(data=None, size=0))
    await db.commit()
    return {"ok": True}


# ---------- photos and voice notes ----------
@router.post("/send-media", response_model=ThreadMessage, status_code=201)
async def send_media(
    receiver_id: str = Form(...),
    receiver_name: str = Form(""),
    kind: str = Form(...),
    caption: str = Form(""),
    reply_to_id: Optional[int] = Form(None),
    duration_ms: Optional[int] = Form(None),
    width: Optional[int] = Form(None),
    height: Optional[int] = Form(None),
    file: UploadFile = File(...),
    current_user: UserResponse = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Send a photo (already shrunk by the phone) or a voice note to a person, a group or the team."""
    me = str(current_user.id)
    await _authorize(db, me, receiver_id)
    if kind not in ("image", "audio"):
        raise HTTPException(status_code=400, detail="نوع الملف غير مدعوم")
    mime = (file.content_type or "").split(";")[0].strip().lower()
    limit = MAX_IMAGE_BYTES if kind == "image" else MAX_AUDIO_BYTES
    if (kind == "image" and mime not in IMAGE_TYPES) or (kind == "audio" and mime not in AUDIO_TYPES):
        raise HTTPException(status_code=400, detail="صيغة الملف غير مدعومة")
    data = await file.read(limit + 1)
    if not data:
        raise HTTPException(status_code=400, detail="الملف فارغ")
    if len(data) > limit:
        raise HTTPException(status_code=413, detail="الملف كبير جداً")
    if kind == "image" and not _looks_like_image(data):
        raise HTTPException(status_code=400, detail="الملف ليس صورة")

    caption = (caption or "").strip()[:4000]
    reply_ok = None
    if reply_to_id:
        found = (await db.execute(select(Chat_messages.id).where(Chat_messages.id == reply_to_id, _thread_condition(me, receiver_id)))).first()
        reply_ok = reply_to_id if found else None

    now = datetime.now(timezone.utc)
    msg = Chat_messages(
        user_id=me,
        sender_name=current_user.name or me[:8],
        receiver_id=receiver_id,
        receiver_name=receiver_name or None,
        message_text=caption or (IMAGE_PLACEHOLDER if kind == "image" else AUDIO_PLACEHOLDER),
        is_read=False,
        created_at=now,
    )
    db.add(msg)
    await db.flush()
    db.add(
        Chat_attachments(
            message_id=msg.id,
            kind=kind,
            mime=mime,
            size=len(data),
            duration_ms=max(0, min(int(duration_ms), 3_600_000)) if duration_ms else None,
            width=width if width and 0 < width < 20000 else None,
            height=height if height and 0 < height < 20000 else None,
            has_caption=bool(caption),
            data=data,
        )
    )
    if reply_ok:
        db.add(Chat_message_extras(message_id=msg.id, reply_to_id=reply_ok))
    await db.commit()
    return ThreadMessage(
        id=msg.id,
        user_id=me,
        sender_name=msg.sender_name,
        text=caption,
        created_at=_iso(now),
        mine=True,
        attachment=Attachment(kind=kind, mime=mime, size=len(data), duration_ms=duration_ms, width=width, height=height),
    )


@router.get("/attachments/{message_id}")
async def get_attachment(message_id: int, current_user: UserResponse = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    """The photo / voice note of a message, for the people of that conversation only."""
    me = str(current_user.id)
    msg = (await db.execute(select(Chat_messages).where(Chat_messages.id == message_id))).scalar_one_or_none()
    if msg is None or not await _may_see_message(db, me, msg):
        raise HTTPException(status_code=404, detail="غير موجود")
    att = (await db.execute(select(Chat_attachments).where(Chat_attachments.message_id == message_id))).scalar_one_or_none()
    if att is None:
        raise HTTPException(status_code=404, detail="غير موجود")
    if not att.data:
        raise HTTPException(status_code=410, detail="تم حذف هذا الملف")
    return Response(
        content=bytes(att.data),
        media_type=att.mime,
        headers={"Cache-Control": "private, max-age=604800", "X-Content-Type-Options": "nosniff", "Content-Disposition": "inline"},
    )


# ---------- people and groups ----------
@router.get("/people", response_model=List[Member])
async def list_people(current_user: UserResponse = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    """Everybody one can write to (the active accounts), with whether they are online."""
    me = str(current_user.id)
    presence = await _presence(db)
    rows = (
        await db.execute(select(App_users.user_id, App_users.name).where(or_(App_users.status.is_(None), App_users.status == "active")))
    ).all()
    out = [
        Member(user_id=uid, name=(name or "").strip() or uid[:8], is_online=presence.get(uid, (False, None))[0], last_active_at=presence.get(uid, (False, None))[1])
        for uid, name in rows
        if uid and uid != me
    ]
    return sorted(out, key=lambda m: (not m.is_online, m.name))


@router.put("/groups/{group_id}")
async def rename_group(group_id: int, data: RenameBody, current_user: UserResponse = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    me = str(current_user.id)
    await _authorize(db, me, f"{GROUP_PREFIX}{group_id}")
    name = " ".join((data.name or "").split())
    if not name:
        raise HTTPException(status_code=400, detail="اسم المجموعة مطلوب")
    g = (await db.execute(select(Chat_groups).where(Chat_groups.id == group_id))).scalar_one_or_none()
    if g is None:
        raise HTTPException(status_code=404, detail="المجموعة غير موجودة")
    g.name = name[:80]
    await db.commit()
    return {"ok": True, "name": g.name}
