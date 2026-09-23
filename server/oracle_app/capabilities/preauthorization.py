from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import re

from oracle_app.configuration.safety_acknowledgements import (
    RUNBOOK_POWER_EXPANSION_ACKNOWLEDGEMENT,
)

from .semantic import (
    BlastRadius,
    ConfirmationClass,
    SemanticCapabilityRegistry,
    SemanticDirection,
    blast_radius_at_most,
)


_CANONICAL_ID = re.compile(r"^[a-z][a-z0-9]*(?:[_-][a-z0-9]+)*$")
_TRIGGERS = frozenset({"schedule", "home_event", "presence", "network_event", "alert_event"})


@dataclass(frozen=True, order=True)
class PreauthorizedPower:
    capability_id: str
    semantic_direction: SemanticDirection
    blast_radius: BlastRadius
    target_ids: tuple[str, ...]
    automatic_triggers: tuple[str, ...] = ()
    selector_bindings: tuple[tuple[str, str], ...] = ()
    argument_bindings: tuple[tuple[str, str], ...] = ()
    automatic_trigger_bindings: tuple[tuple[str, str, str], ...] = ()
    maximum_invocations: int = 1

    def __post_init__(self) -> None:
        if not isinstance(self.semantic_direction, SemanticDirection) or not isinstance(
            self.blast_radius, BlastRadius
        ):
            raise ValueError("Preauthorization direction and blast radius must be typed values.")
        if not self.target_ids or len(self.target_ids) > 64:
            raise ValueError("Preauthorization must name between one and 64 exact targets.")
        if tuple(sorted(set(self.target_ids))) != self.target_ids or any(
            _CANONICAL_ID.fullmatch(item) is None for item in self.target_ids
        ):
            raise ValueError("Preauthorization target IDs must be sorted unique canonical IDs.")
        if tuple(sorted(set(self.automatic_triggers))) != self.automatic_triggers or not set(
            self.automatic_triggers
        ).issubset(_TRIGGERS):
            raise ValueError("Automatic triggers must be sorted unique registered trigger kinds.")
        if tuple(sorted(set(self.selector_bindings))) != self.selector_bindings or any(
            _CANONICAL_ID.fullmatch(name) is None
            or _CANONICAL_ID.fullmatch(value) is None
            for name, value in self.selector_bindings
        ):
            raise ValueError("Preauthorization selectors must be sorted unique canonical bindings.")
        if tuple(sorted(set(self.argument_bindings))) != self.argument_bindings or any(
            _CANONICAL_ID.fullmatch(name) is None or not scope or len(scope) > 256
            for name, scope in self.argument_bindings
        ):
            raise ValueError("Preauthorization argument bindings must be sorted and bounded.")
        if not 1 <= self.maximum_invocations <= 320:
            raise ValueError("Preauthorization invocation bound must be finite.")
        if tuple(sorted(set(self.automatic_trigger_bindings))) != self.automatic_trigger_bindings or any(
            kind not in _TRIGGERS or _CANONICAL_ID.fullmatch(trigger_id) is None
            or not detail or len(detail) > 512
            for kind, trigger_id, detail in self.automatic_trigger_bindings
        ):
            raise ValueError("Automatic trigger bindings must be bounded and unique.")
        if {item[0] for item in self.automatic_trigger_bindings} != set(self.automatic_triggers) and self.automatic_trigger_bindings:
            raise ValueError("Automatic trigger kinds and exact bindings must agree.")


@dataclass(frozen=True)
class SafetyManifest:
    format: str
    powers: tuple[PreauthorizedPower, ...]
    digest: str

    def __post_init__(self) -> None:
        if self.format != "oracle-safety-manifest-v1":
            raise ValueError("Safety manifest format is invalid.")
        if not re.fullmatch(r"sha256:[0-9a-f]{64}", self.digest):
            raise ValueError("Safety manifest digest is invalid.")

    def to_sanitized_dict(self) -> dict[str, object]:
        return {
            "format": self.format,
            "powers": [_power_payload(item) for item in self.powers],
            "digest": self.digest,
        }


