"""Login security: the login history, and the moment before which an account's sessions no longer count
(set when its password changes or the admin logs it out everywhere). New tables: app_users is not migrated."""
from core.database import Base
from sqlalchemy import Boolean, Column, DateTime, Integer, String


class Login_events(Base):
    """One login attempt: who (the name typed, and the account when it exists), whether it worked and
    why not, from which address and device."""

    __tablename__ = "login_events"
    __table_args__ = {"extend_existing": True}

    id = Column(Integer, primary_key=True, index=True, autoincrement=True, nullable=False)
    account = Column(String, nullable=False, index=True)
    user_id = Column(String, nullable=True, index=True)
    ok = Column(Boolean, nullable=False, default=False)
    reason = Column(String, nullable=True)  # ok | wrong_password | unknown_user | locked | blocked | inactive
    ip = Column(String, nullable=True)
    user_agent = Column(String, nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, index=True)


class Session_resets(Base):
    """Sessions (tokens) of this account issued before ``after`` are refused."""

    __tablename__ = "session_resets"
    __table_args__ = {"extend_existing": True}

    id = Column(Integer, primary_key=True, index=True, autoincrement=True, nullable=False)
    user_id = Column(String, nullable=False, unique=True, index=True)
    after = Column(DateTime(timezone=True), nullable=False)
