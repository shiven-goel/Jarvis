"""Household configuration: people, roles, model routing, privacy and approvals.

Loaded from ``<config-dir>/people.toml`` (default ``~/.openjarvis/people.toml``)
or the path in ``OPENJARVIS_PEOPLE``. When the file does not exist the whole
household layer stays off and OpenJarvis behaves exactly as upstream.

Example (see ``personal/config/people.example.toml`` for the annotated version)::

    [settings]
    enabled = true

    [[people]]
    id = "owner"
    name = "Shiven"
    role = "owner"
    slack = ["U01ABCDEF"]
    whatsapp = ["15551234567"]
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, FrozenSet, List, Optional, Tuple

logger = logging.getLogger(__name__)

try:  # Python 3.11+
    import tomllib as _toml
except ModuleNotFoundError:  # pragma: no cover - 3.10
    import tomli as _toml  # type: ignore[no-redef]

ROLES = ("owner", "family")

# Channel ids (as reported by ChannelMessage.channel) -> identity key in people.toml
CHANNEL_IDENTITY_KEYS: Dict[str, str] = {
    "slack": "slack",
    "whatsapp_baileys": "whatsapp",
    "whatsapp": "whatsapp",
    "telegram": "telegram",
    "discord": "discord",
}

# --- Defaults -----------------------------------------------------------------

# Tools that touch personal data (email, files, notes, calendar, contacts).
# Matched as name prefixes. Results from these never go to a cloud model, and
# family members never get them.
DEFAULT_SENSITIVE_TOOL_PREFIXES: Tuple[str, ...] = (
    "m365_",
    "gmail",
    "outlook",
    "gdrive",
    "drive_",
    "google_",
    "notion",
    "calendar_",
    "gcalendar",
    "contacts",
    "gcontacts",
    "knowledge_",
    "scan_chunks",
    "digest_collect",
    "imessage",
    "apple_",
    "slack_connector",
    "db_query",
    "file_read",
    "pdf_extract",
)

# Words that mean "this is about my private data" -> answer with the local model.
DEFAULT_SENSITIVE_KEYWORDS: Tuple[str, ...] = (
    "email",
    "e-mail",
    "emails",
    "inbox",
    "mail",
    "gmail",
    "outlook",
    "m365",
    "calendar",
    "meeting",
    "meetings",
    "schedule",
    "appointment",
    "event",
    "drive",
    "document",
    "documents",
    "doc",
    "docs",
    "file",
    "files",
    "folder",
    "spreadsheet",
    "sheet",
    "notion",
    "note",
    "notes",
    "contact",
    "contacts",
    "password",
    "bank",
    "salary",
    "medical",
    "invoice",
    "receipt",
)

# Low-risk tools that run without asking (owner). Everything else needs approval.
DEFAULT_AUTO_TOOLS: Tuple[str, ...] = (
    "calculator",
    "think",
    "get_weather",
    "web_search",
    "fx_macro_data",
    "retrieval",
    "memory_store",
    "memory_retrieve",
    "memory_search",
    "memory_index",
    "memory_manage",
    "user_profile_manage",
    "kg_query",
    "kg_neighbors",
    "kg_add_entity",
    "kg_add_relation",
    "record_decision",
    "skill_manage",
    "knowledge_search",
    "knowledge_sql",
    "scan_chunks",
    "file_read",
    "pdf_extract",
    "calendar_search",
    "calendar_upcoming",
    "digest_collect",
    "text_to_speech",
    "audio_transcribe",
    "channel_list",
    "channel_status",
    "get_pending_actions",
    "list_scheduled_tasks",
    "schedule_task",  # reminders
    "pause_scheduled_task",
    "resume_scheduled_task",
    "cancel_scheduled_task",
    "git_status",
    "git_diff",
    "git_log",
    "m365_accounts",
    "m365_mail_recent",
    "m365_mail_search",
    "m365_mail_read",
    "llm",
    # http_request is low risk only for GET/HEAD; see approval_gate.classify_risk
)

# What family members may use. No personal-data tools, nothing that acts.
DEFAULT_FAMILY_TOOLS: Tuple[str, ...] = (
    "calculator",
    "think",
    "get_weather",
    "web_search",
    "memory_store",
    "memory_retrieve",
    "memory_search",
    "memory_manage",
    "user_profile_manage",
)


# --- Dataclasses ----------------------------------------------------------------


@dataclass(frozen=True)
class Person:
    id: str
    name: str
    role: str  # "owner" | "family"
    identities: Dict[str, FrozenSet[str]] = field(default_factory=dict)

    @property
    def is_owner(self) -> bool:
        return self.role == "owner"


@dataclass
class ModelPolicy:
    """Which model answers what.

    ``local`` handles private and quick requests; ``cloud`` handles complex,
    non-private ones; ``cloud_alt`` is used when the message starts with
    ``/gemini`` (or the alt's first word). ``family`` is "local", "cloud" or a
    model name.
    """

    local: str = "qwen3:8b"
    cloud: str = "claude-opus-5-5"
    cloud_alt: str = "gemini-2.5-pro"
    family: str = "local"
    complexity_threshold: float = 0.30  # below this -> local model
    cloud_enabled: bool = True


@dataclass
class PrivacyPolicy:
    sensitive_tool_prefixes: Tuple[str, ...] = DEFAULT_SENSITIVE_TOOL_PREFIXES
    sensitive_keywords: Tuple[str, ...] = DEFAULT_SENSITIVE_KEYWORDS
    # After private data is touched, keep the conversation on the local model
    # for this many more turns.
    sticky_turns: int = 6


@dataclass
class ApprovalPolicy:
    enabled: bool = True
    auto_tools: FrozenSet[str] = frozenset(DEFAULT_AUTO_TOOLS)
    always_ask_tools: FrozenSet[str] = frozenset()
    timeout_seconds: int = 600


@dataclass
class HouseholdConfig:
    enabled: bool = True
    ignore_groups: bool = True
    stranger_reply: str = ""  # empty = ignore strangers silently
    people: List[Person] = field(default_factory=list)
    models: ModelPolicy = field(default_factory=ModelPolicy)
    privacy: PrivacyPolicy = field(default_factory=PrivacyPolicy)
    approval: ApprovalPolicy = field(default_factory=ApprovalPolicy)
    family_tools: FrozenSet[str] = frozenset(DEFAULT_FAMILY_TOOLS)
    data_dir: Path = field(default_factory=lambda: _config_dir() / "people")
    source_path: Optional[Path] = None
    router: Any = None  # model_router.RouterConfig

    # -- lookups --------------------------------------------------------------

    @property
    def owner(self) -> Optional[Person]:
        for p in self.people:
            if p.is_owner:
                return p
        return None

    def get(self, person_id: str) -> Optional[Person]:
        for p in self.people:
            if p.id == person_id:
                return p
        return None

    def resolve_sender(self, channel: str, sender: str) -> Optional[Person]:
        """Map a channel sender id to an allowed person (or None)."""
        key = CHANNEL_IDENTITY_KEYS.get(channel, channel)
        norm = normalize_identity(key, sender)
        if not norm:
            return None
        for p in self.people:
            if norm in p.identities.get(key, frozenset()):
                return p
        return None

    def person_dir(self, person: Person) -> Path:
        """Private data folder for a non-owner person."""
        safe = re.sub(r"[^a-zA-Z0-9_-]", "_", person.id)
        return self.data_dir / safe


# --- Helpers --------------------------------------------------------------------


def _config_dir() -> Path:
    try:
        from openjarvis.core.config import get_config_dir

        return Path(get_config_dir())
    except Exception:  # pragma: no cover
        return Path.home() / ".openjarvis"


def normalize_identity(key: str, raw: str) -> str:
    """Normalize a sender id so config entries and live ids compare equal.

    - whatsapp: digits only (``+1 (555) 123-4567`` and
      ``15551234567@s.whatsapp.net`` both become ``15551234567``). Linked-id
      senders (``1234@lid``) keep the ``@lid`` suffix so they can be listed
      explicitly.
    - slack / discord: upper-cased ids.
    - others: stripped string.
    """
    s = str(raw or "").strip()
    if not s:
        return ""
    if key == "whatsapp":
        user = s.split(":")[0] if "@" in s else s  # drop device suffix "123:4@s..."
        if s.endswith("@lid"):
            return re.sub(r"\D", "", user.split("@")[0]) + "@lid"
        user = user.split("@")[0]
        return re.sub(r"\D", "", user)
    if key in ("slack", "discord"):
        return s.upper()
    return s


def is_group_conversation(channel: str, conversation_id: str) -> bool:
    """True for group chats / public channels (not 1:1 direct messages)."""
    cid = str(conversation_id or "")
    key = CHANNEL_IDENTITY_KEYS.get(channel, channel)
    if key == "whatsapp":
        return (
            cid.endswith("@g.us")
            or cid.endswith("@broadcast")
            or cid.endswith("@newsletter")
        )
    if key == "slack":
        # D = direct message; C = channel; G = private channel / group DM
        return bool(cid) and not cid.upper().startswith("D")
    if key == "telegram":
        return cid.startswith("-")
    return False


def _as_tuple(value: Any, default: Tuple[str, ...]) -> Tuple[str, ...]:
    if value is None:
        return default
    if isinstance(value, str):
        value = [v.strip() for v in value.split(",")]
    return tuple(str(v).strip() for v in value if str(v).strip())


def _parse_person(raw: Dict[str, Any]) -> Person:
    pid = str(raw.get("id", "")).strip()
    if not pid:
        raise ValueError("each [[people]] entry needs an 'id'")
    role = str(raw.get("role", "family")).strip().lower()
    if role not in ROLES:
        raise ValueError(f"person '{pid}': role must be one of {ROLES}, got {role!r}")
    identities: Dict[str, FrozenSet[str]] = {}
    for key in set(CHANNEL_IDENTITY_KEYS.values()):
        vals = raw.get(key)
        if vals is None:
            continue
        if isinstance(vals, str):
            vals = [vals]
        norm = frozenset(n for n in (normalize_identity(key, v) for v in vals) if n)
        if norm:
            identities[key] = norm
    return Person(
        id=pid,
        name=str(raw.get("name", pid)),
        role=role,
        identities=identities,
    )


def parse_household(
    data: Dict[str, Any], *, source: Optional[Path] = None
) -> HouseholdConfig:
    settings = data.get("settings", {}) or {}
    models_raw = data.get("models", {}) or {}
    privacy_raw = data.get("privacy", {}) or {}
    approval_raw = data.get("approval", {}) or {}
    family_raw = data.get("family", {}) or {}

    people = [_parse_person(p) for p in data.get("people", []) or []]
    owners = [p for p in people if p.is_owner]
    if len(owners) != 1:
        raise ValueError(
            f'people.toml must define exactly one person with role = "owner" '
            f"(found {len(owners)})"
        )
    ids = [p.id for p in people]
    if len(ids) != len(set(ids)):
        raise ValueError("people.toml has duplicate person ids")
    # The same identity must not belong to two people.
    seen: Dict[Tuple[str, str], str] = {}
    for p in people:
        for key, vals in p.identities.items():
            for v in vals:
                if (key, v) in seen:
                    raise ValueError(
                        f"{key} id {v!r} is listed for both "
                        f"'{seen[(key, v)]}' and '{p.id}'"
                    )
                seen[(key, v)] = p.id

    mp = ModelPolicy()
    for f in ("local", "cloud", "cloud_alt", "family"):
        if f in models_raw:
            setattr(mp, f, str(models_raw[f]))
    if "complexity_threshold" in models_raw:
        mp.complexity_threshold = float(models_raw["complexity_threshold"])
    if "cloud_enabled" in models_raw:
        mp.cloud_enabled = bool(models_raw["cloud_enabled"])

    pp = PrivacyPolicy(
        sensitive_tool_prefixes=_as_tuple(
            privacy_raw.get("sensitive_tool_prefixes"), DEFAULT_SENSITIVE_TOOL_PREFIXES
        )
        + _as_tuple(privacy_raw.get("extra_sensitive_tool_prefixes"), ()),
        sensitive_keywords=_as_tuple(
            privacy_raw.get("sensitive_keywords"), DEFAULT_SENSITIVE_KEYWORDS
        )
        + _as_tuple(privacy_raw.get("extra_sensitive_keywords"), ()),
        sticky_turns=int(privacy_raw.get("sticky_turns", 6)),
    )

    auto = set(_as_tuple(approval_raw.get("auto_tools"), DEFAULT_AUTO_TOOLS))
    auto |= set(_as_tuple(approval_raw.get("extra_auto_tools"), ()))
    always_ask = frozenset(_as_tuple(approval_raw.get("always_ask_tools"), ()))
    auto -= always_ask
    ap = ApprovalPolicy(
        enabled=bool(approval_raw.get("enabled", True)),
        auto_tools=frozenset(auto),
        always_ask_tools=always_ask,
        timeout_seconds=int(approval_raw.get("timeout_minutes", 10)) * 60,
    )

    family_tools = set(_as_tuple(family_raw.get("tools"), DEFAULT_FAMILY_TOOLS))
    # Family never gets personal-data tools, even if listed by mistake.
    family_tools = {
        t
        for t in family_tools
        if not any(t.startswith(p) for p in pp.sensitive_tool_prefixes)
    }

    from openjarvis.household.model_router import parse_router_config

    router = parse_router_config(
        data.get("router", {}) or {},
        local=mp.local,
        cloud=mp.cloud,
        cloud_alt=mp.cloud_alt,
    )
    data_dir = settings.get("data_dir")
    return HouseholdConfig(
        router=router,
        enabled=bool(settings.get("enabled", True)),
        ignore_groups=bool(settings.get("ignore_groups", True)),
        stranger_reply=str(settings.get("stranger_reply", "")),
        people=people,
        models=mp,
        privacy=pp,
        approval=ap,
        family_tools=frozenset(family_tools),
        data_dir=Path(data_dir).expanduser() if data_dir else _config_dir() / "people",
        source_path=source,
    )


def default_people_path() -> Path:
    env = os.environ.get("OPENJARVIS_PEOPLE", "").strip()
    if env:
        return Path(env).expanduser()
    return _config_dir() / "people.toml"


def load_household_config(path: Optional[Path] = None) -> Optional[HouseholdConfig]:
    """Load people.toml. Returns None when the file is absent.

    Raises ``ValueError`` for an invalid file: a broken allowlist must stop
    startup rather than silently letting everyone in.
    """
    p = Path(path) if path else default_people_path()
    if not p.exists():
        return None
    with open(p, "rb") as fh:
        data = _toml.load(fh)
    try:
        mode = p.stat().st_mode & 0o077
        if mode:
            logger.warning("%s is readable by other users; run: chmod 600 %s", p, p)
    except OSError:
        pass
    return parse_household(data, source=p)


__all__ = [
    "ApprovalPolicy",
    "HouseholdConfig",
    "ModelPolicy",
    "Person",
    "PrivacyPolicy",
    "default_people_path",
    "is_group_conversation",
    "load_household_config",
    "normalize_identity",
    "parse_household",
]
