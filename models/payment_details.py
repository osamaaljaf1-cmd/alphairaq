from core.database import Base
from sqlalchemy import Column, Float, Integer, String


class PaymentDetails(Base):
    __tablename__ = "payment_details"
    __table_args__ = {"extend_existing": True}

    id = Column(Integer, primary_key=True, index=True, autoincrement=True, nullable=False)
    user_id = Column(String, nullable=False)
    payment_id = Column(Integer, nullable=False, index=True)
    debt_id = Column(Integer, nullable=False, index=True)
    amount_applied = Column(Float, nullable=False)