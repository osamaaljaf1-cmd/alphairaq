from core.database import Base
from sqlalchemy import Column, DateTime, Float, Integer, String


class Activity_logs(Base):
    __tablename__ = "activity_logs"
    __table_args__ = {"extend_existing": True}

    id = Column(Integer, primary_key=True, index=True, autoincrement=True, nullable=False)
    user_id = Column(String, nullable=False)
    action = Column(String, nullable=False)
    page = Column(String, nullable=False)
    timestamp = Column(DateTime(timezone=True), nullable=True)
    # New fields for doctor visits and activity tracking
    type = Column(String, nullable=True, default="general")
    doctor_id = Column(Integer, nullable=True)
    doctor_name = Column(String, nullable=True)
    pharmacy_id = Column(Integer, nullable=True)
    pharmacy_name = Column(String, nullable=True)
    rep_id = Column(Integer, nullable=True)
    details = Column(String, nullable=True)
    # Item and geolocation fields
    item_id = Column(Integer, nullable=True)
    item_name = Column(String, nullable=True)
    latitude = Column(Float, nullable=True)
    longitude = Column(Float, nullable=True)