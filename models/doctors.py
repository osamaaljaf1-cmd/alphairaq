from core.database import Base
from sqlalchemy import Column, Integer, String


class Doctors(Base):
    __tablename__ = "doctors"
    __table_args__ = {"extend_existing": True}

    id = Column(Integer, primary_key=True, index=True, autoincrement=True, nullable=False)
    name = Column(String, nullable=False)
    customer_number = Column(String, nullable=True)
    specialty = Column(String, nullable=True)
    phone = Column(String, nullable=True)
    hospital = Column(String, nullable=True)
    area = Column(String, nullable=True)
    representative_id = Column(Integer, nullable=True)
    status = Column(String, nullable=True)
    notes = Column(String, nullable=True)