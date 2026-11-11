"""Household layer for a personal Jarvis: who may talk to it and what they get.

[jarvis-fork] Not part of upstream OpenJarvis. Activated only when
``~/.openjarvis/people.toml`` (or ``$OPENJARVIS_PEOPLE``) exists. It adds:

* a sender allowlist with roles (owner / family) for every chat channel,
  ignoring group chats and strangers;
* per-person memory (USER.md, MEMORY.md, memory.db) for family members, who
  also get a restricted, non-personal tool set;
* privacy routing: private data is only ever processed by the local model;
* a risk-tiered approval gate for tool calls.
"""

from __future__ import annotations

import logging
import threading
import time
from collections import deque
from pathlib import Path
from typing import Any, Dict, List, Optional

from openjarvis.household.config import (
    HouseholdConfig,
    Person,
    load_household_config,
)
from openjarvis.household.privacy import PrivacyRouter

logger = logging.getLogger(__name__)


class Household:
    """Runtime state built from :class:`HouseholdConfig`."""

    def __init__(self, config: HouseholdConfig) -> None:
        from openjarvis.security.approval_gate import ApprovalGate

        self.config = config
        self.router = PrivacyRouter(config)
        self.gate = ApprovalGate(
            auto_tools=config.approval.auto_tools,
            always_ask_tools=config.approval.always_ask_tools,
            timeout_seconds=config.approval.timeout_seconds,
            enabled=config.approval.enabled,
        )
        self._lock = threading.Lock()
        self._memory_backends: Dict[str, Any] = {}
        self._family_tools: Dict[str, List[Any]] = {}
        # Recent routing decisions (no message text), for the HUD / debugging.
        self.recent_routes: deque = deque(maxlen=50)

    def record_route(
        self, person: Person, channel: str, model: str, reason: str
    ) -> None:
        self.recent_routes.append(
            {
                "time": time.time(),
                "person": person.id,
                "channel": channel,
                "model": model,
                "reason": reason,
            }
        )

    # -- per-person resources ------------------------------------------------------

    def person_paths(self, person: Person) -> Dict[str, Path]:
        d = self.config.person_dir(person)
        return {
            "dir": d,
            "user": d / "USER.md",
            "memory": d / "MEMORY.md",
            "db": d / "memory.db",
        }

    def ensure_person_dir(self, person: Person) -> Dict[str, Path]:
        paths = self.person_paths(person)
        paths["dir"].mkdir(parents=True, exist_ok=True)
        try:
            paths["dir"].chmod(0o700)
            self.config.data_dir.chmod(0o700)
        except OSError:
            pass
        if not paths["user"].exists():
            paths["user"].write_text(
                f"# {person.name}\n\n- Family member of the owner.\n"
            )
        return paths

    def memory_backend_for(self, person: Person) -> Optional[Any]:
        """Owner -> None (use the system's backend). Family -> their own SQLite DB."""
        if person.is_owner:
            return None
        with self._lock:
            mb = self._memory_backends.get(person.id)
            if mb is None:
                from openjarvis.tools.storage.sqlite import SQLiteMemory

                paths = self.ensure_person_dir(person)
                mb = SQLiteMemory(db_path=str(paths["db"]))
                self._memory_backends[person.id] = mb
            return mb

    def memory_files_for(self, person: Person, base: Any) -> Optional[Any]:
        """Family -> MemoryFilesConfig pointing at their own USER.md/MEMORY.md."""
        if person.is_owner:
            return None
        from dataclasses import replace

        paths = self.ensure_person_dir(person)
        return replace(
            base,
            memory_path=str(paths["memory"]),
            user_path=str(paths["user"]),
        )

    def system_prompt_config_for(self, person: Person, base: Any) -> Any:
        from dataclasses import replace

        owner = self.config.owner
        owner_name = owner.name if owner else "the owner"
        if person.is_owner:
            note = f"You are talking with {person.name}, your owner."
        else:
            note = (
                f"You are talking with {person.name}, a family member of "
                f"{owner_name}. Be warm and helpful. You have no access to "
                f"{owner_name}'s email, calendar, files, notes or memories, and "
                f"must never claim otherwise or reveal anything private about "
                f"{owner_name}. If asked to act on {owner_name}'s behalf, say "
                f"they need to ask {owner_name} directly."
            )
        prefix = (base.prefix + "\n\n" if getattr(base, "prefix", "") else "") + note
        return replace(base, prefix=prefix)

    def family_tools(self, person: Person) -> List[Any]:
        """Fresh tool instances for a family member, bound to their own memory."""
        with self._lock:
            cached = self._family_tools.get(person.id)
        if cached is not None:
            return cached
        import openjarvis.tools  # noqa: F401 -- populate registry
        from openjarvis.core.registry import ToolRegistry
        from openjarvis.tools._stubs import BaseTool

        backend = self.memory_backend_for(person)
        paths = self.ensure_person_dir(person)
        tools: List[Any] = []
        for name in sorted(self.config.family_tools):
            if not ToolRegistry.contains(name):
                continue
            cls = ToolRegistry.get(name)
            try:
                if name == "memory_manage":
                    inst = cls(memory_path=paths["memory"])
                elif name == "user_profile_manage":
                    inst = cls(user_path=paths["user"])
                elif isinstance(cls, type) and issubclass(cls, BaseTool):
                    inst = cls()
                else:
                    continue
            except Exception as exc:
                logger.debug("family tool %s unavailable: %s", name, exc)
                continue
            if name.startswith("memory_") or name == "retrieval":
                if hasattr(inst, "_backend"):
                    inst._backend = backend
            tools.append(inst)
        with self._lock:
            self._family_tools[person.id] = tools
        return tools


_lock = threading.Lock()
_household: Optional[Household] = None
_loaded = False


def get_household(*, reload: bool = False) -> Optional[Household]:
    """Return the process household (loading people.toml once), or None if off."""
    global _household, _loaded
    with _lock:
        if _loaded and not reload:
            return _household
        cfg = load_household_config()
        if cfg is None or not cfg.enabled:
            _household = None
        else:
            _household = Household(cfg)
            logger.info(
                "Household enabled: %d people (%s)",
                len(cfg.people),
                ", ".join(f"{p.id}:{p.role}" for p in cfg.people),
            )
        _install_gate(_household)
        _loaded = True
        return _household


def set_household(household: Optional[Household]) -> None:
    """Install a household explicitly (tests, embedding)."""
    global _household, _loaded
    with _lock:
        _household = household
        _loaded = True
        _install_gate(household)


def reset_household() -> None:
    global _household, _loaded
    with _lock:
        _household = None
        _loaded = False
        _install_gate(None)


def _install_gate(household: Optional[Household]) -> None:
    from openjarvis.security.approval_gate import install_gate

    install_gate(household.gate if household is not None else None)


__all__ = [
    "Household",
    "get_household",
    "reset_household",
    "set_household",
]
