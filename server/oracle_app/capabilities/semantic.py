from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import re
from types import MappingProxyType
from typing import Mapping


_CANONICAL_ID = re.compile(r"^[a-z][a-z0-9]*(?:[_-][a-z0-9]+)*$")
_CAPABILITY_ID = re.compile(r"^[a-z][a-z0-9]*(?:[._-][a-z0-9]+)+$")


class UnknownSemanticCapabilityError(KeyError):
    pass


class InvalidCapabilityArgumentsError(ValueError):
    pass


class ValueKind(StrEnum):
    BOOLEAN = "boolean"
    CANONICAL_ID = "canonical_id"
    ENUM = "enum"
    INTEGER = "integer"
    NUMBER = "number"
    TEXT = "text"


class SemanticDirection(StrEnum):
    ACTIVATE = "activate"
    COMMUNICATE = "communicate"
    DEACTIVATE = "deactivate"
    EXECUTE = "execute"
    EXPOSE = "expose"
    ADJUST = "adjust"
    SECURE = "secure"


class ConfirmationClass(StrEnum):
    ORDINARY = "ordinary"
    CONSEQUENTIAL = "consequential"


class BlastRadius(StrEnum):
    TARGET = "target"
    ROOM = "room"
    HOUSEHOLD = "household"
    INFRASTRUCTURE = "infrastructure"


_BLAST_RADIUS_ORDER = {
    BlastRadius.TARGET: 0,
    BlastRadius.ROOM: 1,
    BlastRadius.HOUSEHOLD: 2,
    BlastRadius.INFRASTRUCTURE: 3,
}


class RetryTrait(StrEnum):
    NEVER = "never"
    BOUNDED = "bounded"


class ReconciliationTrait(StrEnum):
    NONE = "none"
    READ_AFTER_WRITE = "read_after_write"
    OPERATION_STATUS = "operation_status"


class CancellationTrait(StrEnum):
    NONE = "none"
    BEFORE_DISPATCH = "before_dispatch"
    ACTIVE_OPERATION = "active_operation"


class CapabilityOutcome(StrEnum):
    VERIFIED = "verified"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    UNSUPPORTED = "unsupported"
    UNAVAILABLE = "unavailable"
    FAILED = "failed"
    UNKNOWN = "unknown"
    CANCELED = "canceled"


@dataclass(frozen=True)
class ValueDefinition:
    name: str
    kind: ValueKind
    required: bool = True
    enum_values: tuple[str, ...] = ()
    minimum: float | None = None
    maximum: float | None = None
    max_length: int | None = None

    def __post_init__(self) -> None:
        if _CANONICAL_ID.fullmatch(self.name) is None:
            raise ValueError("Capability value names must be canonical IDs.")
        if self.kind is ValueKind.ENUM:
            if not self.enum_values or tuple(sorted(set(self.enum_values))) != self.enum_values:
                raise ValueError("Enum values must be a non-empty sorted unique tuple.")
        elif self.enum_values:
            raise ValueError("Only enum values may define enum_values.")
        if self.minimum is not None and self.maximum is not None and self.minimum > self.maximum:
            raise ValueError("Capability value minimum cannot exceed maximum.")
        if self.max_length is not None and self.max_length < 1:
            raise ValueError("Capability value max_length must be positive.")

    def validate(self, value: object) -> object:
        if self.kind is ValueKind.BOOLEAN:
            valid = isinstance(value, bool)
        elif self.kind is ValueKind.CANONICAL_ID:
            valid = isinstance(value, str) and _CANONICAL_ID.fullmatch(value) is not None
        elif self.kind is ValueKind.ENUM:
            valid = isinstance(value, str) and value in self.enum_values
        elif self.kind is ValueKind.INTEGER:
            valid = isinstance(value, int) and not isinstance(value, bool)
        elif self.kind is ValueKind.NUMBER:
            valid = isinstance(value, (int, float)) and not isinstance(value, bool)
        else:
            valid = isinstance(value, str) and bool(value.strip())
        if not valid:
            raise InvalidCapabilityArgumentsError(f"Capability value {self.name!r} is invalid.")
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            if self.minimum is not None and value < self.minimum:
                raise InvalidCapabilityArgumentsError(f"Capability value {self.name!r} is below its bound.")
            if self.maximum is not None and value > self.maximum:
                raise InvalidCapabilityArgumentsError(f"Capability value {self.name!r} exceeds its bound.")
        if isinstance(value, str) and self.max_length is not None and len(value) > self.max_length:
            raise InvalidCapabilityArgumentsError(f"Capability value {self.name!r} exceeds its length bound.")
        return value


