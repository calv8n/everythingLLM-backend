from __future__ import annotations

from dataclasses import dataclass, field
from threading import RLock

from ...config import settings


@dataclass
class SessionMemory:
    messages: list[dict[str, str]] = field(default_factory=list)


_sessions: dict[str, SessionMemory] = {}
_lock = RLock()


def append_message(session_id: str, role: str, content: str) -> list[dict[str, str]]:
    """Append a conversation message and retain only the configured newest messages."""
    with _lock:
        session = _sessions.setdefault(session_id, SessionMemory())
        session.messages.append({"role": role, "content": content})
        keep_count = max(0, settings.SESSION_MEMORY_MESSAGES)
        if keep_count == 0:
            session.messages.clear()
        else:
            del session.messages[:-keep_count]
        return [message.copy() for message in session.messages]


def get_messages(session_id: str) -> list[dict[str, str]]:
    """Return a snapshot of the retained conversation messages."""
    with _lock:
        session = _sessions.get(session_id)
        return [message.copy() for message in session.messages] if session else []


def clear_session(session_id: str) -> None:
    with _lock:
        _sessions.pop(session_id, None)
