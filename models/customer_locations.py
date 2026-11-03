from core.database import Base
from sqlalchemy import Column, DateTime, Float, Integer, String, func


class CustomerLocations(Base):
    """Where a customer's pharmacy is (one point per customer), so the delivery person can open
    it in Google Maps / Waze. A table of its own, created on first use: the customers table is
    never altered."""

    __tablename__ = "customer_locations"
    __table_args__ = {"extend_existing": True}

    id = Column(Integer, primary_key=True, index=True, autoincrement=True, nullable=False)
    pharmacy_id = Column(Integer, nullable=False, unique=True, index=True)
    latitude = Column(Float, nullable=False)
    longitude = Column(Float, nullable=False)
    accuracy_m = Column(Float, nullable=True)  # GPS accuracy in metres, when it came from a phone
    source = Column(String, nullable=False, default="gps")  # gps | manual | visit
    set_by = Column(String, nullable=True)  # user id
    set_by_name = Column(String, nullable=True)
    set_at = Column(DateTime(timezone=True), server_default=func.now())