@dataclass(frozen=True)
class SemanticCapabilityDefinition:
    capability_id: str
    owner: str
    arguments: tuple[ValueDefinition, ...]
    results: tuple[ValueDefinition, ...]
    direction_argument: str | None
    directions: tuple[tuple[str, SemanticDirection], ...]
    confirmation_by_direction: tuple[tuple[SemanticDirection, ConfirmationClass], ...]
    blast_radius_inputs: tuple[str, ...]
    maximum_blast_radius: BlastRadius
    retry: RetryTrait
    reconciliation: ReconciliationTrait
    cancellation: CancellationTrait
    idempotent: bool
    safe_for_bounded_retry: bool
    opaque_provider_unit: bool = False

    def __post_init__(self) -> None:
        if _CAPABILITY_ID.fullmatch(self.capability_id) is None or _CANONICAL_ID.fullmatch(self.owner) is None:
            raise ValueError("Capability and owner IDs must be canonical.")
        argument_names = tuple(item.name for item in self.arguments)
        result_names = tuple(item.name for item in self.results)
        if len(set(argument_names)) != len(argument_names) or len(set(result_names)) != len(result_names):
            raise ValueError("Capability argument and result names must be unique.")
        if not set(self.blast_radius_inputs).issubset(argument_names):
            raise ValueError("Blast-radius inputs must name declared arguments.")
        direction_map = dict(self.directions)
        if len(direction_map) != len(self.directions) or not direction_map:
            raise ValueError("Capability directions must be non-empty and unique.")
        if self.direction_argument is None:
            if set(direction_map) != {"*"}:
                raise ValueError("Fixed semantic direction must use the '*' selector.")
        else:
            argument = next((item for item in self.arguments if item.name == self.direction_argument), None)
            if argument is None or argument.kind is not ValueKind.ENUM or set(direction_map) != set(argument.enum_values):
                raise ValueError("Directional selectors must cover one declared enum argument exactly.")
        confirmation_map = dict(self.confirmation_by_direction)
        if set(confirmation_map) != set(direction_map.values()):
            raise ValueError("Every semantic direction must have one confirmation class.")
        if self.safe_for_bounded_retry:
            if self.retry is not RetryTrait.BOUNDED:
                raise ValueError("Safe bounded retry requires the bounded retry trait.")
            if not self.idempotent and self.reconciliation is ReconciliationTrait.NONE:
                raise ValueError("Safe bounded retry requires idempotency or reconciliation.")

    def validate_arguments(self, values: Mapping[str, object]) -> Mapping[str, object]:
        if not isinstance(values, Mapping):
            raise InvalidCapabilityArgumentsError("Capability arguments must be a mapping.")
        definitions = {item.name: item for item in self.arguments}
        unknown = set(values) - set(definitions)
        missing = {item.name for item in self.arguments if item.required and item.name not in values}
        if unknown or missing:
            raise InvalidCapabilityArgumentsError("Capability arguments do not match the closed definition.")
        validated = {name: definitions[name].validate(value) for name, value in values.items()}
        return MappingProxyType(validated)

    def validate_result(self, values: Mapping[str, object]) -> Mapping[str, object]:
        if not isinstance(values, Mapping):
            raise InvalidCapabilityArgumentsError("Capability result must be a mapping.")
        definitions = {item.name: item for item in self.results}
        unknown = set(values) - set(definitions)
        missing = {item.name for item in self.results if item.required and item.name not in values}
        if unknown or missing:
            raise InvalidCapabilityArgumentsError("Capability result does not match the closed definition.")
        validated = {name: definitions[name].validate(value) for name, value in values.items()}
        return MappingProxyType(validated)

    def risk(self, values: Mapping[str, object]) -> tuple[SemanticDirection, ConfirmationClass]:
        arguments = self.validate_arguments(values)
        selector = "*" if self.direction_argument is None else str(arguments[self.direction_argument])
        direction = dict(self.directions)[selector]
        return direction, dict(self.confirmation_by_direction)[direction]


