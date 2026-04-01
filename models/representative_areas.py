from core.database import Base
from sqlalchemy import Column, Integer


class Representative_areas(Base):
    __tablename__ = "representative_areas"
    __table_args__ = {"extend_existing": True}

    id = Column(Integer, primary_key=True, index=True, autoincrement=True, nullable=False)
    representative_id = Column(Integer, nullable=False)
    area_id = Column(Integer, nullable=False)