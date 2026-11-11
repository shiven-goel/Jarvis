"""Per-request context: who is talking to Jarvis right now, and how to reach them.

Set by the household channel wiring for every inbound message and read by the
approval gate (to ask the right person) and the privacy router. Kept free of
other OpenJarvis imports so security code can depend on it without cycles.
"""

from __future__ import annotations

import contextvars
from dataclasses import dataclass, field
from typing import Any, Callable, Optional


@dataclass
class RequestContext:
    """Identity and reply path for the request being processed."""

    person_id: str
    person_name: str
    role: str  # "owner" | "family"
    channel: str = ""  # "slack", "whatsapp_baileys", "api", ...
    conversation_id: str = ""
    notify: Optional[Callable[[str], None]] = None  # send a message back to them
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def is_owner(self) -> bool:
        return self.role == "owner"


_current: contextvars.ContextVar[Optional[RequestContext]] = contextvars.ContextVar(
    "openjarvis_household_request", default=None
)


def current_request() -> Optional[RequestContext]:
    """Return the request context for the running task, if any."""
    return _current.get()


def set_request(ctx: Optional[RequestContext]) -> contextvars.Token:
    return _current.set(ctx)


def reset_request(token: contextvars.Token) -> None:
    _current.reset(token)


__all__ = ["RequestContext", "current_request", "set_request", "reset_request"]
