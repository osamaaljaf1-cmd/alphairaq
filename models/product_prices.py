"""An item's price in a price list (سعر أربيل …). An item with no row in a list sells at its own
products.price there, so the main list needs no rows at all."""
from core.database import Base
from sqlalchemy import Column, Float, Integer, UniqueConstraint


class Product_prices(Base):
    __tablename__ = "product_prices"
    __table_args__ = (UniqueConstraint("price_list_id", "product_id", name="uq_product_prices_list_product"), {"extend_existing": True})

    id = Column(Integer, primary_key=True, index=True, autoincrement=True, nullable=False)
    price_list_id = Column(Integer, nullable=False, index=True)
    product_id = Column(Integer, nullable=False, index=True)
    price = Column(Float, nullable=False)
