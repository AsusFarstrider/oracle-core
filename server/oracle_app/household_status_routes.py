from __future__ import annotations

from datetime import UTC, datetime

from fastapi import FastAPI, HTTPException, Request

from .brain_application_composition import BRAIN_APPLICATION_COMPOSITION_STATE_KEY, CanonicalBrainApplicationComposition
from .communication_modes import reconcile_communication_modes
from .home_assistant_presence import read_home_assistant_presence
from .presence_intents import PresenceQuery


def household_control_status(composition: CanonicalBrainApplicationComposition) -> dict[str, object]:
    runtime = composition.runtime
    now = datetime.now(UTC)
    try:
        mode = reconcile_communication_modes(household=runtime.household, now=now)
        dnd = {"status": "unconfigured"} if mode is None else {
            "status": "active" if mode.active else "inactive",
            "expires_at": mode.expires_at.isoformat() if mode.expires_at else None,
        }
    except Exception:
        dnd = {"status": "unknown"}

    home_assistant = runtime.home_assistant
    if home_assistant is None or not home_assistant.enabled:
        presence: dict[str, object] = {"status": "disabled", "people": []}
    else:
        try:
            result = read_home_assistant_presence(
                PresenceQuery(kind="list", desired_state="home"),
                household_settings=runtime.household,
                home_assistant_settings=home_assistant,
            )
            presence = {
                "status": "available" if result.get("ok") else "unavailable",
                "people": [
                    {"display_name": item["display_name"], "state": item["state"]}
                    for item in result.get("people", []) if isinstance(item, dict)
                    and item.get("state") in {"home", "away", "unknown"}
                    and isinstance(item.get("display_name"), str)
                ],
            }
        except Exception:
            presence = {"status": "unavailable", "people": []}
    collisions = sorted(home_assistant.callable_alias_collisions) if home_assistant is not None else []
    domains = {}
    for name, execution in (("lists", composition.lists_execution), ("notes", composition.notes_execution)):
        if execution is None:
            domains[name] = {"status": "disabled"}
            continue
        try:
            health = execution.health()
            domains[name] = {
                "status": health.get("status") if health.get("status") in {"ok", "degraded", "disabled"} else "unavailable",
                "configured_lists": health.get("configured_lists", 0) if name == "lists" else None,
            }
        except Exception:
            domains[name] = {"status": "unavailable"}
    return {
        "ok": True, "generated_at": now.isoformat(), "refresh_after_seconds": 30,
        "dnd": dnd, "presence": presence, "callable_collision_warnings": collisions,
        "lists": domains["lists"], "notes": domains["notes"],
    }


def household_control_status_http(request: Request) -> dict[str, object]:
    composition = getattr(request.app.state, BRAIN_APPLICATION_COMPOSITION_STATE_KEY, None)
    if not isinstance(composition, CanonicalBrainApplicationComposition):
        raise HTTPException(status_code=503, detail="Canonical application composition is unavailable.")
    return household_control_status(composition)


def register_household_status_routes(app: FastAPI) -> None:
    app.get("/api/ui/household-status")(household_control_status_http)
    app.get("/api/admin/household-status")(household_control_status_http)
