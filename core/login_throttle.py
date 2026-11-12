"""Brute-force protection for the password login.

Failed logins are counted per account name and per client address in a sliding window. Too many
failures lock that account name (or that address) out for the rest of the window, whatever password
is tried next, so guessing a password is no longer possible at machine speed. A success clears the
account's counter. It lives in the process's memory: simple, nothing to configure, and a restart
only forgets the counters.
"""
import time
from collections import defaultdict, deque
from typing import Deque, Dict, Optional

from fastapi import Request

WINDOW_SECONDS = 15 * 60
MAX_FAILURES_PER_ACCOUNT = 10
MAX_FAILURES_PER_ADDRESS = 60  # an office / mobile network shares one address: keep this generous

_failures: Dict[str, Deque[float]] = defaultdict(deque)


def client_address(request: Request) -> str:
    """The caller's address: the last X-Forwarded-For entry (added by the platform's proxy, so the
    caller cannot forge it) or the socket address."""
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        last = forwarded.split(",")[-1].strip()
        if last:
            return last
    return request.client.host if request.client else "unknown"


def _recent(key: str, now: float) -> Deque[float]:
    q = _failures[key]
    while q and now - q[0] > WINDOW_SECONDS:
        q.popleft()
    if not q:
        _failures.pop(key, None)
    return q


def seconds_locked(account: str, address: str) -> int:
    """0 when the login may be tried, else the seconds until it may be again."""
    now = time.monotonic()
    wait = 0
    for key, limit in ((f"a:{account}", MAX_FAILURES_PER_ACCOUNT), (f"i:{address}", MAX_FAILURES_PER_ADDRESS)):
        q = _recent(key, now)
        if len(q) >= limit:
            wait = max(wait, int(WINDOW_SECONDS - (now - q[-limit])) + 1)
    return wait


def record_failure(account: str, address: str) -> None:
    now = time.monotonic()
    if len(_failures) > 20000:  # a flood of random names must not grow memory without end
        _failures.clear()
    _failures[f"a:{account}"].append(now)
    _failures[f"i:{address}"].append(now)


def record_success(account: str) -> None:
    _failures.pop(f"a:{account}", None)


def normalise_account(value: Optional[str]) -> str:
    return (value or "").strip().lower()
