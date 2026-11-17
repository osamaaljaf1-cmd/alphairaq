from core.database import Base
from sqlalchemy import Column, DateTime, Integer, String, UniqueConstraint, func


class Order_submissions(Base):
    """One row per new order placed through /api/v1/order-submit: the key the phone made for that
    order, so the same order sent twice (a double tap, a retry) is made only once."""

    __tablename__ = "order_submissions"
    __table_args__ = (UniqueConstraint("client_request_id", "user_id", name="uq_order_submission_key"), {"extend_existing": True})

    id = Column(Integer, primary_key=True, index=True, autoincrement=True, nullable=False)
    client_request_id = Column(String, nullable=False, index=True)
    user_id = Column(String, nullable=False)
    order_id = Column(Integer, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
