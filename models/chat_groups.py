from core.database import Base
from sqlalchemy import Column, DateTime, Integer, String, func


class Chat_groups(Base):
    __tablename__ = "chat_groups"
    __table_args__ = {"extend_existing": True}

    id = Column(Integer, primary_key=True, index=True, autoincrement=True, nullable=False)
    name = Column(String, nullable=False)
    created_by = Column(String, nullable=False)
    created_by_name = Column(String, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
