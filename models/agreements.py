from core.database import Base
from sqlalchemy import Column, Date, Float, Integer, String


class Agreements(Base):
    __tablename__ = "agreements"
    __table_args__ = {"extend_existing": True}

    id = Column(Integer, primary_key=True, index=True, autoincrement=True, nullable=False)
    doctor_id = Column(Integer, nullable=False)
    pharmacy_id = Column(Integer, nullable=False)
    product_id = Column(Integer, nullable=False)

    agreed_price = Column(Float, nullable=False)
    bonus_value = Column(Float, nullable=False)
    bonus_type = Column(String, nullable=True)

    # New fields from user spec
    agreed_quantity = Column(Float, nullable=True)
    agreed_amount = Column(Float, nullable=True)
    agreed_bonus = Column(Float, nullable=True)

    bonus_qty_threshold = Column(Integer, nullable=True)
    bonus_qty = Column(Integer, nullable=True)

    start_date = Column(String, nullable=True)
    end_date = Column(String, nullable=True)
    status = Column(String, nullable=True, default="active")
    notes = Column(String, nullable=True)
    order_id = Column(Integer, nullable=True)