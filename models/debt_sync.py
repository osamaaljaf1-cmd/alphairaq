from core.database import Base
from sqlalchemy import Column, DateTime, Integer, String, Text, func


class DebtSyncRuns(Base):
    """One applied synchronization of the debts from the accounting program. Kept so a
    run can be undone: every change it made is stored in DebtSyncChanges with the state
    the debt had before."""

    __tablename__ = "debt_sync_runs"
    __table_args__ = {"extend_existing": True}

    id = Column(Integer, primary_key=True, index=True, autoincrement=True, nullable=False)
    source = Column(String, nullable=False, default="phenix")
    status = Column(String, nullable=False, default="applied")  # applied | undone
    summary = Column(Text, nullable=True)  # JSON: the counts of the run
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    undone_at = Column(DateTime(timezone=True), nullable=True)


class DebtSyncChanges(Base):
    __tablename__ = "debt_sync_changes"
    __table_args__ = {"extend_existing": True}

    id = Column(Integer, primary_key=True, index=True, autoincrement=True, nullable=False)
    run_id = Column(Integer, nullable=False, index=True)
    debt_id = Column(Integer, nullable=False, index=True)
    action = Column(String, nullable=False)  # insert | update | close
    before = Column(Text, nullable=True)  # JSON of the debt before the change (null for insert)
