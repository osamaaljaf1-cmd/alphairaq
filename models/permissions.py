from core.database import Base
from sqlalchemy import Boolean, Column, Integer, String


class Permissions(Base):
    __tablename__ = "permissions"
    __table_args__ = {"extend_existing": True}

    id = Column(Integer, primary_key=True, index=True, autoincrement=True, nullable=False)
    role = Column(String, nullable=False)
    page = Column(String, nullable=False)
    can_view = Column(Boolean, nullable=True, default=False, server_default='false')
    can_add = Column(Boolean, nullable=True, default=False, server_default='false')
    can_edit = Column(Boolean, nullable=True, default=False, server_default='false')
    can_delete = Column(Boolean, nullable=True, default=False, server_default='false')
    can_import = Column(Boolean, nullable=True, default=False, server_default='false')
    can_export = Column(Boolean, nullable=True, default=False, server_default='false')