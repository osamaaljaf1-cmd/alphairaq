from core.database import Base
from sqlalchemy import Column, DateTime, Float, Integer, String, func


class Payments(Base):
    __tablename__ = "payments"
    __table_args__ = {"extend_existing": True}

    id = Column(Integer, primary_key=True, index=True, autoincrement=True, nullable=False)
    user_id = Column(String, nullable=False)
    customer_name = Column(String, nullable=False)
    total_selected = Column(Float, nullable=False)
    discount = Column(Float, nullable=False, default=0)
    returns_amount = Column(Float, nullable=False, default=0)
    net_received = Column(Float, nullable=False)
    notes = Column(String, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())