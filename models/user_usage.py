from core.database import Base
from sqlalchemy import Column, Date, DateTime, Integer, String


class User_usage(Base):
    __tablename__ = "user_usage"
    __table_args__ = {"extend_existing": True}

    id = Column(Integer, primary_key=True, index=True, autoincrement=True, nullable=False)
    user_id = Column(String, nullable=False)
    login_time = Column(DateTime(timezone=True), nullable=True)
    logout_time = Column(DateTime(timezone=True), nullable=True)
    duration_minutes = Column(Integer, nullable=True)
    date = Column(Date, nullable=True)