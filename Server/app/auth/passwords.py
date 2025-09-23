"""Backward compatible wrappers around :mod:`app.auth.security`."""

from .security import hash_password, needs_rehash, verify_password


__all__ = ["hash_password", "verify_password", "needs_rehash"]
