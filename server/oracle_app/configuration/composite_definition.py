"""Bounded Stage 8 composite definition language."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field, model_validator

from .model_base import CanonicalId, ConfigurationModel, DisplayText


class CompositeInput(ConfigurationModel):
    type: Literal["boolean", "integer", "string"]
    required: bool = False
    default: bool | int | str | None = None
    minimum: int | None = None
    maximum: int | None = None
    allowed_values: list[Annotated[str, Field(min_length=1, max_length=128)]] = Field(default_factory=list)
    prompt: DisplayText | None = None

    @model_validator(mode="after")
    def bounded(self) -> CompositeInput:
        if self.required and self.default is not None:
            raise ValueError("Required composite input cannot have a default.")
        if not self.required and self.default is None:
            raise ValueError("Optional composite input requires a default.")
        if self.required and self.prompt is None:
            raise ValueError("Required composite input requires a manual clarification prompt.")
        if self.type == "boolean":
            if self.default is not None and not isinstance(self.default, bool):
                raise ValueError("Boolean composite input default must be boolean.")
            if self.minimum is not None or self.maximum is not None or self.allowed_values:
                raise ValueError("Boolean composite input cannot declare numeric or string bounds.")
        elif self.type == "integer":
            if self.minimum is None or self.maximum is None or self.minimum > self.maximum:
                raise ValueError("Integer composite input requires ordered finite bounds.")
            if self.minimum < -86400 or self.maximum > 86400 or self.allowed_values:
                raise ValueError("Integer composite input exceeds the bounded composite range.")
            if self.default is not None and (isinstance(self.default, bool) or not isinstance(self.default, int)
                                             or not self.minimum <= self.default <= self.maximum):
                raise ValueError("Integer composite input default is outside its bounds.")
        else:
            if self.minimum is not None or self.maximum is not None:
                raise ValueError("String composite input cannot declare numeric bounds.")
            if not self.allowed_values or len(self.allowed_values) > 32 or len(set(self.allowed_values)) != len(self.allowed_values):
                raise ValueError("String composite input requires a bounded unique allowed-value set.")
            if self.default is not None and self.default not in self.allowed_values:
                raise ValueError("String composite input default must be an allowed value.")
        return self

    def validate_value(self, value: object) -> None:
        if self.type == "boolean":
            valid = isinstance(value, bool)
        elif self.type == "integer":
            valid = (
                isinstance(value, int)
                and not isinstance(value, bool)
                and self.minimum <= value <= self.maximum  # type: ignore[operator]
            )
        else:
            valid = isinstance(value, str) and value in self.allowed_values
        if not valid:
            raise ValueError("Composite invocation input is outside its declared type or bounds.")


class InputReference(ConfigurationModel):
    input_id: CanonicalId


class CompositeCondition(ConfigurationModel):
    source: Literal["input", "result", "state"]
    reference_id: CanonicalId
    field: CanonicalId
    target_id: CanonicalId | None = None
    operator: Literal["equals", "not_equals", "greater_than"]
    value: bool | int | float | str
    on_unknown: Literal["stop", "skip", "proceed", "fallback"] = "stop"
    fallback_operation_id: CanonicalId | None = None

    @model_validator(mode="after")
    def fallback_is_explicit(self) -> CompositeCondition:
        if (self.on_unknown == "fallback") != (self.fallback_operation_id is not None):
            raise ValueError("Unknown-condition fallback requires exactly one named operation.")
        if (self.source == "state") != (self.target_id is not None):
            raise ValueError("Only a registered state condition may name an exact target.")
        return self


class CompositeRetry(ConfigurationModel):
    max_attempts: Annotated[int, Field(ge=1, le=3)] = 1
    delay_seconds: Annotated[int, Field(ge=0, le=3600)] = 0


class CompositeRepeat(ConfigurationModel):
    count: Annotated[int, Field(ge=1, le=10)] = 1


class CompositeOperationBase(ConfigurationModel):
    id: CanonicalId
    label: DisplayText
    phase: Literal["main", "fallback", "on_failure", "on_cancel"] = "main"
    depends_on: list[CanonicalId] = Field(default_factory=list, max_length=32)
    critical: bool = True
    condition: CompositeCondition | None = None
    retry: CompositeRetry = Field(default_factory=CompositeRetry)
    repeat: CompositeRepeat = Field(default_factory=CompositeRepeat)


class CapabilityOperation(CompositeOperationBase):
    type: Literal["capability"]
    capability_id: Annotated[str, Field(pattern=r"^[a-z][a-z0-9]*(?:[._-][a-z0-9]+)+$", max_length=128)]
    arguments: dict[CanonicalId, bool | int | float | str | InputReference]


class FixedWaitOperation(CompositeOperationBase):
    type: Literal["wait"]
    duration_seconds: Annotated[int, Field(ge=1, le=86400)]
    max_lateness_seconds: Annotated[int, Field(ge=0, le=86400)]


class WaitUntilOperation(CompositeOperationBase):
    type: Literal["wait_until"]
    predicate_id: CanonicalId
    target_id: CanonicalId
    expected_value: Annotated[str, Field(min_length=1, max_length=128)]
    timeout_seconds: Annotated[int, Field(ge=1, le=86400)]
    poll_seconds: Annotated[int, Field(ge=1, le=3600)]


class ChildOperation(CompositeOperationBase):
    type: Literal["child"]
    definition_id: CanonicalId
    inputs: dict[CanonicalId, bool | int | str | InputReference] = Field(default_factory=dict)


class ScheduleCompositeTrigger(ConfigurationModel):
    id: CanonicalId
    kind: Literal["schedule"]
    household_local_hour: Annotated[int, Field(ge=0, le=23)]
    household_local_minute: Annotated[int, Field(ge=0, le=59)]
    weekdays: list[Annotated[int, Field(ge=0, le=6)]] = Field(min_length=1, max_length=7)
    inputs: dict[CanonicalId, bool | int | str] = Field(
        default_factory=dict, max_length=16, exclude_if=lambda value: not value
    )

    @model_validator(mode="after")
    def unique_days(self) -> ScheduleCompositeTrigger:
        if len(set(self.weekdays)) != len(self.weekdays):
            raise ValueError("Schedule weekdays must be unique.")
        return self


class EvidenceCompositeTrigger(ConfigurationModel):
    id: CanonicalId
    kind: Literal["home_event", "presence", "network_event", "alert_event"]
    evidence_id: CanonicalId
    expected_state: Annotated[str, Field(min_length=1, max_length=128)]
    inputs: dict[CanonicalId, bool | int | str] = Field(
        default_factory=dict, max_length=16, exclude_if=lambda value: not value
    )


CompositeTrigger = Annotated[
    ScheduleCompositeTrigger | EvidenceCompositeTrigger,
    Field(discriminator="kind"),
]


CompositeOperation = Annotated[
    CapabilityOperation | FixedWaitOperation | WaitUntilOperation | ChildOperation,
    Field(discriminator="type"),
]


class CompositeDefinition(ConfigurationModel):
    run_policy: Literal["fail_fast", "best_effort"] = "fail_fast"
    preauthorize_consequential: bool = False
    inputs: dict[CanonicalId, CompositeInput] = Field(default_factory=dict, max_length=16)
    automatic_triggers: list[CompositeTrigger] = Field(default_factory=list, max_length=16)
    operations: list[CompositeOperation] = Field(min_length=1, max_length=32)
    on_failure: list[CanonicalId] = Field(default_factory=list, max_length=16)
    on_cancel: list[CanonicalId] = Field(default_factory=list, max_length=16)

    @model_validator(mode="after")
    def validate_graph(self) -> CompositeDefinition:
        by_id = {operation.id: operation for operation in self.operations}
        if len(by_id) != len(self.operations):
            raise ValueError("Composite operation IDs must be unique.")
        if len({trigger.id for trigger in self.automatic_triggers}) != len(self.automatic_triggers):
            raise ValueError("Composite automatic trigger IDs must be unique.")
        for trigger in self.automatic_triggers:
            if set(trigger.inputs) - set(self.inputs):
                raise ValueError("Composite automatic trigger supplies an undeclared input.")
            for input_id, value in trigger.inputs.items():
                self.inputs[input_id].validate_value(value)
        for operation in self.operations:
            if len(set(operation.depends_on)) != len(operation.depends_on):
                raise ValueError("Composite dependencies must be unique.")
            if not set(operation.depends_on).issubset(by_id) or operation.id in operation.depends_on:
                raise ValueError("Composite dependency must name another static operation.")
            if any(by_id[dependency].phase != operation.phase for dependency in operation.depends_on):
                raise ValueError("Composite dependencies must remain inside one execution phase.")
            condition = operation.condition
            if condition and condition.source == "input" and condition.reference_id not in self.inputs:
                raise ValueError("Composite condition references an undefined input.")
            if condition and condition.source == "result" and (
                condition.reference_id not in operation.depends_on
                or not isinstance(by_id[condition.reference_id], CapabilityOperation)
            ):
                raise ValueError("Composite result condition requires a direct capability dependency.")
            if condition and condition.fallback_operation_id is not None:
                if operation.phase == "fallback":
                    raise ValueError("Fallback operations cannot recursively select another fallback.")
                fallback = by_id.get(condition.fallback_operation_id)
                if fallback is None or fallback.phase != "fallback":
                    raise ValueError("Unknown-condition fallback must name a dedicated fallback operation.")
            for value in (operation.arguments.values() if isinstance(operation, CapabilityOperation)
                          else operation.inputs.values() if isinstance(operation, ChildOperation) else ()):
                if isinstance(value, InputReference) and value.input_id not in self.inputs:
                    raise ValueError("Composite operation references an undefined input.")
            if isinstance(operation, (FixedWaitOperation, WaitUntilOperation, ChildOperation)) and operation.retry.max_attempts != 1:
                raise ValueError("Only registered capability operations may retry.")
        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(operation_id: str) -> None:
            if operation_id in visiting:
                raise ValueError("Composite dependency graph contains a cycle.")
            if operation_id in visited:
                return
            visiting.add(operation_id)
            for dependency in by_id[operation_id].depends_on:
                visit(dependency)
            visiting.remove(operation_id)
            visited.add(operation_id)

        for operation_id in by_id:
            visit(operation_id)
        for branch in (self.on_failure, self.on_cancel):
            if len(set(branch)) != len(branch) or not set(branch).issubset(by_id):
                raise ValueError("Compensation must name unique static operations.")
        if set(self.on_failure) & set(self.on_cancel):
            raise ValueError("Failure and cancel compensation cannot share an operation.")
        fallback_ids = [
            item.condition.fallback_operation_id
            for item in self.operations
            if item.condition is not None and item.condition.fallback_operation_id is not None
        ]
        if len(set(fallback_ids)) != len(fallback_ids) or {item.id for item in self.operations if item.phase == "fallback"} != set(fallback_ids):
            raise ValueError("Each dedicated fallback operation must have exactly one unknown-condition owner.")
        if {item.id for item in self.operations if item.phase == "on_failure"} != set(self.on_failure):
            raise ValueError("Failure compensation phase and explicit plan must match.")
        if {item.id for item in self.operations if item.phase == "on_cancel"} != set(self.on_cancel):
            raise ValueError("Cancel compensation phase and explicit plan must match.")
        if not any(item.phase == "main" for item in self.operations):
            raise ValueError("Composite definition requires a main operation.")
        return self
