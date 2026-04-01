from core.database import Base
from sqlalchemy import Boolean, Column, Float, Integer, String


class Products(Base):
    __tablename__ = "products"
    __table_args__ = {"extend_existing": True}

    id = Column(Integer, primary_key=True, index=True, autoincrement=True, nullable=False)
    name = Column(String, nullable=False)
    code = Column(String, nullable=True)
    price = Column(Float, nullable=False)
    category = Column(String, nullable=True)
    unit = Column(String, nullable=True)
    stock_quantity = Column(Integer, nullable=True)
    is_active = Column(Boolean, nullable=True, default=True, server_default='true')