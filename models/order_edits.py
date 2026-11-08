from core.database import Base
from sqlalchemy import Column, DateTime, Float, Integer, String, Text, func


class OrderEditLog(Base):
    """Who changed an order's lines or totals, at which stage, and what it was before and after.
    Kept in its own table, created on first use (the orders tables are never altered)."""

    __tablename__ = "order_edit_log"
    __table_args__ = {"extend_existing": True}

    id = Column(Integer, primary_key=True, index=True, autoincrement=True, nullable=False)
    order_id = Column(Integer, nullable=False, index=True)
    stage = Column(String, nullable=False)  # the order's status when it was edited
    edited_by = Column(String, nullable=False)  # user id
    edited_by_name = Column(String, nullable=True)
    edited_by_role = Column(String, nullable=True)
    total_before = Column(Float, nullable=True)
    total_after = Column(Float, nullable=True)
    details = Column(Text, nullable=False)  # JSON: the lines (and header fields) before and after
    created_at = Column(DateTime(timezone=True), server_default=func.now())
