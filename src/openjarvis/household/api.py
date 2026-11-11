"""REST endpoints for the household layer (dashboard / HUD).

    GET  /v1/jarvis/approvals            pending + recent decisions
    POST /v1/jarvis/approvals/{id}/approve
    POST /v1/jarvis/approvals/{id}/deny
    GET  /v1/jarvis/household            people (no ids), model policy

The API server binds to 127.0.0.1 and is exposed only over Tailscale, so the
dashboard user is treated as the owner. Set OPENJARVIS_API_KEY to also require
a bearer token.
"""

from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter, HTTPException

router = APIRouter(prefix="/v1/jarvis", tags=["jarvis"])


def _hh():
    from openjarvis.household import get_household

    hh = get_household()
    if hh is None:
        raise HTTPException(status_code=404, detail="household layer is not enabled")
    return hh


@router.get("/approvals")
def list_approvals() -> Dict[str, Any]:
    hh = _hh()
    return {
        "pending": [p.to_dict() for p in hh.gate.pending()],
        "recent": [p.to_dict() for p in hh.gate.history()[-20:]][::-1],
    }


def _decide(approval_id: str, approved: bool) -> Dict[str, Any]:
    hh = _hh()
    owner = hh.config.owner
    if not hh.gate.resolve(
        approval_id, approved, by=f"{owner.id if owner else 'owner'}:dashboard"
    ):
        raise HTTPException(status_code=404, detail="no pending approval with that id")
    return {"id": approval_id, "status": "approved" if approved else "denied"}


@router.post("/approvals/{approval_id}/approve")
def approve(approval_id: str) -> Dict[str, Any]:
    return _decide(approval_id, True)


@router.post("/approvals/{approval_id}/deny")
def deny(approval_id: str) -> Dict[str, Any]:
    return _decide(approval_id, False)


@router.get("/routes")
def recent_routes() -> Dict[str, Any]:
    """Last 50 model-routing decisions (who, channel, model, why; no message text)."""
    return {"routes": list(_hh().recent_routes)[::-1]}


@router.get("/usage")
def usage() -> Dict[str, Any]:
    """Model-router spend: today's total, budget, last 7 days per model, catalog."""
    hh = _hh()
    rc = hh.config.router
    return {
        "spent_today_usd": hh.usage.spent_today(),
        "daily_budget_usd": rc.daily_budget_usd,
        "days": hh.usage.summary(7),
        "models": [
            {
                "name": m.name,
                "kind": m.kind,
                "tier": m.tier,
                "context_window": m.context_window,
                "input_cost_per_m": m.input_cost,
                "output_cost_per_m": m.output_cost,
                "aliases": list(m.aliases),
                "enabled": m.enabled,
            }
            for m in rc.models
        ],
    }


@router.get("/household")
def household_info() -> Dict[str, Any]:
    hh = _hh()
    c = hh.config
    return {
        "people": [
            {
                "id": p.id,
                "name": p.name,
                "role": p.role,
                "channels": sorted(p.identities),
            }
            for p in c.people
        ],
        "models": {
            "local": c.models.local,
            "cloud": c.models.cloud,
            "cloud_alt": c.models.cloud_alt,
            "family": c.models.family,
            "cloud_enabled": c.models.cloud_enabled,
        },
        "ignore_groups": c.ignore_groups,
        "approval_timeout_seconds": c.approval.timeout_seconds,
    }


__all__ = ["router"]
