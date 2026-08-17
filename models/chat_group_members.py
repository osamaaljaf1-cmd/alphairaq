from core.database import Base
from sqlalchemy import Column, DateTime, Integer, String, func


class Chat_group_members(Base):
    __tablename__ = "chat_group_members"
    __table_args__ = {"extend_existing": True}

    id = Column(Integer, primary_key=True, index=True, autoincrement=True, nullable=False)
    group_id = Column(Integer, nullable=False)
    user_id = Column(String, nullable=False)
    user_name = Column(String, nullable=True)
    added_at = Column(DateTime(timezone=True), server_default=func.now())
