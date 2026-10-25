from core.database import Base
from sqlalchemy import Column, DateTime, Integer, String, func


class PaymentHandovers(Base):
    """Archive record written when accounting confirms receiving a rep's
    collected cash: ties each payment to the manual receipt number accounting
    wrote on the handover, and groups the payments confirmed together
    (`batch_id`, one receipt for one rep) so the archive reads like a ledger
    of signed handover receipts."""

    __tablename__ = "payment_handovers"
    __table_args__ = {"extend_existing": True}

    id = Column(Integer, primary_key=True, index=True, autoincrement=True, nullable=False)
    payment_id = Column(Integer, nullable=False, unique=True, index=True)
    batch_id = Column(String, nullable=False, index=True)
    receipt_number = Column(String, nullable=False, index=True)
    rep_user_id = Column(String, nullable=False, index=True)  # the rep who handed the cash over
    confirmed_by = Column(String, nullable=False)  # accounting user id
    confirmed_by_name = Column(String, nullable=True)
    confirmed_at = Column(DateTime(timezone=True), server_default=func.now())
