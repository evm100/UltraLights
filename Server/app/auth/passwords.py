"""Password hashing helpers."""
from passlib.context import CryptContext


_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def hash_password(password: str) -> str:
    """Hash ``password`` using a strong adaptive hash."""

    if not isinstance(password, str):
        raise TypeError("password must be a string")
    return _context.hash(password)


def verify_password(password: str, hashed_password: str) -> bool:
    """Return ``True`` if ``password`` matches ``hashed_password``."""

    if not password or not hashed_password:
        return False
    try:
        return _context.verify(password, hashed_password)
    except ValueError:
        return False


def needs_rehash(hashed_password: str) -> bool:
    """Return ``True`` if the hash should be upgraded."""

    if not hashed_password:
        return True
    return _context.needs_update(hashed_password)


__all__ = ["hash_password", "verify_password", "needs_rehash"]
