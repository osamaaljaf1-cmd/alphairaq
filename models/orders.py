from core.database import Base
from sqlalchemy import Column, DateTime, Float, Integer, String


class Orders(Base):
    __tablename__ = "orders"
    __table_args__ = {"extend_existing": True}

    id = Column(Integer, primary_key=True, index=True, autoincrement=True, nullable=False)
    user_id = Column(String, nullable=False)
    representative_id = Column(Integer, nullable=True)
    pharmacy_id = Column(Integer, nullable=False)
    doctor_id = Column(Integer, nullable=True)
    status = Column(String, nullable=False)
    total_amount = Column(Float, nullable=True)
    bonus_total = Column(Float, nullable=True)
    notes = Column(String, nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=True)
    approved_at = Column(DateTime(timezone=True), nullable=True)
    approved_by = Column(String, nullable=True)
    manager_approved_at = Column(DateTime(timezone=True), nullable=True)
    manager_approved_by = Column(String, nullable=True)
    accounting_approved_at = Column(DateTime(timezone=True), nullable=True)
    accounting_approved_by = Column(String, nullable=True)
    printed_at = Column(DateTime(timezone=True), nullable=True)
    printed_by = Column(String, nullable=True)
    delivered_at = Column(DateTime(timezone=True), nullable=True)
    delivered_by = Column(String, nullable=True)