from core.database import Base
from sqlalchemy import Column, Float, Integer, String


class Representatives(Base):
    __tablename__ = "representatives"
    __table_args__ = {"extend_existing": True}

    id = Column(Integer, primary_key=True, index=True, autoincrement=True, nullable=False)
    user_id = Column(String, nullable=False)
    name = Column(String, nullable=False)
    phone = Column(String, nullable=True)
    region = Column(String, nullable=True)
    monthly_target = Column(Float, nullable=True)
    role = Column(String, nullable=False)