"""Cost- and token-aware model router.

[jarvis-fork] Picks the model for each message from a catalog, using:

1. **Privacy** (decided first, by :mod:`openjarvis.household.privacy`): private
   turns may only use ``local`` models.
2. **Task type**: chat, quick question, summarize, extract, writing, coding,
   analysis, hard reasoning. Each type needs a minimum quality *tier*
   (1 = small local model ... 4 = strongest cloud model).
3. **Complexity** of the message bumps the tier up or down one step.
4. **Token size**: estimated input tokens (message + history + overhead) plus
   expected output must fit the model's context window. Long documents
   therefore go to long-context models.
5. **Cost**: among the models that qualify, the cheapest estimated cost wins.
6. **Daily budget**: at 80% of ``daily_budget_usd`` the maximum tier drops by
   one; at 100% only local models are used until midnight.
7. **Fallbacks**: an ordered list of other qualifying models, tried if the
   chosen one fails.

Usage (tokens and estimated USD per model per day) is recorded locally in
``<config-dir>/router_usage.json``.
"""

from __future__ import annotations

import json
import logging
import math
import os
import re
import threading
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

logger = logging.getLogger(__name__)

# --- Task types --------------------------------------------------------------

TASKS = (
    "chat",
    "quick",
    "summarize",
    "extract",
    "writing",
    "coding",
    "analysis",
    "hard",
)

DEFAULT_TASK_TIERS: Dict[str, int] = {
    "chat": 1,
    "quick": 1,
    "extract": 2,
    "summarize": 2,
    "writing": 3,
    "coding": 3,
    "analysis": 3,
    "hard": 4,
}

# Typical answer length per task (tokens), used for cost and context estimates.
EXPECTED_OUTPUT: Dict[str, int] = {
    "chat": 150,
    "quick": 250,
    "extract": 500,
    "summarize": 700,
    "writing": 1200,
    "coding": 1500,
    "analysis": 1500,
    "hard": 3000,
}

_PATTERNS: List[Tuple[str, re.Pattern[str]]] = [
    (
        "coding",
        re.compile(
            r"```|\b(code|function|bug|debug|stack ?trace|exception|traceback|refactor|"
            r"python|javascript|typescript|java|rust|golang|sql|regex|api|script|"
            r"compile|unit test|class|repo|git|docker|kubernetes|bash)\b",
            re.I,
        ),
    ),
    (
        "hard",
        re.compile(
            r"\b(prove|proof|derive|step[- ]by[- ]step|rigorous|optimi[sz]e|"
            r"architecture|design a|strategy|trade-?offs?|multi-step|plan out)\b",
            re.I,
        ),
    ),
    (
        "summarize",
        re.compile(
            r"\b(summari[sz]e|summary|tl;?dr|recap|key points|condense)\b", re.I
        ),
    ),
    (
        "extract",
        re.compile(
            r"\b(extract|list all|pull out|parse|classify|categori[sz]e|tag|"
            r"convert to (json|csv|table)|fill in)\b",
            re.I,
        ),
    ),
    (
        "writing",
        re.compile(
            r"\b(write|draft|rewrite|compose|essay|email to|cover letter|blog|post|"
            r"story|poem|haiku|caption|speech|proposal|edit my|proofread)\b",
            re.I,
        ),
    ),
    (
        "analysis",
        re.compile(
            r"\b(analy[sz]e|compare|evaluate|assess|explain why|pros and cons|"
            r"research|review|critique|implications)\b",
            re.I,
        ),
    ),
]

_CHAT_RE = re.compile(
    r"^\s*(hi|hey|hello|yo|thanks|thank you|ok|okay|cool|great|good (morning|night|"
    r"evening)|how are you|lol|nice)\b[\s!.?]*$",
    re.I,
)


def estimate_tokens(text: str) -> int:
    """Rough token estimate (~4 characters per token, plus a small floor)."""
    return int(math.ceil(len(text or "") / 4.0)) + 4


def classify_task(text: str) -> str:
    t = (text or "").strip()
    if not t or _CHAT_RE.match(t):
        return "chat"
    for name, pat in _PATTERNS:
        if pat.search(t):
            return name
    # Long pasted text without an instruction is usually "summarize this".
    if len(t) > 6000:
        return "summarize"
    return "quick"


# --- Catalog -----------------------------------------------------------------


