from core.database import Base
from sqlalchemy import Column, DateTime, Float, Integer, String, func


class Debt_return_applications(Base):
    """Records that a return's value was applied against a specific debt
    invoice, reducing its remaining_amount — the return equivalent of
    PaymentDetails (which does the same thing for a cash payment)."""

    __tablename__ = "debt_return_applications"
    __table_args__ = {"extend_existing": True}

    id = Column(Integer, primary_key=True, index=True, autoincrement=True, nullable=False)
    user_id = Column(String, nullable=False)
    return_id = Column(Integer, nullable=False)
    debt_id = Column(Integer, nullable=False)
    amount_applied = Column(Float, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
