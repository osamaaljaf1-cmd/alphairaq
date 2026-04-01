from core.database import Base
from sqlalchemy import Column, DateTime, Integer, String


class Messages(Base):
    __tablename__ = "messages"
    __table_args__ = {"extend_existing": True}

    id = Column(Integer, primary_key=True, index=True, autoincrement=True, nullable=False)
    user_id = Column(String, nullable=False)
    doctor_id = Column(Integer, nullable=False)
    pharmacy_id = Column(Integer, nullable=True)
    product_id = Column(Integer, nullable=True)
    return_id = Column(Integer, nullable=True)
    message_type = Column(String, nullable=False)
    message_content = Column(String, nullable=False)
    doctor_phone = Column(String, nullable=True)
    status = Column(String, nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=True)