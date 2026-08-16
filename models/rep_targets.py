from core.database import Base
from sqlalchemy import Column, DateTime, Float, Integer, func


class RepTargets(Base):
    __tablename__ = "rep_targets"
    __table_args__ = {"extend_existing": True}

    id = Column(Integer, primary_key=True, index=True, autoincrement=True, nullable=False)
    representative_id = Column(Integer, nullable=False)
    year = Column(Integer, nullable=False)
    month = Column(Integer, nullable=False)
    product_id = Column(Integer, nullable=True)  # NULL row = the overall monthly amount target
    target_qty = Column(Float, nullable=True)
    target_amount = Column(Float, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
