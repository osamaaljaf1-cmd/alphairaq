from core.database import Base
from sqlalchemy import Column, DateTime, Float, Integer, String, func


class Debts(Base):
    __tablename__ = "debts"
    __table_args__ = {"extend_existing": True}

    id = Column(Integer, primary_key=True, index=True, autoincrement=True, nullable=False)
    user_id = Column(String, nullable=False)
    customer_name = Column(String, nullable=False)
    invoice_number = Column(String, nullable=False, unique=True, index=True)
    amount = Column(Float, nullable=False)
    remaining_amount = Column(Float, nullable=False)
    invoice_date = Column(String, nullable=True)
    status = Column(String, nullable=False, default="unpaid")  # unpaid, partial, paid
    notes = Column(String, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())