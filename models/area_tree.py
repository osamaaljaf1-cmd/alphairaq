"""Branches, governorates and areas as one tree (areas.parent_area_id), with what each level is and
which price list its customers get. Kept in tables of their own: the areas table is not migrated."""
from core.database import Base
from sqlalchemy import Column, Integer, String


class Price_lists(Base):
    """A price list (سعر بغداد، سعر أربيل …): a product's price per list comes in the next phase."""

    __tablename__ = "price_lists"
    __table_args__ = {"extend_existing": True}

    id = Column(Integer, primary_key=True, index=True, autoincrement=True, nullable=False)
    name = Column(String, nullable=False)
    code = Column(String, nullable=True, unique=True)
    sort = Column(Integer, nullable=True, default=0)


class Area_nodes(Base):
    """What an area of the tree is: the main branch (hq), a branch, a governorate or an area, the price
    list its customers get (None = the same as the level above it), and the Phenix branch number."""

    __tablename__ = "area_nodes"
    __table_args__ = {"extend_existing": True}

    id = Column(Integer, primary_key=True, index=True, autoincrement=True, nullable=False)
    area_id = Column(Integer, nullable=False, unique=True, index=True)
    kind = Column(String, nullable=False, default="area")  # hq | branch | governorate | area
    price_list_id = Column(Integer, nullable=True)
    phenix_branch = Column(Integer, nullable=True)
