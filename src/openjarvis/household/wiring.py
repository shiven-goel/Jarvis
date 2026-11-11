"""Channel wiring with allowlist, roles, approvals, privacy routing, per-person memory.

Replaces :meth:`openjarvis.system.core.JarvisSystem.wire_channel` when the
household layer is on. Differences from upstream:

1. Messages from senders not in people.toml are dropped (logged with the id
   so you can add it). Group chats / public channels are ignored by default.
2. "yes <id>" / "no <id>" replies resolve pending approvals *before* anything
   else, on the receiving thread.
3. Each message is processed on a worker thread (in order per conversation) so
   a turn waiting for approval doesn't block the channel from receiving the
   reply.
4. The privacy router picks the model and tool set per message; private turns
   are tagged in the session store and never shown to a cloud model.
5. Family members get their own session, memory and tool set.
"""

from __future__ import annotations

import concurrent.futures
import contextvars
import logging
import threading
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List

from openjarvis.household import Household
from openjarvis.household.config import Person, is_group_conversation
from openjarvis.household.context import RequestContext, reset_request, set_request
from openjarvis.household.privacy import RouteDecision

logger = logging.getLogger(__name__)

_MAX_WORKERS = 4
_HISTORY_LIMIT = 40  # messages


class _Dispatcher:
    """Thread pool that keeps messages of the same conversation in order."""

    def __init__(self) -> None:
        self._pool = concurrent.futures.ThreadPoolExecutor(
            max_workers=_MAX_WORKERS, thread_name_prefix="jarvis-channel"
        )
        self._locks: Dict[str, threading.Lock] = defaultdict(threading.Lock)
        self._guard = threading.Lock()

    def submit(self, key: str, fn, *args) -> concurrent.futures.Future:
        with self._guard:
            lock = self._locks[key]

        def _run():
            with lock:
                return fn(*args)

        ctx = contextvars.copy_context()
        return self._pool.submit(ctx.run, _run)


def _private_turns_remaining(session, sticky: int) -> int:
    """How many more turns stay local because of recent private activity.

    Counted from the last turn that *introduced* private data
    (``private_source``), not from turns that were merely kept local by
    stickiness -- otherwise a conversation could never return to the cloud.
    """
    user_turns = 0
    for sm in reversed(session.messages):
        if sm.role != "user":
            continue
        if (sm.metadata or {}).get("private_source"):
            return max(0, sticky - user_turns)
        user_turns += 1
        if user_turns >= sticky:
            break
    return 0


def _history(session, *, drop_private: bool) -> List[Any]:
    from openjarvis.core.types import Message, Role

    out: List[Any] = []
    for sm in session.messages[-_HISTORY_LIMIT:]:
        if drop_private and (sm.metadata or {}).get("private"):
            continue
        try:
            role = Role(sm.role)
        except ValueError:
            role = Role.USER
        out.append(Message(role=role, content=sm.content))
    return out


def _owner_tools(
    system: Any, household: Household, decision: RouteDecision
) -> List[Any]:
    tools = household.router.filter_tools(list(system.tools or []), decision)
    # The llm sub-tool must use the same model as the turn (a private turn must
    # not leak data to a cloud model through it).
    out = []
    for t in tools:
        if t.spec.name == "llm":
            try:
                from openjarvis.tools.llm_tool import LLMTool

                out.append(LLMTool(system.engine, model=decision.model))
                continue
            except Exception:
                if decision.private:
                    continue
        out.append(t)
    return out


