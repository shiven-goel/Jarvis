"""Privacy routing for dashboard / API chat requests.

Called at the top of ``POST /v1/chat/completions``. The API is local-only
(127.0.0.1 + Tailscale), so the caller is the owner. If the conversation is
about private data, the model is forced to the local one; on cloud turns the
approval gate refuses personal-data tools.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from openjarvis.household.context import RequestContext, set_request

logger = logging.getLogger(__name__)


def apply_api_privacy(request_body: Any) -> Optional[str]:
    """Mutate *request_body* (model / last message) per the privacy policy.

    Returns the routing reason, or None when the household layer is off.
    """
    from openjarvis.household import get_household

    hh = get_household()
    if hh is None:
        return None
    owner = hh.config.owner
    msgs = list(getattr(request_body, "messages", []) or [])
    last_user = None
    for m in reversed(msgs):
        if getattr(m, "role", "") == "user" and getattr(m, "content", None):
            last_user = m
            break
    text = str(last_user.content) if last_user is not None else ""

    # Sticky privacy: recent user turns or tool results that touched private data.
    sticky = hh.config.privacy.sticky_turns
    remaining = 0
    user_seen = 0
    earlier = msgs[: msgs.index(last_user)] if last_user is not None else msgs
    for m in reversed(earlier):
        role = getattr(m, "role", "")
        name = getattr(m, "name", "") or ""
        if role == "tool" and hh.router.is_sensitive_tool(name):
            remaining = max(remaining, sticky - user_seen)
        if role == "user":
            if hh.router.mentions_private_data(str(getattr(m, "content", "") or "")):
                remaining = max(remaining, sticky - user_seen)
            user_seen += 1
            if user_seen >= sticky:
                break

    decision = hh.router.route(owner, text, private_turns_remaining=remaining)
    requested = getattr(request_body, "model", "") or ""
    if last_user is not None and decision.query != text:
        last_user.content = decision.query  # strip /local, /claude, /gemini prefix
        request_body.model = decision.model
    elif decision.private or not requested:
        request_body.model = decision.model
    final_model = request_body.model
    hh.record_route(owner, "api", final_model, decision.reason)
    cloud_turn = final_model != hh.config.models.local
    set_request(
        RequestContext(
            person_id=owner.id,
            person_name=owner.name,
            role="owner",
            channel="api",
            extra={
                "route": decision.reason,
                "cloud_turn": cloud_turn,
                "sensitive_tool": hh.router.is_sensitive_tool,
            },
        )
    )
    if requested and requested != final_model:
        logger.info(
            "API privacy routing: %s -> %s (%s)",
            requested,
            final_model,
            decision.reason,
        )
    return decision.reason


__all__ = ["apply_api_privacy"]
