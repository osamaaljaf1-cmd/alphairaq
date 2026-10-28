from core.database import Base
from sqlalchemy import Column, Integer, Text


class PaymentReceiptNotes(Base):
    """The note printed on a payment's receipt for the customer. Kept in its own
    table (created on first use, like payment_handovers) instead of a new column
    on payments, so the payments table every other query depends on is untouched.
    The payment's own ``notes`` stay internal to the company."""

    __tablename__ = "payment_receipt_notes"
    __table_args__ = {"extend_existing": True}

    payment_id = Column(Integer, primary_key=True, autoincrement=False)
    note = Column(Text, nullable=False)
