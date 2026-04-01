from core.database import Base
from sqlalchemy import Column, Integer, String


class App_users(Base):
    __tablename__ = "app_users"
    __table_args__ = {"extend_existing": True}

    id = Column(Integer, primary_key=True, index=True, autoincrement=True, nullable=False)
    user_id = Column(String, nullable=False)
    name = Column(String, nullable=False)
    email = Column(String, nullable=False)
    password_hash = Column(String, nullable=False)
    role = Column(String, nullable=False)
    status = Column(String, nullable=False)