from core.database import Base
from sqlalchemy import Column, DateTime, Integer, String


class Daily_reports(Base):
    __tablename__ = "daily_reports"
    __table_args__ = {"extend_existing": True}

    id = Column(Integer, primary_key=True, index=True, autoincrement=True, nullable=False)
    user_id = Column(String, nullable=False)
    rep_name = Column(String, nullable=True)
    report_date = Column(String, nullable=True)
    total_visits = Column(Integer, nullable=True)
    completed_count = Column(Integer, nullable=True)
    cancelled_count = Column(Integer, nullable=True)
    deal_count = Column(Integer, nullable=True)
    quota_count = Column(Integer, nullable=True)
    followup_count = Column(Integer, nullable=True)
    report_data = Column(String, nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=True)