@dataclass(frozen=True)
class ModelProfile:
    name: str
    kind: str  # "local" | "cloud"
    tier: int  # 1..4 quality tier
    context_window: int
    input_cost: float = 0.0  # USD per 1M input tokens
    output_cost: float = 0.0  # USD per 1M output tokens
    strengths: Tuple[str, ...] = ()
    aliases: Tuple[str, ...] = ()  # e.g. ("haiku",) -> "/haiku ..."
    enabled: bool = True
    # Price change above this many input tokens (e.g. Haiku 5.5 doubles past 100K)
    long_context_threshold: int = 0
    long_input_cost: float = 0.0
    long_output_cost: float = 0.0

    @property
    def is_local(self) -> bool:
        return self.kind == "local"

    def cost(self, tokens_in: int, tokens_out: int) -> float:
        if self.long_context_threshold and tokens_in > self.long_context_threshold:
            ic, oc = (
                self.long_input_cost or self.input_cost,
                self.long_output_cost or self.output_cost,
            )
        else:
            ic, oc = self.input_cost, self.output_cost
        return (tokens_in * ic + tokens_out * oc) / 1_000_000


def default_catalog(local: str, cloud: str, cloud_alt: str) -> List[ModelProfile]:
    """Minimal catalog when people.toml has no [[router.models]]."""
    out = [
        ModelProfile(
            local, "local", 1, 8192, strengths=("chat", "quick"), aliases=("local",)
        ),
        ModelProfile(
            cloud, "cloud", 4, 200_000, 3.0, 15.0, aliases=("claude", "cloud")
        ),
    ]
    if cloud_alt and cloud_alt != cloud:
        # Only used when asked for by name (/gemini), so give it no tier advantage.
        out.append(
            ModelProfile(
                cloud_alt,
                "cloud",
                3,
                1_000_000,
                1.25,
                10.0,
                aliases=("gemini",),
                enabled=True,
            )
        )
    return out


@dataclass
class RouterConfig:
    models: List[ModelProfile] = field(default_factory=list)
    task_tiers: Dict[str, int] = field(default_factory=lambda: dict(DEFAULT_TASK_TIERS))
    daily_budget_usd: float = 0.0  # 0 = no budget
    preferred: Dict[str, str] = field(default_factory=dict)  # task -> model name
    context_margin: float = 0.85  # use at most this share of a context window
    history_overhead_tokens: int = 1500  # system prompt + tool schemas
    explicit_catalog: bool = False

    def by_alias(self, word: str) -> Optional[ModelProfile]:
        w = word.lower()
        for m in self.models:
            if w == m.name.lower() or w in (a.lower() for a in m.aliases):
                return m
        return None

    def get(self, name: str) -> Optional[ModelProfile]:
        for m in self.models:
            if m.name == name:
                return m
        return None


def parse_router_config(
    raw: Dict[str, Any], *, local: str, cloud: str, cloud_alt: str
) -> RouterConfig:
    raw = raw or {}
    models_raw = raw.get("models") or []
    if models_raw:
        models = []
        for m in models_raw:
            name = str(m.get("name", "")).strip()
            if not name:
                raise ValueError("each [[router.models]] entry needs a 'name'")
            kind = str(m.get("kind", "cloud")).lower()
            if kind not in ("local", "cloud"):
                raise ValueError(
                    f"router model {name}: kind must be 'local' or 'cloud'"
                )
            tier = int(m.get("tier", 2))
            if not 1 <= tier <= 4:
                raise ValueError(f"router model {name}: tier must be 1-4")
            models.append(
                ModelProfile(
                    name=name,
                    kind=kind,
                    tier=tier,
                    context_window=int(
                        m.get("context_window", 8192 if kind == "local" else 200_000)
                    ),
                    input_cost=float(m.get("input_cost", 0.0)),
                    output_cost=float(m.get("output_cost", 0.0)),
                    strengths=tuple(m.get("strengths", ()) or ()),
                    aliases=tuple(m.get("aliases", ()) or ()),
                    enabled=bool(m.get("enabled", True)),
                    long_context_threshold=int(m.get("long_context_threshold", 0)),
                    long_input_cost=float(m.get("long_input_cost", 0.0)),
                    long_output_cost=float(m.get("long_output_cost", 0.0)),
                )
            )
        names = [m.name for m in models]
        if len(names) != len(set(names)):
            raise ValueError("duplicate model names in [[router.models]]")
        if not any(m.is_local and m.enabled for m in models):
            raise ValueError("[[router.models]] needs at least one enabled local model")
        explicit = True
    else:
        models = default_catalog(local, cloud, cloud_alt)
        explicit = False
    tiers = dict(DEFAULT_TASK_TIERS)
    for k, v in (raw.get("tasks") or {}).items():
        if k not in TASKS:
            raise ValueError(
                f"[router.tasks] unknown task '{k}' (use {', '.join(TASKS)})"
            )
        tiers[k] = max(1, min(4, int(v)))
    preferred = {str(k): str(v) for k, v in (raw.get("prefer") or {}).items()}
    for task, name in preferred.items():
        if task not in TASKS:
            raise ValueError(f"[router.prefer] unknown task '{task}'")
        if not any(m.name == name for m in models):
            raise ValueError(f"[router.prefer] {task} -> unknown model '{name}'")
    return RouterConfig(
        models=models,
        task_tiers=tiers,
        daily_budget_usd=float(raw.get("daily_budget_usd", 0.0)),
        preferred=preferred,
        context_margin=float(raw.get("context_margin", 0.85)),
        history_overhead_tokens=int(raw.get("history_overhead_tokens", 1500)),
        explicit_catalog=explicit,
    )