@dataclass(frozen=True)
class CapabilityResult:
    capability_id: str
    outcome: CapabilityOutcome
    message_code: str
    operation_id: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.capability_id, str) or _CAPABILITY_ID.fullmatch(self.capability_id) is None:
            raise ValueError("Capability result ID must be canonical.")
        if not isinstance(self.message_code, str) or _CANONICAL_ID.fullmatch(self.message_code) is None:
            raise ValueError("Capability result message code must be a canonical ID.")
        if self.operation_id is not None and (
            not isinstance(self.operation_id, str)
            or len(self.operation_id) > 160
            or _CANONICAL_ID.fullmatch(self.operation_id) is None
        ):
            raise ValueError("Capability operation ID must be a bounded canonical ID.")

    def sanitized_audit(self) -> dict[str, object]:
        return {
            "capability_id": self.capability_id,
            "outcome": self.outcome.value,
            "message_code": self.message_code,
            "operation_id": self.operation_id,
        }


class SemanticCapabilityRegistry:
    def __init__(self, definitions: tuple[SemanticCapabilityDefinition, ...]) -> None:
        by_id = {item.capability_id: item for item in definitions}
        if len(by_id) != len(definitions):
            raise ValueError("Semantic capability IDs must be unique.")
        self._definitions = MappingProxyType(by_id)

    @property
    def capability_ids(self) -> tuple[str, ...]:
        return tuple(sorted(self._definitions))

    def require(self, capability_id: str) -> SemanticCapabilityDefinition:
        try:
            return self._definitions[capability_id]
        except KeyError as exc:
            raise UnknownSemanticCapabilityError(capability_id) from exc

    def assess(
        self,
        capability_id: str,
        arguments: Mapping[str, object],
        *,
        interface: str,
    ) -> tuple[SemanticDirection, ConfirmationClass]:
        if interface not in {"voice", "ui", "runbook", "system_mode"}:
            raise ValueError("Capability interface must be a supported Oracle interface.")
        return self.require(capability_id).risk(arguments)


def _argument(name: str, kind: ValueKind, **kwargs: object) -> ValueDefinition:
    return ValueDefinition(name=name, kind=kind, **kwargs)


def _result_fields(*extra: ValueDefinition) -> tuple[ValueDefinition, ...]:
    return (
        _argument(
            "outcome",
            ValueKind.ENUM,
            enum_values=tuple(sorted(item.value for item in CapabilityOutcome)),
        ),
        *extra,
    )


