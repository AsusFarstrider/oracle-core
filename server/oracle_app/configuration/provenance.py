from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel

from .loader import LoadedBundle


@dataclass(frozen=True, order=True)
class ProvenanceEntry:
    file_role: str
    path: str
    source: str


def _collect(value: Any, *, file_role: str, path: str) -> list[ProvenanceEntry]:
    entries: list[ProvenanceEntry] = []
    if isinstance(value, BaseModel):
        for field_name in value.__class__.model_fields:
            field_source = "authored" if field_name in value.model_fields_set else "defaulted"
            field_path = f"{path}.{field_name}" if path else field_name
            entries.append(ProvenanceEntry(file_role, field_path, field_source))
            entries.extend(
                _collect(
                    getattr(value, field_name),
                    file_role=file_role,
                    path=field_path,
                )
            )
    elif isinstance(value, list):
        for index, item in enumerate(value):
            entries.extend(
                _collect(
                    item,
                    file_role=file_role,
                    path=f"{path}[{index}]",
                )
            )
    elif isinstance(value, dict):
        for key, item in value.items():
            entries.extend(
                _collect(
                    item,
                    file_role=file_role,
                    path=f"{path}.{key}" if path else str(key),
                )
            )
    return entries


def collect_provenance(bundle: LoadedBundle) -> tuple[ProvenanceEntry, ...]:
    entries: list[ProvenanceEntry] = []
    for file_role, model in bundle.roles.items():
        entries.extend(_collect(model, file_role=file_role, path=""))
    return tuple(sorted(entries))
