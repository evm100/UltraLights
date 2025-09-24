"""Helpers for throttling repeated authentication attempts."""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from threading import Lock
from typing import Callable, Deque, Dict, Optional

from ..config import settings


_TimeProvider = Callable[[], datetime]


def _default_time_provider() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class RateLimitState:
    """Simple container describing a rate-limit state."""

    blocked: bool
    retry_after: int = 0


class LoginRateLimiter:
    """Track failed login attempts and enforce a cooldown window."""

    def __init__(
        self,
        *,
        max_attempts: int,
        window_seconds: int,
        block_seconds: int,
        time_provider: Optional[_TimeProvider] = None,
    ) -> None:
        if max_attempts <= 0:
            raise ValueError("max_attempts must be greater than zero")
        if window_seconds <= 0:
            raise ValueError("window_seconds must be greater than zero")
        if block_seconds <= 0:
            raise ValueError("block_seconds must be greater than zero")

        self._max_attempts = max_attempts
        self._window = timedelta(seconds=window_seconds)
        self._block = timedelta(seconds=block_seconds)
        self._time_provider: _TimeProvider = time_provider or _default_time_provider
        self._attempts: Dict[str, Deque[datetime]] = {}
        self._blocked_until: Dict[str, datetime] = {}
        self._lock = Lock()

    def _now(self) -> datetime:
        return self._time_provider()

    def _prune_attempts(self, identifier: str, *, now: datetime) -> None:
        attempts = self._attempts.get(identifier)
        if not attempts:
            return
        threshold = now - self._window
        while attempts and attempts[0] < threshold:
            attempts.popleft()
        if not attempts:
            self._attempts.pop(identifier, None)

    def _status_locked(self, identifier: str, *, now: datetime) -> RateLimitState:
        blocked_until = self._blocked_until.get(identifier)
        if blocked_until and blocked_until > now:
            retry_after = int((blocked_until - now).total_seconds())
            return RateLimitState(blocked=True, retry_after=max(retry_after, 1))

        if blocked_until and blocked_until <= now:
            self._blocked_until.pop(identifier, None)

        self._prune_attempts(identifier, now=now)
        return RateLimitState(blocked=False, retry_after=0)

    def status(self, identifier: str) -> RateLimitState:
        """Return the current rate-limit status for ``identifier``."""

        with self._lock:
            return self._status_locked(identifier, now=self._now())

    def register_failure(self, identifier: str) -> RateLimitState:
        """Record a failed attempt and return the updated status."""

        with self._lock:
            now = self._now()
            state = self._status_locked(identifier, now=now)
            if state.blocked:
                return state

            attempts = self._attempts.setdefault(identifier, deque())
            attempts.append(now)
            self._prune_attempts(identifier, now=now)
            if len(attempts) >= self._max_attempts:
                blocked_until = now + self._block
                self._blocked_until[identifier] = blocked_until
                attempts.clear()
                retry_after = int(self._block.total_seconds())
                return RateLimitState(blocked=True, retry_after=max(retry_after, 1))

            return RateLimitState(blocked=False, retry_after=0)

    def register_success(self, identifier: str) -> None:
        """Clear throttling state after a successful login."""

        with self._lock:
            self._attempts.pop(identifier, None)
            self._blocked_until.pop(identifier, None)


_login_rate_limiter: LoginRateLimiter | None = None


def get_login_rate_limiter() -> LoginRateLimiter:
    """Return the singleton rate limiter configured from settings."""

    if _login_rate_limiter is None:  # pragma: no cover - defensive
        reset_login_rate_limiter()
    assert _login_rate_limiter is not None
    return _login_rate_limiter


def reset_login_rate_limiter(limiter: Optional[LoginRateLimiter] = None) -> None:
    """Replace the global limiter, primarily for startup and tests."""

    global _login_rate_limiter
    if limiter is not None:
        _login_rate_limiter = limiter
        return

    _login_rate_limiter = LoginRateLimiter(
        max_attempts=settings.LOGIN_ATTEMPT_LIMIT,
        window_seconds=settings.LOGIN_ATTEMPT_WINDOW,
        block_seconds=settings.LOGIN_BACKOFF_SECONDS,
    )


__all__ = ["LoginRateLimiter", "RateLimitState", "get_login_rate_limiter", "reset_login_rate_limiter"]

