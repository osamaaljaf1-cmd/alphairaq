from core.database import Base
from sqlalchemy import Column, DateTime, Float, Integer, String, Text, func


class CustomerCredits(Base):
    """A return / credit note of a customer, mirrored from the accounting program (Phenix): the
    money the customer is owed back, kept as a NEGATIVE amount so the customer's balance here can
    show the same net figure as the program. Kept apart from the debts table on purpose: a credit
    is never a debt to collect, pay or count as open. Created on first use (see services/debt_sync)."""

    __tablename__ = "customer_credits"
    __table_args__ = {"extend_existing": True}

    id = Column(Integer, primary_key=True, index=True, autoincrement=True, nullable=False)
    customer_name = Column(String, nullable=False, index=True)
    credit_key = Column(String, nullable=False, unique=True, index=True)  # normalized document number
    invoice_number = Column(String, nullable=False)
    amount = Column(Float, nullable=False)  # negative
    remaining_amount = Column(Float, nullable=False)  # negative; 0 once closed
    invoice_date = Column(String, nullable=True)
    status = Column(String, nullable=False, default="open")  # open | closed
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class DebtSyncCreditChanges(Base):
    """What a sync run did to the credits, so the run can be undone (like DebtSyncChanges)."""

    __tablename__ = "debt_sync_credit_changes"
    __table_args__ = {"extend_existing": True}

    id = Column(Integer, primary_key=True, index=True, autoincrement=True, nullable=False)
    run_id = Column(Integer, nullable=False, index=True)
    credit_id = Column(Integer, nullable=False, index=True)
    action = Column(String, nullable=False)  # insert | update | close
    before = Column(Text, nullable=True)  # JSON of the credit before the change (null for insert)
