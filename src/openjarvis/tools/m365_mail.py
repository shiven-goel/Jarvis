"""Agent tools for Microsoft 365 / Outlook mail (multi-account, read-only).

[jarvis-fork] Backed by :mod:`openjarvis.connectors.m365_mail`. All four tools
only read; none can send, move or delete mail.
"""

from __future__ import annotations

from typing import Any, Optional

from openjarvis.core.registry import ToolRegistry
from openjarvis.core.types import ToolResult
from openjarvis.tools._stubs import BaseTool, ToolSpec


def _spec(name: str) -> ToolSpec:
    from openjarvis.connectors.m365_mail import M365MailConnector

    for s in M365MailConnector(mail=_NullMail()).mcp_tools():
        if s.name == name:
            return s
    raise KeyError(name)


class _NullMail:  # avoids touching disk just to build specs
    pass


class _M365Tool(BaseTool):
    is_local = False  # talks to Microsoft Graph
    tool_id = ""
    _mail: Optional[Any] = None

    def __init__(self, mail: Optional[Any] = None) -> None:
        self._mail = mail

    @property
    def spec(self) -> ToolSpec:
        return _spec(self.tool_id)

    def _m(self):
        if self._mail is None:
            from openjarvis.connectors.m365_mail import M365Mail

            self._mail = M365Mail()
        return self._mail

    def execute(self, **params: Any) -> ToolResult:
        from openjarvis.connectors.m365_mail import M365Error

        try:
            text, meta = self._run(**params)
            return ToolResult(
                tool_name=self.tool_id, content=text, success=True, metadata=meta
            )
        except M365Error as exc:
            return ToolResult(tool_name=self.tool_id, content=str(exc), success=False)
        except Exception as exc:  # network etc.
            return ToolResult(
                tool_name=self.tool_id,
                content=f"Microsoft 365 error: {exc}",
                success=False,
            )

    def _run(self, **params: Any):  # pragma: no cover - abstract
        raise NotImplementedError


@ToolRegistry.register("m365_accounts")
class M365AccountsTool(_M365Tool):
    tool_id = "m365_accounts"

    def _run(self, **params: Any):
        accts = self._m().store.accounts()
        if not accts:
            return "No Microsoft 365 accounts connected.", {"count": 0}
        lines = [
            f"{alias}: {a.get('address', '?')} ({a.get('kind', 'mailbox')})"
            for alias, a in sorted(accts.items())
        ]
        return "\n".join(lines), {"count": len(accts)}


@ToolRegistry.register("m365_mail_recent")
class M365MailRecentTool(_M365Tool):
    tool_id = "m365_mail_recent"

    def _run(self, **params: Any):
        m = self._m()
        items = m.recent(
            params.get("account") or None,
            top=int(params.get("top", 10) or 10),
            unread_only=bool(params.get("unread_only", False)),
        )
        return m.format_list(items), {"count": len(items)}


@ToolRegistry.register("m365_mail_search")
class M365MailSearchTool(_M365Tool):
    tool_id = "m365_mail_search"

    def _run(self, **params: Any):
        m = self._m()
        items = m.search(
            str(params.get("query", "")),
            params.get("account") or None,
            top=int(params.get("top", 10) or 10),
        )
        return m.format_list(items), {"count": len(items)}


@ToolRegistry.register("m365_mail_read")
class M365MailReadTool(_M365Tool):
    tool_id = "m365_mail_read"

    def _run(self, **params: Any):
        m = self._m()
        msg = m.read(str(params.get("account", "")), str(params.get("message_id", "")))
        return m.format_message(msg), {"account": msg.get("_account")}


__all__ = [
    "M365AccountsTool",
    "M365MailReadTool",
    "M365MailRecentTool",
    "M365MailSearchTool",
]
