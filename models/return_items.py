from core.database import Base
from sqlalchemy import Column, Float, Integer, String


class Return_items(Base):
    __tablename__ = "return_items"
    __table_args__ = {"extend_existing": True}

    id = Column(Integer, primary_key=True, index=True, autoincrement=True, nullable=False)
    user_id = Column(String, nullable=False)
    return_id = Column(Integer, nullable=False)
    product_id = Column(Integer, nullable=False)
    quantity = Column(Integer, nullable=False)
    unit_price = Column(Float, nullable=True)
    agreement_id = Column(Integer, nullable=True)