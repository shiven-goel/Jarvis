"""Privacy-aware model routing ("sensitive stuff stays local").

Rules, in order:

1. Family member -> the family model (default: local); never any personal-data
   tools.
2. Owner, message is about private data (keywords) **or** the conversation
   touched private data in the last ``sticky_turns`` turns -> local model, all
   tools.
3. Owner, explicit ``/local``, ``/claude`` or ``/gemini`` prefix -> that model
   (cloud prefixes are ignored when rule 2 applies).
4. Owner, simple request (complexity below threshold) -> local model.
5. Otherwise -> cloud model, **with personal-data tools removed** and with
   earlier private turns dropped from the history it sees.

Upstream routes only by complexity; this adds the privacy dimension.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Iterable, List, Optional, Sequence

from openjarvis.household.config import HouseholdConfig, Person

_PREFIX_RE = re.compile(r"^\s*/(local|claude|cloud|gemini)\b[:\s]*", re.I)


@dataclass
class RouteDecision:
    model: str
    private: bool  # True -> local-only turn; mark the turn private
    reason: str
    query: str  # message with any routing prefix removed
    strip_sensitive_tools: bool = False
    drop_private_history: bool = False
    tags: List[str] = field(default_factory=list)


class PrivacyRouter:
    def __init__(self, household: HouseholdConfig) -> None:
        self._hh = household
        kws = sorted(set(household.privacy.sensitive_keywords), key=len, reverse=True)
        self._kw_re = (
            re.compile(r"\b(" + "|".join(re.escape(k) for k in kws) + r")\b", re.I)
            if kws
            else None
        )

    # -- classification ---------------------------------------------------------

    def is_sensitive_tool(self, name: str) -> bool:
        return any(name.startswith(p) for p in self._hh.privacy.sensitive_tool_prefixes)

    def mentions_private_data(self, text: str) -> bool:
        return bool(self._kw_re and self._kw_re.search(text or ""))

    def touched_private(self, tool_names: Iterable[str]) -> bool:
        return any(self.is_sensitive_tool(n) for n in tool_names)

    @staticmethod
    def _complexity(text: str) -> float:
        try:
            from openjarvis.learning.routing.complexity import score_complexity

            return float(score_complexity(text).score)
        except Exception:
            return 1.0 if len(text) > 200 else 0.2

    def _resolve_named(self, name: str) -> str:
        m = self._hh.models
        return {
            "local": m.local,
            "cloud": m.cloud,
            "claude": m.cloud,
            "gemini": m.cloud_alt,
        }.get(name.lower(), name)

    # -- decision -----------------------------------------------------------------

    def route(
        self,
        person: Person,
        text: str,
        *,
        private_turns_remaining: int = 0,
    ) -> RouteDecision:
        m = self._hh.models
        query = text or ""
        forced: Optional[str] = None
        pm = _PREFIX_RE.match(query)
        if pm:
            forced = pm.group(1).lower()
            query = query[pm.end() :]

        if not person.is_owner:
            model = self._resolve_named(m.family)
            cloud = model not in (m.local,)
            if cloud and not m.cloud_enabled:
                model, cloud = m.local, False
            return RouteDecision(
                model=model,
                private=False,
                reason="family member",
                query=query,
                strip_sensitive_tools=True,
                drop_private_history=True,
                tags=["family"],
            )

        if self.mentions_private_data(query) or private_turns_remaining > 0:
            why = (
                "mentions private data"
                if self.mentions_private_data(query)
                else "conversation recently used private data"
            )
            if forced in ("claude", "cloud", "gemini"):
                why += " (cloud request ignored: private data stays local)"
            return RouteDecision(model=m.local, private=True, reason=why, query=query)

        if forced == "local" or not m.cloud_enabled:
            return RouteDecision(
                model=m.local, private=False, reason="local requested", query=query
            )
        if forced in ("claude", "cloud", "gemini"):
            return RouteDecision(
                model=self._resolve_named(forced),
                private=False,
                reason=f"{forced} requested",
                query=query,
                strip_sensitive_tools=True,
                drop_private_history=True,
            )

        score = self._complexity(query)
        if score < m.complexity_threshold:
            return RouteDecision(
                model=m.local,
                private=False,
                reason=f"simple request (complexity {score:.2f})",
                query=query,
            )
        return RouteDecision(
            model=m.cloud,
            private=False,
            reason=f"complex request (complexity {score:.2f})",
            query=query,
            strip_sensitive_tools=True,
            drop_private_history=True,
        )

    # -- helpers for callers -------------------------------------------------------

    def filter_tools(self, tools: Sequence[Any], decision: RouteDecision) -> List[Any]:
        if not decision.strip_sensitive_tools:
            return list(tools)
        return [t for t in tools if not self.is_sensitive_tool(t.spec.name)]


__all__ = ["PrivacyRouter", "RouteDecision"]
