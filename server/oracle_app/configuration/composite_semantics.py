"""Static binding and review-only safety facts for bounded composites."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import hashlib
import json
from typing import Mapping

from oracle_app.capabilities.preauthorization import (
    PreauthorizedPower,
    SafetyManifest,
    build_safety_manifest,
)
from oracle_app.capabilities.semantic import (
    BlastRadius,
    SEMANTIC_CAPABILITY_REGISTRY,
    SemanticDirection,
    UnknownSemanticCapabilityError,
    ValueKind,
)

from .composite_definition import (
    CapabilityOperation,
    ChildOperation,
    CompositeCondition,
    CompositeDefinition,
    CompositeInput,
    InputReference,
    ScheduleCompositeTrigger,
)


# Registered Oracle state evidence, not provider payload paths or expressions.
_STATE_FIELDS = {
    "home_target_state": {"state": {"on", "off", "open", "closed", "locked", "unlocked"}},
    "audiobook_playback": {"state": {"playing", "paused", "stopped"}},
}
_WAIT_PREDICATES = frozenset(_STATE_FIELDS)


@dataclass(frozen=True)
class CompositeInputResolution:
    status: str
    values: Mapping[str, bool | int | str]
    missing: tuple[str, ...]


class ConditionTruth(StrEnum):
    TRUE = "true"
    FALSE = "false"
    UNKNOWN = "unknown"


def evaluate_composite_condition(condition: CompositeCondition, observed: object | None) -> ConditionTruth:
    """Unknown is a distinct fact, never the false branch."""
    if observed is None or (condition.source in {"state", "result"} and observed == "unknown"):
        return ConditionTruth.UNKNOWN
    numeric_comparison = (
        condition.operator == "greater_than"
        and isinstance(observed, (int, float)) and not isinstance(observed, bool)
        and isinstance(condition.value, (int, float)) and not isinstance(condition.value, bool)
    )
    if type(observed) is not type(condition.value) and not numeric_comparison:
        return ConditionTruth.UNKNOWN
    try:
        if condition.operator == "equals":
            matched = observed == condition.value
        elif condition.operator == "not_equals":
            matched = observed != condition.value
        else:
            if isinstance(observed, bool) or isinstance(condition.value, bool):
                return ConditionTruth.UNKNOWN
            if not isinstance(observed, (int, float)):
                return ConditionTruth.UNKNOWN
            matched = observed > condition.value
    except (TypeError, ValueError):
        return ConditionTruth.UNKNOWN
    return ConditionTruth.TRUE if matched else ConditionTruth.FALSE


def resolve_composite_inputs(
    composition: CompositeDefinition,
    supplied: Mapping[str, object],
    *,
    invocation: str,
) -> CompositeInputResolution:
    """Pure input boundary: humans clarify; unattended invocations fail closed."""
    if invocation not in {"manual", "automatic"}:
        raise ValueError("Composite invocation must be manual or automatic.")
    if set(supplied) - set(composition.inputs):
        raise ValueError("Composite invocation contains undeclared input.")
    values: dict[str, bool | int | str] = {}
    missing: list[str] = []
    for input_id, definition in composition.inputs.items():
        value = supplied.get(input_id, definition.default)
        if value is None:
            missing.append(input_id)
            continue
        _validate_input_value(definition, value)
        values[input_id] = value  # type: ignore[assignment]
    if missing:
        return CompositeInputResolution(
            status="clarify" if invocation == "manual" else "missing_input",
            values=values,
            missing=tuple(sorted(missing)),
        )
    return CompositeInputResolution("resolved", values, ())


def _validate_input_value(definition: CompositeInput, value: object) -> None:
    definition.validate_value(value)


def _sample_input(definition: CompositeInput) -> bool | int | str:
    if definition.default is not None:
        return definition.default
    if definition.type == "boolean":
        return False
    if definition.type == "integer":
        return definition.minimum  # type: ignore[return-value]
    return definition.allowed_values[0]


def _validate_condition(condition: CompositeCondition, composition: CompositeDefinition, by_id: Mapping[str, object]) -> None:
    if condition.source == "input":
        if condition.field != "value":
            raise ValueError("Input condition can read only its declared value.")
        definition = composition.inputs[condition.reference_id]
        _validate_input_value(definition, condition.value)
        if condition.operator == "greater_than" and definition.type != "integer":
            raise ValueError("Greater-than condition requires a bounded integer.")
    elif condition.source == "result":
        referenced = by_id[condition.reference_id]
        assert isinstance(referenced, CapabilityOperation)
        capability = SEMANTIC_CAPABILITY_REGISTRY.require(referenced.capability_id)
        fields = {item.name: item for item in capability.results}
        if condition.field not in fields:
            raise ValueError("Result condition field is not registered for the capability.")
        field = fields[condition.field]
        field.validate(condition.value)
        if condition.operator == "greater_than" and field.kind not in {ValueKind.INTEGER, ValueKind.NUMBER}:
            raise ValueError("Greater-than result condition requires a numeric field.")
    else:
        fields = _STATE_FIELDS.get(condition.reference_id)
        if fields is None or condition.field not in fields or condition.value not in fields[condition.field]:
            raise ValueError("State condition must use a registered finite predicate and value.")
        if condition.operator == "greater_than":
            raise ValueError("State condition cannot use numeric comparison.")


def _validate_capability(operation: CapabilityOperation, composition: CompositeDefinition) -> tuple[SemanticDirection, BlastRadius, tuple[tuple[str, str], ...], tuple[tuple[str, str], ...], tuple[str, ...]]:
    if operation.capability_id == "notifications.submit":
        raise ValueError("Composite notifications must use the configured notification-type trigger, not arbitrary message submission.")
    capability = SEMANTIC_CAPABILITY_REGISTRY.require(operation.capability_id)
    definitions = {item.name: item for item in capability.arguments}
    if set(operation.arguments) != set(definitions):
        raise ValueError("Composite capability must bind every registered argument exactly.")
    resolved: dict[str, object] = {}
    for name, value in operation.arguments.items():
        if isinstance(value, InputReference):
            if name in set(capability.blast_radius_inputs) | {capability.direction_argument, "action_id"}:
                raise ValueError("Power selector and target arguments must be static exact bindings.")
            definition = composition.inputs[value.input_id]
            if (definition.type == "boolean" and definitions[name].kind is not ValueKind.BOOLEAN) or (
                definition.type == "integer" and definitions[name].kind not in {ValueKind.INTEGER, ValueKind.NUMBER}
            ) or (definition.type == "string" and definitions[name].kind not in {ValueKind.TEXT, ValueKind.ENUM, ValueKind.CANONICAL_ID}):
                raise ValueError("Composite input type does not match the capability argument.")
            if definition.type == "integer":
                lower, upper = definition.minimum, definition.maximum
                if definitions[name].minimum is not None and lower < definitions[name].minimum:  # type: ignore[operator]
                    raise ValueError("Composite input range exceeds the capability argument.")
                if definitions[name].maximum is not None and upper > definitions[name].maximum:  # type: ignore[operator]
                    raise ValueError("Composite input range exceeds the capability argument.")
            if definition.type == "string":
                for allowed in definition.allowed_values:
                    definitions[name].validate(allowed)
            resolved[name] = _sample_input(definition)
        else:
            resolved[name] = value
    capability.validate_arguments(resolved)
    direction, _ = capability.risk(resolved)
    target_ids = tuple(sorted(str(resolved[name]) for name in capability.blast_radius_inputs))
    if not target_ids:
        raise ValueError("Composite capability must declare a registered exact target.")
    selectors = tuple(sorted(
        (name, str(resolved[name]))
        for name in {capability.direction_argument, "action_id"} & set(resolved)
    ))
    scopes: list[tuple[str, str]] = []
    for name, value in operation.arguments.items():
        if name in capability.blast_radius_inputs:
            continue
        if isinstance(value, InputReference):
            input_definition = composition.inputs[value.input_id]
            scope = f"input:{value.input_id}:{input_definition.type}"
            if input_definition.type == "integer":
                scope += f":{input_definition.minimum}..{input_definition.maximum}"
            elif input_definition.type == "string":
                allowed = ",".join(input_definition.allowed_values)
                scope += ":" + (
                    allowed if len(allowed) <= 180 else "sha256:" + hashlib.sha256(allowed.encode("utf-8")).hexdigest()
                )
        elif definitions[name].kind is ValueKind.TEXT:
            scope = "sha256:" + hashlib.sha256(str(value).encode("utf-8")).hexdigest()
        else:
            scope = str(value).lower() if isinstance(value, bool) else str(value)
        scopes.append((name, scope))
    return direction, capability.maximum_blast_radius, selectors, tuple(sorted(scopes)), tuple(sorted(set(target_ids)))


def _trigger_bindings(composition: CompositeDefinition) -> tuple[tuple[str, str, str], ...]:
    bindings: list[tuple[str, str, str]] = []
    for trigger in composition.automatic_triggers:
        if isinstance(trigger, ScheduleCompositeTrigger):
            detail = f"household_local={trigger.household_local_hour:02d}:{trigger.household_local_minute:02d};weekdays={','.join(str(day) for day in sorted(trigger.weekdays))}"
        else:
            detail = f"evidence={trigger.evidence_id};state={trigger.expected_state}"
        if trigger.inputs:
            encoded = json.dumps(trigger.inputs, sort_keys=True, separators=(",", ":"))
            detail += ";inputs=sha256:" + hashlib.sha256(encoded.encode("utf-8")).hexdigest()
        bindings.append((trigger.kind, trigger.id, detail))
    return tuple(sorted(bindings))


def validate_and_manifest_composite(
    composition: CompositeDefinition,
    *,
    child_manifests: Mapping[str, SafetyManifest] | None = None,
) -> SafetyManifest:
    """Validate code-owned types and render the bounded power envelope."""
    by_id = {operation.id: operation for operation in composition.operations}
    powers: dict[tuple[object, ...], int] = {}
    trigger_bindings = _trigger_bindings(composition)
    trigger_kinds = tuple(sorted({item[0] for item in trigger_bindings}))
    for operation in composition.operations:
        if operation.condition is not None:
            _validate_condition(operation.condition, composition, by_id)
        if operation.type == "wait_until":
            if operation.predicate_id not in _WAIT_PREDICATES:
                raise ValueError("Wait-until predicate is not registered.")
            if operation.expected_value not in _STATE_FIELDS[operation.predicate_id]["state"]:
                raise ValueError("Wait-until expected value is not a registered state.")
            if operation.poll_seconds > operation.timeout_seconds:
                raise ValueError("Wait-until polling interval exceeds its timeout.")
        if isinstance(operation, CapabilityOperation):
            try:
                capability = SEMANTIC_CAPABILITY_REGISTRY.require(operation.capability_id)
            except UnknownSemanticCapabilityError as exc:
                raise ValueError("Composite capability is not registered in Oracle code.") from exc
            if operation.retry.max_attempts > 1 and not capability.safe_for_bounded_retry:
                raise ValueError("Capability is not registered safe for bounded retry.")
            direction, radius, selectors, argument_bindings, target_ids = _validate_capability(operation, composition)
            key = (operation.capability_id, direction, radius, target_ids, trigger_kinds, selectors, argument_bindings, trigger_bindings)
            powers[key] = powers.get(key, 0) + operation.repeat.count * operation.retry.max_attempts
        elif isinstance(operation, ChildOperation):
            if child_manifests is None or operation.definition_id not in child_manifests:
                raise ValueError("Composite child must resolve to a statically named enabled definition.")
            child = child_manifests[operation.definition_id]
            for power in child.powers:
                key = (power.capability_id, power.semantic_direction, power.blast_radius,
                       power.target_ids, trigger_kinds, power.selector_bindings, power.argument_bindings, trigger_bindings)
                powers[key] = powers.get(key, 0) + power.maximum_invocations * operation.repeat.count
    built = tuple(
        PreauthorizedPower(
            capability_id=capability_id,
            semantic_direction=direction,
            blast_radius=radius,
            target_ids=target_ids,
            automatic_triggers=trigger_kinds,
            selector_bindings=selectors,
            argument_bindings=argument_bindings,
            automatic_trigger_bindings=trigger_bindings,
            maximum_invocations=count,
        )
        for (capability_id, direction, radius, target_ids, trigger_kinds, selectors, argument_bindings, trigger_bindings), count in powers.items()
    )
    return build_safety_manifest(SEMANTIC_CAPABILITY_REGISTRY, built)


def validate_composite_collection(definitions: list[object]) -> Mapping[str, SafetyManifest]:
    """Close child references and cycles without invoking any runtime owner."""
    by_id = {str(getattr(item, "id")): item for item in definitions}
    manifests: dict[str, SafetyManifest] = {}
    expanded_counts: dict[str, int] = {}
    visiting: set[str] = set()

    def visit(definition_id: str, depth: int) -> tuple[SafetyManifest, int]:
        if depth > 4:
            raise ValueError("Composite child nesting exceeds the fixed depth bound.")
        if definition_id in visiting:
            raise ValueError("Composite child definitions contain a cycle.")
        definition = by_id[definition_id]
        composition = getattr(definition, "composition")
        if composition is None:
            raise ValueError("A bounded composite child must use the bounded composite definition format.")
        if definition_id in manifests:
            return manifests[definition_id], expanded_counts[definition_id]
        if composition.automatic_triggers and not composition.preauthorize_consequential:
            raise ValueError("Automatic composite triggers require a reviewed preauthorization envelope.")
        visiting.add(definition_id)
        child_manifests: dict[str, SafetyManifest] = {}
        operation_count = len(composition.operations)
        for operation in composition.operations:
            if not isinstance(operation, ChildOperation):
                continue
            child = by_id.get(operation.definition_id)
            if child is None or not getattr(child, "enabled"):
                raise ValueError("Composite child must name an enabled Oracle runbook.")
            if getattr(child, "user_id") != getattr(definition, "user_id"):
                raise ValueError("Composite child cannot change the canonical owning user.")
            if not set(getattr(child, "source_ids")).issubset(getattr(definition, "source_ids")):
                raise ValueError("Composite child cannot expand the parent source scope.")
            child_inputs = child.composition.inputs if child.composition is not None else {}
            if set(operation.inputs) - set(child_inputs) or any(
                input_id not in operation.inputs and input_definition.required
                for input_id, input_definition in child_inputs.items()
            ):
                raise ValueError("Composite child inputs do not satisfy its declared bounded input contract.")
            for input_id, value in operation.inputs.items():
                child_input = child_inputs[input_id]
                if isinstance(value, InputReference):
                    parent_input = composition.inputs[value.input_id]
                    if parent_input.type != child_input.type:
                        raise ValueError("Composite child input reference changes its type.")
                    if parent_input.type == "integer" and (
                        parent_input.minimum < child_input.minimum or parent_input.maximum > child_input.maximum
                    ):
                        raise ValueError("Composite child input reference widens integer bounds.")
                    if parent_input.type == "string" and not set(parent_input.allowed_values).issubset(child_input.allowed_values):
                        raise ValueError("Composite child input reference widens allowed values.")
                else:
                    _validate_input_value(child_input, value)
            child_manifest, child_count = visit(operation.definition_id, depth + 1)
            operation_count += child_count * operation.repeat.count
            child_manifests[operation.definition_id] = child_manifest
        if operation_count > 64:
            raise ValueError("Composite expanded child operation count exceeds the fixed bound.")
        manifest = validate_and_manifest_composite(composition, child_manifests=child_manifests)
        visiting.remove(definition_id)
        manifests[definition_id] = manifest
        expanded_counts[definition_id] = operation_count
        return manifest, operation_count

    for definition in definitions:
        if getattr(definition, "composition") is not None:
            visit(str(getattr(definition, "id")), 1)
    return manifests


def legacy_routine_safety_manifest(definition: object, action_mappings: Mapping[str, object]) -> SafetyManifest:
    """Review the existing interpreter's exact configured powers; do not reauthorize it."""
    powers: dict[tuple[object, ...], int] = {}

    def add(capability_id: str, arguments: Mapping[str, object], *, mapping_key: str | None = None) -> None:
        capability = SEMANTIC_CAPABILITY_REGISTRY.require(capability_id)
        direction, _ = capability.risk(arguments)
        targets = tuple(sorted(set(str(arguments[name]) for name in capability.blast_radius_inputs)))
        selectors = tuple(sorted(
            (name, str(arguments[name]))
            for name in {capability.direction_argument, "action_id"} & set(arguments)
        ))
        scopes = tuple(sorted(
            (name, str(value)) for name, value in arguments.items()
            if name not in capability.blast_radius_inputs
        ))
        if mapping_key is not None:
            scopes += (("mapping_id", mapping_key),)
        key = (capability_id, direction, capability.maximum_blast_radius, targets, selectors, tuple(sorted(scopes)))
        powers[key] = powers.get(key, 0) + 1

    def action(action_id: str) -> None:
        mapping = action_mappings.get(action_id)
        if mapping is None or getattr(mapping, "kind", None) != "action":
            raise ValueError("Compatibility action lacks its exact configured mapping.")
        operations = getattr(mapping, "allowed_operations")
        if len(operations) != 1:
            raise ValueError("Compatibility action must select exactly one operation.")
        operation = operations[0]
        target_id = str(getattr(mapping, "oracle_id"))
        provider_domain = str(getattr(mapping, "entity_id")).split(".", 1)[0]
        if operation == "invoke":
            add("home.provider_action.invoke", {"target_id": target_id, "action_id": target_id}, mapping_key=action_id)
        elif operation in {"turn_on", "turn_off"}:
            capability_id = "home.lights.set" if provider_domain == "light" else "home.power.set"
            add(capability_id, {"target_id": target_id, "state": "on" if operation == "turn_on" else "off"}, mapping_key=action_id)
        elif operation in {"arm", "close", "disarm", "lock", "open", "unlock"}:
            state = {"arm": "armed", "close": "closed", "disarm": "disarmed", "lock": "locked", "open": "open", "unlock": "unlocked"}[operation]
            add("home.access.set", {"target_id": target_id, "state": state}, mapping_key=action_id)
        else:
            raise ValueError("Compatibility action cannot be represented as a static reviewed power.")

    for step in getattr(definition, "steps"):
        if step.type == "ui_action":
            action(step.action_id)
        elif step.type == "audiobook_start":
            add("audiobooks.start_current", {"user_id": step.user_id, "source_id": step.source_id})
            if step.duration_seconds is not None or step.duration_input is not None:
                # A run input is bounded in the compatibility definition; the
                # exact accepted value is frozen later by the existing owner.
                duration = step.duration_seconds or 1
                add("audiobooks.sleep_timer.set", {"source_id": step.source_id, "duration_seconds": duration})
        elif step.type == "sleep_timer":
            duration = step.duration_seconds or 1
            add("audiobooks.sleep_timer.set", {"source_id": step.source_id, "duration_seconds": duration})
        elif step.type == "notification":
            add("notifications.trigger", {"notification_id": step.notification_id})
        elif step.type == "timer_sound":
            add("alerts.sound", {"source_id": step.source_id})
        remediation = getattr(step, "remediation_action_id", None)
        if remediation == "stop_audiobook":
            raise ValueError("Legacy audiobook-stop remediation is not yet represented by the safety registry.")
        if remediation is not None:
            action(remediation)
    built = tuple(
        PreauthorizedPower(
            capability_id=capability_id,
            semantic_direction=direction,
            blast_radius=radius,
            target_ids=targets,
            selector_bindings=selectors,
            argument_bindings=scopes,
            maximum_invocations=count,
        )
        for (capability_id, direction, radius, targets, selectors, scopes), count in powers.items()
    )
    return build_safety_manifest(SEMANTIC_CAPABILITY_REGISTRY, built)


def composite_review_digest(definition: object) -> str:
    """Hash execution-relevant definition facts, excluding presentation wording."""
    composition = getattr(definition, "composition")
    if composition is None:
        raise ValueError("Review digest requires a bounded composite definition.")
    content = composition.model_dump(mode="json")
    for operation in content["operations"]:
        operation.pop("label", None)
    for input_definition in content["inputs"].values():
        input_definition.pop("prompt", None)
    content["user_id"] = getattr(definition, "user_id")
    content["source_ids"] = sorted(getattr(definition, "source_ids"))
    content["manual_triggers"] = getattr(definition, "triggers").model_dump(mode="json")
    content["enabled"] = bool(getattr(definition, "enabled"))
    encoded = json.dumps(content, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()
