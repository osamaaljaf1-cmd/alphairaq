from core.database import Base
from sqlalchemy import Column, Integer, String


class Areas(Base):
    __tablename__ = "areas"
    __table_args__ = {"extend_existing": True}

    id = Column(Integer, primary_key=True, index=True, autoincrement=True, nullable=False)
    name = Column(String, nullable=False)
    parent_area_id = Column(Integer, nullable=True)