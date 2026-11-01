from core.database import Base
from sqlalchemy import Column, DateTime, Float, Integer, String, Text, func


class OrderDeliveries(Base):
    """How an order ended at the customer's door, written by the delivery person: either
    delivered (photo of the receipt after the customer's stamp, plus notes) or refused by the
    customer (reason, notes, optional photo). Kept in its own table, created on first use."""

    __tablename__ = "order_deliveries"
    __table_args__ = {"extend_existing": True}

    id = Column(Integer, primary_key=True, index=True, autoincrement=True, nullable=False)
    order_id = Column(Integer, nullable=False, unique=True, index=True)
    pharmacy_id = Column(Integer, nullable=True, index=True)
    representative_id = Column(Integer, nullable=True)
    outcome = Column(String, nullable=False)  # delivered | refused
    reason = Column(String, nullable=True)  # why the customer refused
    notes = Column(Text, nullable=True)
    photo = Column(Text, nullable=True)  # the receipt photo (data URL, compressed by the app)
    thumb = Column(Text, nullable=True)  # a small version for lists
    order_amount = Column(Float, nullable=True)
    recorded_by = Column(String, nullable=False)  # user id
    recorded_by_name = Column(String, nullable=True)
    recorded_at = Column(DateTime(timezone=True), server_default=func.now())


class OrderEvents(Base):
    """A notification for one user about an order (the customer refused it, it was delivered)."""

    __tablename__ = "order_events"
    __table_args__ = {"extend_existing": True}

    id = Column(Integer, primary_key=True, index=True, autoincrement=True, nullable=False)
    target_user_id = Column(String, nullable=False, index=True)
    kind = Column(String, nullable=False)  # order_refused | order_delivered
    order_id = Column(Integer, nullable=False, index=True)
    pharmacy_id = Column(Integer, nullable=True)
    pharmacy_name = Column(String, nullable=True)
    message = Column(Text, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    read_at = Column(DateTime(timezone=True), nullable=True)
