"""Chat additions kept in tables of their own: the database is not migrated column by column, while a
new table is created at start-up, so nothing has to be added to chat_messages."""
from core.database import Base
from sqlalchemy import Column, DateTime, Integer, String, UniqueConstraint, func


class Chat_message_extras(Base):
    """What a message has beyond its text: the message it answers, and whether its sender deleted it."""

    __tablename__ = "chat_message_extras"
    __table_args__ = {"extend_existing": True}

    id = Column(Integer, primary_key=True, index=True, autoincrement=True, nullable=False)
    message_id = Column(Integer, nullable=False, unique=True, index=True)
    reply_to_id = Column(Integer, nullable=True)
    deleted_at = Column(DateTime(timezone=True), nullable=True)


class Chat_reads(Base):
    """How far each person has read the team channel and each group (private messages keep their own
    is_read flag)."""

    __tablename__ = "chat_reads"
    __table_args__ = (UniqueConstraint("user_id", "channel_id", name="uq_chat_reads_user_channel"), {"extend_existing": True})

    id = Column(Integer, primary_key=True, index=True, autoincrement=True, nullable=False)
    user_id = Column(String, nullable=False, index=True)
    channel_id = Column(String, nullable=False)
    last_read_id = Column(Integer, nullable=False, default=0)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
