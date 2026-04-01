from core.database import Base
from sqlalchemy import Column, Integer, String


class Pharmacies(Base):
    __tablename__ = "pharmacies"
    __table_args__ = {"extend_existing": True}

    id = Column(Integer, primary_key=True, index=True, autoincrement=True, nullable=False)
    name = Column(String, nullable=False)
    customer_number = Column(String, nullable=True)
    address = Column(String, nullable=True)
    phone = Column(String, nullable=True)
    area = Column(String, nullable=True)
    contact_person = Column(String, nullable=True)
    representative_id = Column(Integer, nullable=True)
    status = Column(String, nullable=True)