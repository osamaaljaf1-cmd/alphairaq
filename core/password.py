"""
Password hashing utilities using bcrypt.

Provides bcrypt-based password hashing with backward compatibility
for legacy SHA-256 hashed passwords. Legacy passwords are automatically
upgraded to bcrypt on successful login verification.
"""

import hashlib
import logging

import bcrypt

logger = logging.getLogger(__name__)


def hash_password(password: str) -> str:
    """Hash a password using bcrypt.

    Args:
        password: The plain-text password to hash.

    Returns:
        A bcrypt hash string (starts with '$2b$').
    """
    salt = bcrypt.gensalt(rounds=12)
    hashed = bcrypt.hashpw(password.encode("utf-8"), salt)
    return hashed.decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    """Verify a password against its hash.

    Supports bcrypt hashes (preferred), legacy SHA-256 salt:hash format,
    and plain SHA-256 hex hashes.

    Args:
        password: The plain-text password to verify.
        password_hash: The stored hash (bcrypt, legacy sha256, or plain sha256).

    Returns:
        True if the password matches, False otherwise.
    """
    try:
        if not password_hash:
            return False

        # Check if it's a bcrypt hash (starts with $2b$, $2a$, or $2y$)
        if password_hash.startswith(("$2b$", "$2a$", "$2y$")):
            return bcrypt.checkpw(
                password.encode("utf-8"),
                password_hash.encode("utf-8"),
            )

        # Legacy SHA-256 format: "salt:hash"
        if ":" in password_hash:
            salt, stored_hash = password_hash.split(":", 1)
            computed_hash = hashlib.sha256(f"{salt}{password}".encode()).hexdigest()
            return computed_hash == stored_hash

        # Plain SHA-256 hex hash (64-character hex string, no salt)
        if len(password_hash) == 64 and all(c in "0123456789abcdef" for c in password_hash.lower()):
            computed_hash = hashlib.sha256(password.encode("utf-8")).hexdigest()
            return computed_hash.lower() == password_hash.lower()

        return False
    except Exception as e:
        logger.error(f"Password verification error: {e}")
        return False


def is_legacy_hash(password_hash: str) -> bool:
    """Check if a password hash is in a legacy format (SHA-256).

    Args:
        password_hash: The stored hash string.

    Returns:
        True if the hash is in legacy format and should be upgraded to bcrypt.
    """
    if not password_hash:
        return False
    # Bcrypt hashes start with $2b$, $2a$, or $2y$
    if password_hash.startswith(("$2b$", "$2a$", "$2y$")):
        return False
    # Legacy format: "salt:hash" or plain 64-char SHA-256 hex
    if ":" in password_hash:
        return True
    if len(password_hash) == 64 and all(c in "0123456789abcdef" for c in password_hash.lower()):
        return True
    return False