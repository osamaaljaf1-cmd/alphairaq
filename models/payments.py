from core.database import Base
from sqlalchemy import Boolean, Column, DateTime, Float, Integer, String, Text, func


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
    rep_name = Column(String, nullable=True)
    receipt_image = Column(Text, nullable=True)
    receipt_captured_at = Column(DateTime(timezone=True), nullable=True)
    status = Column(String, nullable=True)  # active (default/NULL), canceled
    canceled_at = Column(DateTime(timezone=True), nullable=True)
    canceled_by = Column(String, nullable=True)
    cancel_reason = Column(String, nullable=True)
    # Cash-handover tracking: a payment recorded by a rep out in the field
    # starts as handed_over=False ("still with the rep") until accounting
    # confirms they physically received it (see /payments/confirm-handover).
    # Payments recorded directly by non-rep roles are marked handed_over=True
    # immediately since there's no rep holding cash in that case.
    handed_over = Column(Boolean, nullable=True, default=False)
    handed_over_at = Column(DateTime(timezone=True), nullable=True)
    handed_over_by = Column(String, nullable=True)