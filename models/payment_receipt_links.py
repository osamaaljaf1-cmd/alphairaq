from core.database import Base
from sqlalchemy import Column, DateTime, Integer, String, func


class PaymentReceiptLinks(Base):
    """Public, unguessable link to a payment's receipt (the QR code printed on it).
    The token is random — never derived from the payment id — so receipts cannot be
    found by trying numbers. Own table, created on first use like payment_handovers,
    so the payments table is not altered. ``template_id`` is the print template
    (company header) the receipt was issued with."""

    __tablename__ = "payment_receipt_links"
    __table_args__ = {"extend_existing": True}

    token = Column(String(64), primary_key=True)
    payment_id = Column(Integer, nullable=False, unique=True, index=True)
    template_id = Column(String(32), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
