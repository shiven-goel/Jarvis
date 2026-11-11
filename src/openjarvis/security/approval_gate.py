"""Risk-tiered human approval for tool calls.

Upstream OpenJarvis accepts ``security.enforce_tool_confirmation`` but does not
apply it to channel or API turns. This gate is called by
:class:`openjarvis.tools._stubs.ToolExecutor` before every tool runs:

* **low risk** (``auto_tools``; ``http_request`` GET/HEAD) -> runs immediately
* **high risk** (anything else: send, delete, write, run code, ...)
    - owner on a chat channel -> Jarvis asks "yes <id> / no <id>" in that chat
      and waits (default 10 min); silence means **no**
    - owner on the dashboard/API -> waits in the approval queue
      (``GET /v1/jarvis/approvals``) for approve/deny
    - family member -> denied
* tools with ``requires_confirmation`` are always high risk.

The gate is a no-op until :func:`install_gate` is called (the household layer
does this when ``people.toml`` exists), so upstream behaviour is unchanged.
"""

from __future__ import annotations

import json
import logging
import re
import secrets
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, FrozenSet, List, Optional

logger = logging.getLogger(__name__)

LOW = "low"
HIGH = "high"

_SAFE_HTTP_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
_SECRET_KEY_RE = re.compile(
    r"(pass(word)?|secret|token|api[_-]?key|authorization|cookie|credential)", re.I
)


@dataclass
class PendingApproval:
    id: str
    tool: str
    summary: str
    person_id: str
    channel: str
    created_at: float
    expires_at: float
    status: str = "pending"  # pending | approved | denied | expired
    decided_by: str = ""
    _event: threading.Event = field(default_factory=threading.Event, repr=False)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "tool": self.tool,
            "summary": self.summary,
            "person_id": self.person_id,
            "channel": self.channel,
            "created_at": self.created_at,
            "expires_at": self.expires_at,
            "status": self.status,
            "decided_by": self.decided_by,
        }


