"""Which branch / governorate / area a staff member (manager, accountant, printing, delivery …)
works in. A rep's areas stay in representative_areas; this is for everyone else. A user with no
row here is not restricted by area, the same as before branches existed."""
from core.database import Base
from sqlalchemy import Column, Integer, String, UniqueConstraint


class Staff_areas(Base):
    __tablename__ = "staff_areas"
    __table_args__ = (UniqueConstraint("user_id", "area_id", name="uq_staff_areas_user_area"), {"extend_existing": True})

    id = Column(Integer, primary_key=True, index=True, autoincrement=True, nullable=False)
    user_id = Column(String, nullable=False, index=True)
    area_id = Column(Integer, nullable=False)
