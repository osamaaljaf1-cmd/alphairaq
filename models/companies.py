"""The companies we represent, the items (products) of each, and the staff who work for each.
New tables of their own: the products table is not migrated."""
from core.database import Base
from sqlalchemy import Boolean, Column, Integer, String, UniqueConstraint


class Companies(Base):
    __tablename__ = "companies"
    __table_args__ = {"extend_existing": True}

    id = Column(Integer, primary_key=True, index=True, autoincrement=True, nullable=False)
    name = Column(String, nullable=False)
    is_active = Column(Boolean, nullable=False, default=True)


class Company_products(Base):
    """Which company an item belongs to: one company per item."""

    __tablename__ = "company_products"
    __table_args__ = {"extend_existing": True}

    id = Column(Integer, primary_key=True, index=True, autoincrement=True, nullable=False)
    company_id = Column(Integer, nullable=False, index=True)
    product_id = Column(Integer, nullable=False, unique=True)


class Company_staff(Base):
    """Who works for a company (a rep, a manager, any account); one person may work for several."""

    __tablename__ = "company_staff"
    __table_args__ = (UniqueConstraint("company_id", "user_id", name="uq_company_staff"), {"extend_existing": True})

    id = Column(Integer, primary_key=True, index=True, autoincrement=True, nullable=False)
    company_id = Column(Integer, nullable=False, index=True)
    user_id = Column(String, nullable=False, index=True)
