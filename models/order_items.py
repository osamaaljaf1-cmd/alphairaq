from core.database import Base
from sqlalchemy import Boolean, Column, Float, Integer, String


class Order_items(Base):
    __tablename__ = "order_items"
    __table_args__ = {"extend_existing": True}

    id = Column(Integer, primary_key=True, index=True, autoincrement=True, nullable=False)
    user_id = Column(String, nullable=False)
    order_id = Column(Integer, nullable=False)
    product_id = Column(Integer, nullable=False)
    quantity = Column(Integer, nullable=False)
    unit_price = Column(Float, nullable=False)
    bonus_amount = Column(Float, nullable=True)
    agreement_id = Column(Integer, nullable=True)
    gift_qty = Column(Integer, nullable=True)
    has_deal = Column(Boolean, nullable=True)