"""Privacy-aware routing ("sensitive stuff stays local") on top of the model router.

Rules, in order:

1. Family member -> the family policy (default: local models only); never any
   personal-data tools.
2. Owner, message is about private data (keywords) **or** the conversation
   touched private data in the last ``sticky_turns`` turns -> local models
   only, all tools. ``/claude``-style requests are ignored.
3. Owner, explicit model prefix (``/local``, ``/claude``, ``/gemini``,
   ``/haiku``, ``/opus`` ... any alias in the router catalog) -> that model.
4. Otherwise the cost/token-aware :class:`ModelRouter` picks the cheapest
   model good enough for the task and big enough for the input.
5. Whenever the chosen model is a cloud model, **personal-data tools are
   removed** and earlier private turns are dropped from the history it sees.

Upstream routes only by complexity; this adds privacy, task type, token size,
cost and a daily budget.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Iterable, List, Optional, Sequence

from openjarvis.household.config import HouseholdConfig, Person
from openjarvis.household.model_router import ModelChoice, ModelRouter

_PREFIX_RE = re.compile(r"^\s*/([a-z0-9][a-z0-9._:-]*)\b[:\s]*", re.I)


@dataclass
class RouteDecision:
    model: str
    private: bool  # True -> local-only turn; mark the turn private
    reason: str
    query: str  # message with any routing prefix removed
    strip_sensitive_tools: bool = False
    drop_private_history: bool = False
    tags: List[str] = field(default_factory=list)
    is_local: bool = True
    choice: Optional[ModelChoice] = None

    @property
    def fallbacks(self) -> List[str]:
        return list(self.choice.fallbacks) if self.choice else []


class PrivacyRouter:
    def __init__(
        self, household: HouseholdConfig, model_router: Optional[ModelRouter] = None
    ) -> None:
        self._hh = household
        self.models = model_router or ModelRouter(household.router)
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

    def is_local_model(self, name: str) -> bool:
        prof = self._hh.router.get(name) if self._hh.router else None
        if prof is not None:
            return prof.is_local
        return name == self._hh.models.local  # unknown names count as cloud

    # -- decision -----------------------------------------------------------------

    def _split_prefix(self, text: str):
        pm = _PREFIX_RE.match(text or "")
        if pm and self._hh.router.by_alias(pm.group(1)) is not None:
            return pm.group(1).lower(), text[pm.end() :]
        return None, text or ""

    def _decision(
        self,
        choice: ModelChoice,
        *,
        private: bool,
        query: str,
        prefix: str = "",
        tags=None,
    ) -> RouteDecision:
        reason = (prefix + "; " if prefix else "") + choice.reason
        return RouteDecision(
            model=choice.model,
            private=private,
            reason=reason.strip("; "),
            query=query,
            strip_sensitive_tools=not choice.is_local or "family" in (tags or []),
            drop_private_history=not choice.is_local or "family" in (tags or []),
            tags=list(tags or []),
            is_local=choice.is_local,
            choice=choice,
        )

    def route(
        self,
        person: Person,
        text: str,
        *,
        private_turns_remaining: int = 0,
        history_tokens: int = 0,
    ) -> RouteDecision:
        m = self._hh.models
        forced, query = self._split_prefix(text)
        cloud_ok = m.cloud_enabled

        if not person.is_owner:
            fam = (m.family or "local").lower()
            if fam == "local" or not cloud_ok:
                choice = self.models.choose(
                    query, history_tokens=history_tokens, local_only=True
                )
            elif fam == "cloud":
                choice = self.models.choose(
                    query, history_tokens=history_tokens, max_tier=2
                )
            else:
                choice = self.models.choose(
                    query, history_tokens=history_tokens, forced=fam
                )
                if not choice.is_local and not cloud_ok:
                    choice = self.models.choose(
                        query, history_tokens=history_tokens, local_only=True
                    )
            return self._decision(
                choice,
                private=False,
                query=query,
                prefix="family member",
                tags=["family"],
            )

        if self.mentions_private_data(query) or private_turns_remaining > 0:
            why = (
                "mentions private data"
                if self.mentions_private_data(query)
                else "conversation recently used private data"
            )
            if forced and not self.is_local_model(
                self._hh.router.by_alias(forced).name
            ):
                why += " (cloud request ignored: private data stays local)"
            choice = self.models.choose(
                query,
                history_tokens=history_tokens,
                local_only=True,
                forced=forced
                if forced and self.is_local_model(self._hh.router.by_alias(forced).name)
                else None,
            )
            return self._decision(choice, private=True, query=query, prefix=why)

        choice = self.models.choose(
            query, history_tokens=history_tokens, local_only=not cloud_ok, forced=forced
        )
        return self._decision(choice, private=False, query=query)

    # -- helpers for callers -------------------------------------------------------

    def filter_tools(self, tools: Sequence[Any], decision: RouteDecision) -> List[Any]:
        if not decision.strip_sensitive_tools:
            return list(tools)
        return [t for t in tools if not self.is_sensitive_tool(t.spec.name)]


__all__ = ["PrivacyRouter", "RouteDecision"]