def _redact(value: Any, depth: int = 0) -> Any:
    if depth > 4:
        return "…"
    if isinstance(value, dict):
        return {
            k: ("***" if _SECRET_KEY_RE.search(str(k)) else _redact(v, depth + 1))
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [_redact(v, depth + 1) for v in value[:20]]
    if isinstance(value, str) and len(value) > 300:
        return value[:300] + "…"
    return value


def summarize_args(params: Dict[str, Any], limit: int = 600) -> str:
    try:
        text = json.dumps(_redact(params), ensure_ascii=False, default=str)
    except Exception:
        text = str(params)
    return text if len(text) <= limit else text[:limit] + "…"


class ApprovalGate:
    """Holds the policy and the queue of pending approvals."""

    def __init__(
        self,
        *,
        auto_tools: FrozenSet[str] | set[str],
        always_ask_tools: FrozenSet[str] | set[str] = frozenset(),
        timeout_seconds: int = 600,
        enabled: bool = True,
        on_pending: Optional[Callable[[PendingApproval], None]] = None,
    ) -> None:
        self.enabled = enabled
        self.auto_tools = frozenset(auto_tools) - frozenset(always_ask_tools)
        self.always_ask_tools = frozenset(always_ask_tools)
        self.timeout_seconds = timeout_seconds
        self._on_pending = on_pending
        self._lock = threading.Lock()
        self._pending: Dict[str, PendingApproval] = {}
        self._history: List[PendingApproval] = []

    # -- policy -----------------------------------------------------------------

    def classify_risk(
        self, tool_name: str, params: Dict[str, Any], *, force_high: bool = False
    ) -> str:
        if force_high or tool_name in self.always_ask_tools:
            return HIGH
        if tool_name == "http_request":
            method = str(params.get("method", "GET")).upper()
            return LOW if method in _SAFE_HTTP_METHODS else HIGH
        return LOW if tool_name in self.auto_tools else HIGH

    # -- main entry -------------------------------------------------------------

    def check(
        self,
        tool_name: str,
        params: Dict[str, Any],
        *,
        agent_id: str = "",
        force_high: bool = False,
    ) -> Optional[str]:
        """Return None to allow the call, or a denial message for the model."""
        if not self.enabled:
            return None
        from openjarvis.household.context import current_request

        ctx = current_request()
        risk = self.classify_risk(tool_name, params, force_high=force_high)

        # Privacy: on a cloud-model turn, personal-data tools are refused so
        # their output never reaches the cloud model.
        if ctx is not None and ctx.extra.get("cloud_turn"):
            is_sensitive = ctx.extra.get("sensitive_tool")
            if callable(is_sensitive) and is_sensitive(tool_name):
                return (
                    f"'{tool_name}' reads private data, which only the local model "
                    "may see. Tell the user to ask again starting with /local "
                    "(or mention the email/calendar/file explicitly)."
                )

        if ctx is not None and not ctx.is_owner:
            allowed = ctx.extra.get("allowed_tools")
            if allowed is not None and tool_name not in allowed:
                return f"Tool '{tool_name}' is not available to {ctx.person_name}."
            if risk == HIGH:
                return (
                    f"'{tool_name}' needs the owner's approval and can't be run "
                    f"for {ctx.person_name}. Tell them you can't do that."
                )
            return None

        if risk == LOW:
            return None

        summary = summarize_args(params)
        pending = self._create(
            tool_name,
            summary,
            person_id=ctx.person_id if ctx else "owner",
            channel=ctx.channel if ctx else "api",
        )
        if ctx is not None and ctx.notify is not None:
            minutes = max(1, self.timeout_seconds // 60)
            try:
                ctx.notify(
                    f"🛡️ Approval needed [{pending.id}]\n"
                    f"I want to run: {tool_name}\n"
                    f"Details: {summary}\n"
                    f'Reply "yes {pending.id}" to allow or "no {pending.id}" '
                    f"to cancel. No reply in {minutes} min = cancelled."
                )
            except Exception:
                logger.exception("Could not send approval request")
        approved = self._wait(pending)
        if approved:
            logger.info("Approval %s granted for %s", pending.id, tool_name)
            return None
        reason = "timed out" if pending.status == "expired" else "denied by the owner"
        return f"Running '{tool_name}' was not approved ({reason}). Do not retry it."

    # -- queue ----------------------------------------------------------------------

    def _create(
        self, tool: str, summary: str, *, person_id: str, channel: str
    ) -> PendingApproval:
        now = time.time()
        with self._lock:
            pid = secrets.token_hex(2)
            while pid in self._pending:
                pid = secrets.token_hex(2)
            p = PendingApproval(
                id=pid,
                tool=tool,
                summary=summary,
                person_id=person_id,
                channel=channel,
                created_at=now,
                expires_at=now + self.timeout_seconds,
            )
            self._pending[pid] = p
        if self._on_pending is not None:
            try:
                self._on_pending(p)
            except Exception:
                logger.debug("on_pending hook failed", exc_info=True)
        return p

    def _wait(self, p: PendingApproval) -> bool:
        p._event.wait(timeout=max(0.0, p.expires_at - time.time()))
        with self._lock:
            if p.status == "pending":
                p.status = "expired"
            self._pending.pop(p.id, None)
            self._history.append(p)
            self._history = self._history[-200:]
        return p.status == "approved"

    def resolve(self, approval_id: str, approved: bool, *, by: str = "owner") -> bool:
        with self._lock:
            p = self._pending.get(approval_id.strip().lower())
            if p is None or p.status != "pending":
                return False
            p.status = "approved" if approved else "denied"
            p.decided_by = by
            p._event.set()
            return True

    def pending(self, person_id: Optional[str] = None) -> List[PendingApproval]:
        with self._lock:
            items = [p for p in self._pending.values() if p.status == "pending"]
        if person_id is not None:
            items = [p for p in items if p.person_id == person_id]
        return sorted(items, key=lambda p: p.created_at)

    def history(self) -> List[PendingApproval]:
        with self._lock:
            return list(self._history)

    # -- chat replies ---------------------------------------------------------------

    _REPLY_RE = re.compile(
        r"^\s*(yes|y|approve|allow|ok|no|n|deny|cancel|stop)\b[\s:#-]*([0-9a-f]{4})?\s*[.!]*\s*$",
        re.I,
    )

    def handle_reply(self, person_id: str, text: str) -> Optional[str]:
        """If *text* answers a pending approval, resolve it and return a confirmation.

        Plain "yes"/"no" works when exactly one approval is waiting for this person.
        Returns None when the message is not an approval reply.
        """
        m = self._REPLY_RE.match(text or "")
        if not m:
            return None
        mine = self.pending(person_id)
        if not mine:
            return None
        word, aid = m.group(1).lower(), (m.group(2) or "").lower()
        approve = word in ("yes", "y", "approve", "allow", "ok")
        if not aid:
            if len(mine) != 1:
                ids = ", ".join(p.id for p in mine)
                return (
                    f"Several approvals are waiting ({ids}). "
                    f'Reply e.g. "yes {mine[0].id}".'
                )
            aid = mine[0].id
        if not any(p.id == aid for p in mine):
            return f"No pending approval with id {aid}."
        self.resolve(aid, approve, by=person_id)
        return f"{'✅ Approved' if approve else '❌ Cancelled'} [{aid}]."


# -- process-wide installation ---------------------------------------------------------

_GATE: Optional[ApprovalGate] = None


def install_gate(gate: Optional[ApprovalGate]) -> None:
    global _GATE
    _GATE = gate


def get_gate() -> Optional[ApprovalGate]:
    return _GATE


def gate_active() -> bool:
    return _GATE is not None and _GATE.enabled


def gate_tool_call(
    tool_name: str,
    params: Dict[str, Any],
    *,
    agent_id: str = "",
    force_high: bool = False,
) -> Optional[str]:
    """Hook used by ToolExecutor. None = allowed."""
    gate = _GATE
    if gate is None:
        return None
    try:
        return gate.check(tool_name, params, agent_id=agent_id, force_high=force_high)
    except Exception as exc:  # fail closed
        logger.exception("Approval gate error")
        return f"Approval check failed ({exc}); '{tool_name}' was not run."


__all__ = [
    "ApprovalGate",
    "HIGH",
    "LOW",
    "PendingApproval",
    "gate_active",
    "gate_tool_call",
    "get_gate",
    "install_gate",
    "summarize_args",
]