def wire_household_channel(
    system: Any, channel_bridge: Any, household: Household
) -> None:
    from openjarvis.sessions.session import SessionStore

    if system.session_store is None:
        system.session_store = SessionStore(
            db_path=Path(system.config.sessions.db_path).expanduser(),
            max_age_hours=system.config.sessions.max_age_hours,
            consolidation_threshold=system.config.sessions.consolidation_threshold,
        )

    hh = household
    dispatcher = _Dispatcher()
    send_lock = threading.Lock()

    def _send(dest: str, text: str, reply_to: str = "") -> None:
        if not text:
            return
        with send_lock:
            try:
                channel_bridge.send(dest, text, conversation_id=reply_to)
            except Exception:
                logger.exception("Channel send error")

    def _process(cm, person: Person) -> None:
        store = system.session_store
        session_key = f"{person.id}:{cm.channel}:{cm.conversation_id}"
        session = store.get_or_create(
            session_key, channel=cm.channel, channel_user_id=cm.sender
        )
        remaining = _private_turns_remaining(session, hh.config.privacy.sticky_turns)
        decision = hh.router.route(
            person, cm.content, private_turns_remaining=remaining
        )
        prior = _history(session, drop_private=decision.drop_private_history)

        if person.is_owner:
            tools = _owner_tools(system, hh, decision)
            overrides: Dict[str, Any] = {
                "model": decision.model,
                "tool_instances": tools,
                "system_prompt_config": hh.system_prompt_config_for(
                    person, system.config.system_prompt
                ),
            }
            allowed = None
        else:
            tools = hh.family_tools(person)
            overrides = {
                "model": decision.model,
                "tool_instances": tools,
                "memory_files": hh.memory_files_for(person, system.config.memory_files),
                "memory_backend": hh.memory_backend_for(person),
                "system_prompt_config": hh.system_prompt_config_for(
                    person, system.config.system_prompt
                ),
            }
            allowed = {t.spec.name for t in tools}

        ctx = RequestContext(
            person_id=person.id,
            person_name=person.name,
            role=person.role,
            channel=cm.channel,
            conversation_id=cm.conversation_id,
            notify=lambda text: _send(cm.conversation_id, text),
            extra={
                "allowed_tools": allowed,
                "route": decision.reason,
                "cloud_turn": decision.model != hh.config.models.local,
                "sensitive_tool": hh.router.is_sensitive_tool,
            },
        )
        token = set_request(ctx)
        hh.record_route(person, cm.channel, decision.model, decision.reason)
        logger.info(
            "[%s] %s -> %s (%s)", cm.channel, person.id, decision.model, decision.reason
        )
        tool_names: List[str] = []
        try:
            agent = system.agent_name if system.agent_name else None
            if agent == "none":
                agent = None
            result = system.ask(
                decision.query,
                context=person.is_owner,
                agent=agent,
                prior_messages=prior,
                **overrides,
            )
            reply = result.get("content", "")
            tool_names = [
                tr.get("tool_name", "") for tr in result.get("tool_results", [])
            ]
        except Exception:
            logger.exception("Channel message handler error")
            reply = "Sorry, I hit an error processing that."
        finally:
            reset_request(token)

        touched = hh.router.touched_private(tool_names)
        source = touched or hh.router.mentions_private_data(decision.query)
        private = decision.private or touched
        meta = {
            "person": person.id,
            "private": private,  # hidden from cloud models later
            "private_source": source,  # restarts the local-only window
            "model": decision.model,
        }
        try:
            store.save_message(
                session.session_id,
                "user",
                cm.content,
                channel=cm.channel,
                metadata=meta,
            )
            store.save_message(
                session.session_id,
                "assistant",
                reply,
                channel=cm.channel,
                metadata=meta,
            )
        except Exception:
            logger.debug("Session save error", exc_info=True)

        _send(cm.conversation_id, reply, getattr(cm, "message_id", ""))

    def _on_channel_message(cm) -> None:
        if hh.config.ignore_groups and is_group_conversation(
            cm.channel, cm.conversation_id
        ):
            logger.debug(
                "Ignoring group message in %s:%s", cm.channel, cm.conversation_id
            )
            return
        person = hh.config.resolve_sender(cm.channel, cm.sender)
        if person is None:
            logger.warning(
                "Ignored message from unknown %s sender %r. To allow them, add this id "
                "to people.toml.",
                cm.channel,
                cm.sender,
            )
            if hh.config.stranger_reply:
                _send(cm.conversation_id, hh.config.stranger_reply)
            return

        # Approval replies are handled immediately (the waiting turn holds the
        # conversation's worker).
        answer = hh.gate.handle_reply(person.id, cm.content)
        if answer is not None:
            _send(cm.conversation_id, answer)
            return

        key = f"{person.id}:{cm.channel}:{cm.conversation_id}"
        dispatcher.submit(key, _process, cm, person)

    channel_bridge.on_message(_on_channel_message)
    channel_bridge._household_dispatcher = dispatcher  # for tests / shutdown


__all__ = ["wire_household_channel"]
