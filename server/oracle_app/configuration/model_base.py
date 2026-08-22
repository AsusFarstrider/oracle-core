from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field


CANONICAL_ID_PATTERN = r"^[a-z][a-z0-9]*(?:[_-][a-z0-9]+)*$"
SECRET_REFERENCE_PATTERN = r"^[A-Z][A-Z0-9_]*$"

CanonicalId = Annotated[str, Field(min_length=1, max_length=128, pattern=CANONICAL_ID_PATTERN)]
SecretReference = Annotated[
    str,
    Field(
        min_length=1,
        max_length=128,
        pattern=SECRET_REFERENCE_PATTERN,
        json_schema_extra={"x-oracle-secret-reference": True},
    ),
]
DisplayText = Annotated[str, Field(min_length=1, max_length=256)]


class ConfigurationModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class EmptyConfiguration(ConfigurationModel):
    pass
