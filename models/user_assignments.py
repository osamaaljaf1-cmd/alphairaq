from core.database import Base
from sqlalchemy import Column, Integer, String


class User_assignments(Base):
    __tablename__ = "user_assignments"
    __table_args__ = {"extend_existing": True}

    id = Column(Integer, primary_key=True, index=True, autoincrement=True, nullable=False)
    user_id = Column(String, nullable=False)
    manager_rep_id = Column(Integer, nullable=False)
    assigned_rep_id = Column(Integer, nullable=False)
    assignment_type = Column(String, nullable=False)