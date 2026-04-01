from core.database import Base
from sqlalchemy import Boolean, Column, DateTime, Integer, String


class User_presence(Base):
    __tablename__ = "user_presence"
    __table_args__ = {"extend_existing": True}

    id = Column(Integer, primary_key=True, index=True, autoincrement=True, nullable=False)
    user_id = Column(String, nullable=False)
    display_name = Column(String, nullable=False)
    is_online = Column(Boolean, nullable=True, default=False, server_default='false')
    last_active_at = Column(DateTime(timezone=True), nullable=False)