SEMANTIC_CAPABILITY_REGISTRY = SemanticCapabilityRegistry(
    (
        SemanticCapabilityDefinition(
            capability_id="home.lights.set",
            owner="home_assistant",
            arguments=(
                _argument("target_id", ValueKind.CANONICAL_ID),
                _argument("state", ValueKind.ENUM, enum_values=("off", "on")),
            ),
            results=_result_fields(_argument("verified_state", ValueKind.ENUM, enum_values=("off", "on"))),
            direction_argument="state",
            directions=(("off", SemanticDirection.DEACTIVATE), ("on", SemanticDirection.ACTIVATE)),
            confirmation_by_direction=(
                (SemanticDirection.ACTIVATE, ConfirmationClass.ORDINARY),
                (SemanticDirection.DEACTIVATE, ConfirmationClass.ORDINARY),
            ),
            blast_radius_inputs=("target_id",),
            maximum_blast_radius=BlastRadius.HOUSEHOLD,
            retry=RetryTrait.BOUNDED,
            reconciliation=ReconciliationTrait.READ_AFTER_WRITE,
            cancellation=CancellationTrait.BEFORE_DISPATCH,
            idempotent=True,
            safe_for_bounded_retry=True,
        ),
        SemanticCapabilityDefinition(
            capability_id="home.power.set",
            owner="home_assistant",
            arguments=(
                _argument("target_id", ValueKind.CANONICAL_ID),
                _argument("state", ValueKind.ENUM, enum_values=("off", "on")),
            ),
            results=_result_fields(_argument("verified_state", ValueKind.ENUM, enum_values=("off", "on"))),
            direction_argument="state",
            directions=(("off", SemanticDirection.DEACTIVATE), ("on", SemanticDirection.ACTIVATE)),
            confirmation_by_direction=(
                (SemanticDirection.ACTIVATE, ConfirmationClass.ORDINARY),
                (SemanticDirection.DEACTIVATE, ConfirmationClass.ORDINARY),
            ),
            blast_radius_inputs=("target_id",),
            maximum_blast_radius=BlastRadius.TARGET,
            retry=RetryTrait.BOUNDED,
            reconciliation=ReconciliationTrait.READ_AFTER_WRITE,
            cancellation=CancellationTrait.BEFORE_DISPATCH,
            idempotent=True,
            safe_for_bounded_retry=True,
        ),
        SemanticCapabilityDefinition(
            capability_id="home.environment.setpoint",
            owner="home_assistant",
            arguments=(
                _argument("target_id", ValueKind.CANONICAL_ID),
                _argument("setpoint", ValueKind.NUMBER, minimum=-50, maximum=150),
                _argument("temperature_unit", ValueKind.ENUM, enum_values=("celsius", "fahrenheit")),
            ),
            results=_result_fields(_argument("verified_setpoint", ValueKind.NUMBER, minimum=-50, maximum=150)),
            direction_argument=None,
            directions=(("*", SemanticDirection.ADJUST),),
            confirmation_by_direction=((SemanticDirection.ADJUST, ConfirmationClass.ORDINARY),),
            blast_radius_inputs=("target_id",),
            maximum_blast_radius=BlastRadius.TARGET,
            retry=RetryTrait.BOUNDED,
            reconciliation=ReconciliationTrait.READ_AFTER_WRITE,
            cancellation=CancellationTrait.BEFORE_DISPATCH,
            idempotent=True,
            safe_for_bounded_retry=True,
        ),
        SemanticCapabilityDefinition(
            capability_id="home.access.set",
            owner="home_assistant",
            arguments=(
                _argument("target_id", ValueKind.CANONICAL_ID),
                _argument(
                    "state",
                    ValueKind.ENUM,
                    enum_values=("armed", "closed", "disarmed", "locked", "open", "unlocked"),
                ),
            ),
            results=_result_fields(_argument("verified_state", ValueKind.ENUM, enum_values=("armed", "closed", "disarmed", "locked", "open", "unlocked"))),
            direction_argument="state",
            directions=(
                ("armed", SemanticDirection.SECURE),
                ("closed", SemanticDirection.SECURE),
                ("disarmed", SemanticDirection.EXPOSE),
                ("locked", SemanticDirection.SECURE),
                ("open", SemanticDirection.EXPOSE),
                ("unlocked", SemanticDirection.EXPOSE),
            ),
            confirmation_by_direction=(
                (SemanticDirection.EXPOSE, ConfirmationClass.CONSEQUENTIAL),
                (SemanticDirection.SECURE, ConfirmationClass.ORDINARY),
            ),
            blast_radius_inputs=("target_id",),
            maximum_blast_radius=BlastRadius.TARGET,
            retry=RetryTrait.NEVER,
            reconciliation=ReconciliationTrait.READ_AFTER_WRITE,
            cancellation=CancellationTrait.BEFORE_DISPATCH,
            idempotent=True,
            safe_for_bounded_retry=False,
        ),
        SemanticCapabilityDefinition(
            capability_id="home.provider_action.invoke",
            owner="home_assistant",
            arguments=(
                _argument("action_id", ValueKind.CANONICAL_ID),
                _argument("target_id", ValueKind.CANONICAL_ID),
            ),
            results=_result_fields(),
            direction_argument=None,
            directions=(("*", SemanticDirection.EXECUTE),),
            confirmation_by_direction=((SemanticDirection.EXECUTE, ConfirmationClass.CONSEQUENTIAL),),
            blast_radius_inputs=("target_id",),
            maximum_blast_radius=BlastRadius.HOUSEHOLD,
            retry=RetryTrait.NEVER,
            reconciliation=ReconciliationTrait.OPERATION_STATUS,
            cancellation=CancellationTrait.BEFORE_DISPATCH,
            idempotent=False,
            safe_for_bounded_retry=False,
            opaque_provider_unit=True,
        ),
        SemanticCapabilityDefinition(
            capability_id="audiobooks.start_current",
            owner="audiobooks",
            arguments=(
                _argument("user_id", ValueKind.CANONICAL_ID),
                _argument("source_id", ValueKind.CANONICAL_ID),
            ),
            results=_result_fields(),
            direction_argument=None,
            directions=(("*", SemanticDirection.ACTIVATE),),
            confirmation_by_direction=((SemanticDirection.ACTIVATE, ConfirmationClass.ORDINARY),),
            blast_radius_inputs=("user_id", "source_id"),
            maximum_blast_radius=BlastRadius.TARGET,
            retry=RetryTrait.NEVER,
            reconciliation=ReconciliationTrait.OPERATION_STATUS,
            cancellation=CancellationTrait.ACTIVE_OPERATION,
            idempotent=False,
            safe_for_bounded_retry=False,
        ),
        SemanticCapabilityDefinition(
            capability_id="audiobooks.sleep_timer.set",
            owner="audiobooks",
            arguments=(
                _argument("source_id", ValueKind.CANONICAL_ID),
                _argument("duration_seconds", ValueKind.INTEGER, minimum=1, maximum=86400),
            ),
            results=_result_fields(),
            direction_argument=None,
            directions=(("*", SemanticDirection.ADJUST),),
            confirmation_by_direction=((SemanticDirection.ADJUST, ConfirmationClass.ORDINARY),),
            blast_radius_inputs=("source_id",),
            maximum_blast_radius=BlastRadius.TARGET,
            retry=RetryTrait.NEVER,
            reconciliation=ReconciliationTrait.OPERATION_STATUS,
            cancellation=CancellationTrait.BEFORE_DISPATCH,
            idempotent=False,
            safe_for_bounded_retry=False,
        ),
        SemanticCapabilityDefinition(
            capability_id="alerts.sound",
            owner="alerts",
            arguments=(_argument("source_id", ValueKind.CANONICAL_ID),),
            results=_result_fields(),
            direction_argument=None,
            directions=(("*", SemanticDirection.COMMUNICATE),),
            confirmation_by_direction=((SemanticDirection.COMMUNICATE, ConfirmationClass.ORDINARY),),
            blast_radius_inputs=("source_id",),
            maximum_blast_radius=BlastRadius.TARGET,
            retry=RetryTrait.NEVER,
            reconciliation=ReconciliationTrait.NONE,
            cancellation=CancellationTrait.BEFORE_DISPATCH,
            idempotent=False,
            safe_for_bounded_retry=False,
        ),
        SemanticCapabilityDefinition(
            capability_id="notifications.submit",
            owner="notifications",
            arguments=(
                _argument("notification_id", ValueKind.CANONICAL_ID),
                _argument("message", ValueKind.TEXT, max_length=500),
            ),
            results=_result_fields(),
            direction_argument=None,
            directions=(("*", SemanticDirection.COMMUNICATE),),
            confirmation_by_direction=((SemanticDirection.COMMUNICATE, ConfirmationClass.ORDINARY),),
            blast_radius_inputs=("notification_id",),
            maximum_blast_radius=BlastRadius.HOUSEHOLD,
            retry=RetryTrait.BOUNDED,
            reconciliation=ReconciliationTrait.OPERATION_STATUS,
            cancellation=CancellationTrait.BEFORE_DISPATCH,
            idempotent=False,
            safe_for_bounded_retry=True,
        ),
        SemanticCapabilityDefinition(
            capability_id="notifications.trigger",
            owner="notifications",
            arguments=(_argument("notification_id", ValueKind.CANONICAL_ID),),
            results=_result_fields(),
            direction_argument=None,
            directions=(("*", SemanticDirection.COMMUNICATE),),
            confirmation_by_direction=((SemanticDirection.COMMUNICATE, ConfirmationClass.ORDINARY),),
            blast_radius_inputs=("notification_id",),
            maximum_blast_radius=BlastRadius.HOUSEHOLD,
            retry=RetryTrait.NEVER,
            reconciliation=ReconciliationTrait.OPERATION_STATUS,
            cancellation=CancellationTrait.BEFORE_DISPATCH,
            idempotent=False,
            safe_for_bounded_retry=False,
        ),
    )
)


def blast_radius_at_most(actual: BlastRadius, maximum: BlastRadius) -> bool:
    return _BLAST_RADIUS_ORDER[actual] <= _BLAST_RADIUS_ORDER[maximum]
