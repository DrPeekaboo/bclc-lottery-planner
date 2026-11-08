"""Per-visitor data kept in memory only. Nothing uploaded is written to disk.

A visitor is identified by a random, HttpOnly cookie. Idle sessions expire (default one hour)
and their uploads, plans and running calculations are discarded.
"""
from dataclasses import dataclass, field
import re
import secrets
import threading
import time

COOKIE = "bclc_sid"
_SID = re.compile(r"[A-Za-z0-9_-]{40,64}")


@dataclass
class Session:
    sid: str
    created: float
    last_seen: float
    history: dict = field(default_factory=dict)    # game key -> {"draws", "name", "sha256", "summary"}
    receipts: dict = field(default_factory=dict)   # game key -> {"lines", "name", "plays"}


class SessionStore:
    def __init__(self, idle_seconds: int = 3600, max_sessions: int = 2000, on_expire=None):
        self.idle = idle_seconds
        self.max_sessions = max_sessions
        self.on_expire = on_expire
        self._sessions: dict[str, Session] = {}
        self._lock = threading.Lock()

    def get_or_create(self, sid: str | None) -> tuple[Session, bool]:
        now = time.time()
        self.purge(now)
        with self._lock:
            if sid and _SID.fullmatch(sid) and sid in self._sessions:
                session = self._sessions[sid]
                session.last_seen = now
                return session, False
            if len(self._sessions) >= self.max_sessions:
                # Drop the least recently used visitor rather than refusing new ones.
                oldest = min(self._sessions.values(), key=lambda s: s.last_seen)
                self._drop(oldest.sid)
            session = Session(secrets.token_urlsafe(32), now, now)
            self._sessions[session.sid] = session
            return session, True

    def delete(self, sid: str) -> None:
        with self._lock:
            self._drop(sid)

    def purge(self, now: float | None = None) -> None:
        now = now or time.time()
        with self._lock:
            for sid in [s.sid for s in self._sessions.values() if now - s.last_seen > self.idle]:
                self._drop(sid)

    def count(self) -> int:
        with self._lock:
            return len(self._sessions)

    def _drop(self, sid: str) -> None:
        session = self._sessions.pop(sid, None)
        if session and self.on_expire:
            self.on_expire(sid)