# --- Usage tracking ----------------------------------------------------------


class UsageTracker:
    """Per-day token and cost totals per model, persisted as a small JSON file."""

    def __init__(self, path: Optional[Path] = None, *, today=date.today) -> None:
        if path is None:
            from openjarvis.core.config import get_config_dir

            path = Path(get_config_dir()) / "router_usage.json"
        self.path = Path(path)
        self._today = today
        self._lock = threading.Lock()
        self._data: Dict[str, Dict[str, Dict[str, float]]] = self._load()

    def _load(self) -> Dict[str, Any]:
        try:
            return json.loads(self.path.read_text())
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            return {}

    def _save(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w") as fh:
                json.dump(self._data, fh, indent=1)
            os.replace(tmp, self.path)
        except OSError:
            logger.debug("Could not save router usage", exc_info=True)

    def record(self, model: str, tokens_in: int, tokens_out: int, cost: float) -> None:
        day = self._today().isoformat()
        with self._lock:
            d = self._data.setdefault(day, {}).setdefault(
                model,
                {"requests": 0, "input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0},
            )
            d["requests"] += 1
            d["input_tokens"] += int(tokens_in)
            d["output_tokens"] += int(tokens_out)
            d["cost_usd"] = round(d["cost_usd"] + float(cost), 6)
            # keep 60 days
            for old in sorted(self._data)[:-60]:
                self._data.pop(old, None)
            self._save()

    def spent_today(self) -> float:
        day = self._today().isoformat()
        with self._lock:
            return round(
                sum(m["cost_usd"] for m in self._data.get(day, {}).values()), 6
            )

    def summary(self, days: int = 7) -> Dict[str, Any]:
        with self._lock:
            keys = sorted(self._data)[-days:]
            return {k: self._data[k] for k in keys}


# --- Decision ----------------------------------------------------------------


@dataclass
class ModelChoice:
    model: str
    task: str
    tier_needed: int
    tokens_in: int
    tokens_out: int
    est_cost: float
    reason: str
    fallbacks: List[str] = field(default_factory=list)
    is_local: bool = True


class ModelRouter:
    def __init__(
        self, config: RouterConfig, usage: Optional[UsageTracker] = None
    ) -> None:
        self.config = config
        self.usage = usage

    # -- helpers --
    def _enabled(self) -> List[ModelProfile]:
        return [m for m in self.config.models if m.enabled]

    def local_models(self) -> List[ModelProfile]:
        return [m for m in self._enabled() if m.is_local]

    def _fits(self, m: ModelProfile, tokens_in: int, tokens_out: int) -> bool:
        return tokens_in + tokens_out <= int(
            m.context_window * self.config.context_margin
        )

    def _budget_cap(self) -> Tuple[int, str]:
        budget = self.config.daily_budget_usd
        if budget <= 0 or self.usage is None:
            return 4, ""
        spent = self.usage.spent_today()
        if spent >= budget:
            return 0, f"daily budget ${budget:.2f} used up (${spent:.2f}); local only"
        if spent >= 0.8 * budget:
            return (
                3,
                f"over 80% of daily budget (${spent:.2f}/${budget:.2f}); "
                "capped below top tier",
            )
        return 4, ""

    def required_tier(self, task: str, complexity: Optional[float]) -> int:
        tier = self.config.task_tiers.get(task, 2)
        if complexity is not None:
            if complexity >= 0.55:
                tier += 1
            elif complexity < 0.15 and task in ("quick", "chat", "extract"):
                tier -= 1
        return max(1, min(4, tier))

    @staticmethod
    def complexity(text: str) -> Optional[float]:
        try:
            from openjarvis.learning.routing.complexity import score_complexity

            return float(score_complexity(text).score)
        except Exception:
            return None

    # -- main --
    def choose(
        self,
        text: str,
        *,
        history_tokens: int = 0,
        local_only: bool = False,
        max_tier: int = 4,
        forced: Optional[str] = None,
    ) -> ModelChoice:
        task = classify_task(text)
        cx = self.complexity(text)
        need = self.required_tier(task, cx)
        t_in = (
            estimate_tokens(text) + history_tokens + self.config.history_overhead_tokens
        )
        t_out = EXPECTED_OUTPUT.get(task, 500)

        notes: List[str] = []
        cap, budget_note = self._budget_cap()
        if budget_note:
            notes.append(budget_note)
        if cap == 0:
            local_only = True
        max_tier = min(max_tier, cap or 4)

        pool = self.local_models() if local_only else self._enabled()
        if not pool:
            raise RuntimeError("model router: no enabled models")

        # Explicit request (/haiku, /opus, /gemini ...), honoured if allowed.
        if forced:
            m = self.config.by_alias(forced)
            if m is not None and m.enabled and (m.is_local or not local_only):
                return self._result(
                    m, task, need, t_in, t_out, [f"{forced} requested"] + notes, pool
                )
            if m is not None and local_only:
                notes.append(f"{forced} ignored (must stay local)")

        # Preferred model for this task, if it qualifies.
        pref = self.config.get(self.config.preferred.get(task, ""))
        if (
            pref
            and pref in pool
            and pref.tier <= max_tier
            and self._fits(pref, t_in, t_out)
        ):
            return self._result(
                pref, task, need, t_in, t_out, [f"preferred for {task}"] + notes, pool
            )

        fitting = [m for m in pool if self._fits(m, t_in, t_out)]
        if not fitting:
            # Nothing fits: take the largest context window available.
            biggest = max(pool, key=lambda m: m.context_window)
            notes.append(
                f"~{t_in} tokens exceeds every context window; history will be trimmed"
            )
            return self._result(biggest, task, need, t_in, t_out, notes, pool)
        allowed = [m for m in fitting if m.tier <= max_tier] or fitting
        capable = [m for m in allowed if m.tier >= need]
        if capable:
            # cheapest qualifying; ties -> lower tier (faster), then local first
            best = min(
                capable,
                key=lambda m: (round(m.cost(t_in, t_out), 7), m.tier, not m.is_local),
            )
            why = f"{task}, needs tier {need}, ~{t_in}+{t_out} tokens"
            if len(fitting) < len(pool):
                why += ", long input"
        else:
            best = max(allowed, key=lambda m: (m.tier, -m.cost(t_in, t_out)))
            why = f"{task}, needs tier {need}; best allowed is tier {best.tier}"
        return self._result(best, task, need, t_in, t_out, [why] + notes, pool)

    def _result(self, m, task, need, t_in, t_out, notes, pool) -> ModelChoice:
        others = [x for x in pool if x.name != m.name and self._fits(x, t_in, t_out)]
        # fallbacks: same-or-better tier by cost, then the rest, local last resort
        others.sort(
            key=lambda x: (x.tier < min(need, m.tier), x.cost(t_in, t_out), x.is_local)
        )
        fb = [x.name for x in others][:3]
        local = [x.name for x in pool if x.is_local and x.name != m.name]
        if local and not any(n in local for n in fb):
            fb.append(local[0])
        return ModelChoice(
            model=m.name,
            task=task,
            tier_needed=need,
            tokens_in=t_in,
            tokens_out=t_out,
            est_cost=round(m.cost(t_in, t_out), 6),
            reason="; ".join(n for n in notes if n),
            fallbacks=fb,
            is_local=m.is_local,
        )

    # -- accounting --
    def record_usage(
        self,
        model: str,
        usage: Dict[str, Any],
        *,
        fallback_in: int = 0,
        fallback_out: int = 0,
    ) -> float:
        prof = self.config.get(model)
        u = usage or {}
        t_in = int(u.get("prompt_tokens") or u.get("input_tokens") or fallback_in or 0)
        t_out = int(
            u.get("completion_tokens") or u.get("output_tokens") or fallback_out or 0
        )
        cost = prof.cost(t_in, t_out) if prof else 0.0
        if self.usage is not None:
            self.usage.record(model, t_in, t_out, cost)
        return cost


def history_token_estimate(messages: Iterable[Any]) -> int:
    return sum(estimate_tokens(getattr(m, "content", "") or "") for m in messages)


__all__ = [
    "DEFAULT_TASK_TIERS",
    "ModelChoice",
    "ModelProfile",
    "ModelRouter",
    "RouterConfig",
    "UsageTracker",
    "classify_task",
    "estimate_tokens",
    "history_token_estimate",
    "parse_router_config",
]
