from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from oracle_app.memory.orchestrations import (
    complete_orchestration_run,
    create_orchestration_run,
    delete_orchestration_run,
    get_orchestration_run,
    list_orchestration_runs,
    reconcile_interrupted_orchestration_runs,
    upsert_orchestration_step,
    update_orchestration_run,
    utc_now_iso,
)

from .models import (
    TERMINAL_RUN_STATUSES,
    DuplicateRunActivationError,
    RunbookActivation,
    RunbookDefinitionRef,
    validate_run_transition,
)


_COMPATIBILITY_KINDS = frozenset({"recovery", "routine"})


class RunbookRepository:
    """Kernel-facing persistence boundary over the current orchestration store.

    Slice 1 does not route existing controllers through this class. It provides
    the additive metadata and transition boundary that later slices can adopt
    one controller at a time.
    """

    def __init__(self, *, db_path: Path | None = None) -> None:
        self._db_path = db_path

    def create_run(
        self,
        definition: RunbookDefinitionRef,
        activation: RunbookActivation,
        *,
        status: str = "running",
        summary: str = "",
        controller_state: dict[str, Any] | None = None,
        payload: dict[str, Any] | None = None,
        preview_id: str = "",
        digest: str = "",
        approval_consumed: bool = False,
    ) -> dict[str, Any]:
        if definition.kind not in _COMPATIBILITY_KINDS:
            raise ValueError(
                f"Runbook kind {definition.kind!r} is not supported by the compatibility store."
            )
        try:
            return create_orchestration_run(
                run_id=activation.run_id,
                orchestration_id=definition.definition_id,
                kind=definition.kind,
                status=status,
                started_at=activation.started_at,
                preview_id=preview_id,
                digest=digest,
                client_id=activation.client_id,
                summary=summary,
                approval_consumed=approval_consumed,
                definition_domain=definition.domain,
                definition_version=definition.version,
                correlation_key=activation.correlation_key,
                activation_idempotency_key=activation.idempotency_key,
                controller_version=definition.controller_version,
                controller_state=controller_state,
                payload=payload,
                db_path=self._db_path,
            )
        except sqlite3.IntegrityError as exc:
            if activation.idempotency_key and self.find_by_activation_idempotency_key(
                activation.idempotency_key
            ):
                raise DuplicateRunActivationError(
                    f"Runbook activation {activation.idempotency_key!r} already exists."
                ) from exc
            raise

    def record_operation(
        self,
        *,
        run_id: str,
        operation_id: str,
        ordinal: int,
        status: str,
        operation_kind: str = "",
        target_id: str = "",
        target_label: str = "",
        capability_id: str = "",
        policy_id: str = "",
        summary: str = "",
        request_id: str = "",
        error_class: str = "",
        verification_status: str = "",
        started_at: str = "",
        completed_at: str = "",
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return upsert_orchestration_step(
            run_id=run_id,
            step_id=operation_id,
            ordinal=ordinal,
            status=status,
            target_type=operation_kind,
            target_id=target_id,
            target_label=target_label,
            action_id=capability_id,
            policy_id=policy_id,
            summary=summary,
            request_id=request_id,
            error_class=error_class,
            verification_status=verification_status,
            started_at=started_at,
            completed_at=completed_at,
            payload=payload,
            db_path=self._db_path,
        )

    def transition_run(
        self,
        run_id: str,
        *,
        status: str,
        summary: str,
        controller_state: dict[str, Any] | None = None,
        payload: dict[str, Any] | None = None,
        cancellation_reason: str | None = None,
        cancellation_requester: str | None = None,
        completed_at: str | None = None,
    ) -> dict[str, Any]:
        current = self.require_run(run_id)
        validate_run_transition(str(current["status"]), status)
        resolved_payload = current["payload"] if payload is None else payload
        resolved_controller_state = (
            current["controller_state"] if controller_state is None else controller_state
        )
        if status == "canceled" and not str(cancellation_reason or "").strip():
            raise ValueError("Canceled runs require a cancellation reason.")
        if status in TERMINAL_RUN_STATUSES:
            updated = complete_orchestration_run(
                run_id,
                status=status,
                summary=summary,
                completed_at=completed_at or utc_now_iso(),
                controller_state=resolved_controller_state,
                cancellation_reason=cancellation_reason,
                cancellation_requester=cancellation_requester,
                payload=resolved_payload,
                db_path=self._db_path,
            )
        else:
            updated = update_orchestration_run(
                run_id,
                status=status,
                summary=summary,
                controller_state=resolved_controller_state,
                payload=resolved_payload,
                db_path=self._db_path,
            )
        if updated is None:
            raise RuntimeError(f"Runbook run {run_id!r} disappeared during transition.")
        return updated

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        return get_orchestration_run(run_id, db_path=self._db_path)

    def delete_run(self, run_id: str) -> None:
        delete_orchestration_run(run_id, db_path=self._db_path)

    def require_run(self, run_id: str) -> dict[str, Any]:
        run = self.get_run(run_id)
        if run is None:
            raise KeyError(f"Runbook run {run_id!r} was not found.")
        return run

    def list_runs(
        self,
        *,
        definition_id: str | None = None,
        kind: str | None = None,
        domain: str | None = None,
        status: str | None = None,
        correlation_key: str | None = None,
        limit: int = 25,
    ) -> list[dict[str, Any]]:
        return list_orchestration_runs(
            orchestration_id=definition_id,
            kind=kind,
            definition_domain=domain,
            status=status,
            correlation_key=correlation_key,
            limit=limit,
            db_path=self._db_path,
        )

    def find_by_activation_idempotency_key(self, key: str) -> dict[str, Any] | None:
        matches = list_orchestration_runs(
            activation_idempotency_key=key,
            limit=1,
            db_path=self._db_path,
        )
        return matches[0] if matches else None

    def reconcile_interrupted_runs(self) -> int:
        return reconcile_interrupted_orchestration_runs(db_path=self._db_path)