def build_safety_manifest(
    registry: SemanticCapabilityRegistry,
    powers: tuple[PreauthorizedPower, ...],
) -> SafetyManifest:
    ordered = tuple(sorted(set(powers)))
    if len(ordered) != len(powers):
        raise ValueError("Safety manifest powers must be unique.")
    for power in ordered:
        definition = registry.require(power.capability_id)
        directions = {direction for _, direction in definition.directions}
        if power.semantic_direction not in directions:
            raise ValueError("Preauthorization direction is not registered for the capability.")
        if not blast_radius_at_most(power.blast_radius, definition.maximum_blast_radius):
            raise ValueError("Preauthorization blast radius exceeds the capability maximum.")
        arguments = {item.name: item for item in definition.arguments}
        for name, value in power.selector_bindings:
            if name not in arguments:
                raise ValueError("Preauthorization selector is not a registered capability argument.")
            arguments[name].validate(value)
        if any(
            name not in arguments and not (name == "mapping_id" and definition.owner == "home_assistant")
            for name, _ in power.argument_bindings
        ):
            raise ValueError("Preauthorization argument binding is not registered for the capability.")
    payload = {
        "format": "oracle-safety-manifest-v1",
        "powers": [_power_payload(item) for item in ordered],
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return SafetyManifest(
        format="oracle-safety-manifest-v1",
        powers=ordered,
        digest=f"sha256:{hashlib.sha256(encoded).hexdigest()}",
    )


def required_safety_acknowledgements(
    registry: SemanticCapabilityRegistry,
    before: SafetyManifest | None,
    after: SafetyManifest,
) -> frozenset[str]:
    _verify_manifest(registry, after)
    if before is not None:
        _verify_manifest(registry, before)
    before_powers = frozenset() if before is None else frozenset(before.powers)
    additions = set(after.powers) - set(before_powers)
    if not additions:
        return frozenset()
    previous_by_semantics: dict[tuple[str, SemanticDirection], list[PreauthorizedPower]] = {}
    for power in before_powers:
        previous_by_semantics.setdefault(
            (power.capability_id, power.semantic_direction), []
        ).append(power)
    material = any(
        bool(power.automatic_triggers)
        or dict(registry.require(power.capability_id).confirmation_by_direction)[power.semantic_direction]
        is ConfirmationClass.CONSEQUENTIAL
        or any(
            power.selector_bindings != previous.selector_bindings
            or power.argument_bindings != previous.argument_bindings
            or power.automatic_trigger_bindings != previous.automatic_trigger_bindings
            or power.maximum_invocations > previous.maximum_invocations
            or
            not blast_radius_at_most(power.blast_radius, previous.blast_radius)
            or not set(power.target_ids).issubset(previous.target_ids)
            for previous in previous_by_semantics.get(
                (power.capability_id, power.semantic_direction), []
            )
        )
        for power in additions
    )
    return (
        frozenset({RUNBOOK_POWER_EXPANSION_ACKNOWLEDGEMENT})
        if material
        else frozenset()
    )


def _power_payload(power: PreauthorizedPower) -> dict[str, object]:
    payload: dict[str, object] = {
        "capability_id": power.capability_id,
        "semantic_direction": power.semantic_direction.value,
        "blast_radius": power.blast_radius.value,
        "target_ids": list(power.target_ids),
        "automatic_triggers": list(power.automatic_triggers),
    }
    if power.selector_bindings:
        payload["selector_bindings"] = [
            {"name": name, "value": value} for name, value in power.selector_bindings
        ]
    if power.argument_bindings:
        payload["argument_bindings"] = [
            {"name": name, "scope": scope} for name, scope in power.argument_bindings
        ]
    if power.automatic_trigger_bindings:
        payload["automatic_trigger_bindings"] = [
            {"kind": kind, "id": trigger_id, "scope": detail}
            for kind, trigger_id, detail in power.automatic_trigger_bindings
        ]
    if power.maximum_invocations != 1:
        payload["maximum_invocations"] = power.maximum_invocations
    return payload


def _verify_manifest(
    registry: SemanticCapabilityRegistry,
    manifest: SafetyManifest,
) -> None:
    rebuilt = build_safety_manifest(registry, manifest.powers)
    if rebuilt.format != manifest.format or rebuilt.digest != manifest.digest:
        raise ValueError("Safety manifest does not match its registered powers.")
