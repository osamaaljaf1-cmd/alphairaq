from core.database import Base
from sqlalchemy import Boolean, Column, DateTime, Integer, String


class Chat_messages(Base):
    __tablename__ = "chat_messages"
    __table_args__ = {"extend_existing": True}

    id = Column(Integer, primary_key=True, index=True, autoincrement=True, nullable=False)
    user_id = Column(String, nullable=False)
    sender_name = Column(String, nullable=False)
    receiver_id = Column(String, nullable=False)
    receiver_name = Column(String, nullable=True)
    message_text = Column(String, nullable=False)
    is_read = Column(Boolean, nullable=True, default=False, server_default='false')
    created_at = Column(DateTime(timezone=True), nullable=False)