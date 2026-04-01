from core.database import Base
from sqlalchemy import Boolean, Column, DateTime, Integer, String


class Returns(Base):
    __tablename__ = "returns"
    __table_args__ = {"extend_existing": True}

    id = Column(Integer, primary_key=True, index=True, autoincrement=True, nullable=False)
    user_id = Column(String, nullable=False)
    order_id = Column(Integer, nullable=True)
    pharmacy_id = Column(Integer, nullable=True)
    doctor_id = Column(Integer, nullable=True)
    representative_id = Column(Integer, nullable=True)
    product_id = Column(Integer, nullable=True)
    quantity = Column(Integer, nullable=True)
    reason = Column(String, nullable=False)
    status = Column(String, nullable=True)
    whatsapp_sent = Column(Boolean, nullable=True)
    invoice_number = Column(String, nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=True)