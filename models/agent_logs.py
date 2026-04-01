from core.database import Base
from sqlalchemy import Boolean, Column, DateTime, Float, Integer, String


class Agent_logs(Base):
    __tablename__ = "agent_logs"
    __table_args__ = {"extend_existing": True}

    id = Column(Integer, primary_key=True, index=True, autoincrement=True, nullable=False)
    user_id = Column(String, nullable=False)
    agent_id = Column(String, nullable=True)
    latitude = Column(Float, nullable=True)
    longitude = Column(Float, nullable=True)
    timestamp = Column(DateTime(timezone=True), nullable=True)
    app_open = Column(Boolean, nullable=True, default=False, server_default='false')
    app_close = Column(Boolean, nullable=True, default=False, server_default='false')