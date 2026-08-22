from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .loader import LoadedBundle, load_bundle_snapshot_structure, snapshot_candidate, validate_loaded_bundle
from .normalization import ConfigurationCanonicalizationError, NormalizedBundle, normalize_bundle
from .provenance import ProvenanceEntry, collect_provenance
from .secrets import SecretCompanionError, SecretSnapshot, load_secret_companion, validate_secret_snapshot
from .validation import BundleValidationError, ConfigurationFinding
from .transition_validation import TransitionValidationResult


@dataclass(frozen=True)
class ValidationReport:
    validation_findings: tuple[ConfigurationFinding, ...] = ()
    activation_blockers: tuple[ConfigurationFinding, ...] = ()
    transition_blockers: tuple[ConfigurationFinding, ...] = ()
    readiness_findings: tuple[ConfigurationFinding, ...] = ()

    @property
    def activation_eligible(self) -> bool:
        return not any(
            item.blocks_activation
            for item in self.validation_findings + self.activation_blockers + self.transition_blockers
        )


@dataclass(frozen=True)
class CandidateInspection:
    report: ValidationReport
    candidate_id: str
    authored_revision: str
    normalized_candidate_revision: str | None = None
    bundle: LoadedBundle | None = None
    normalized: NormalizedBundle | None = None
    provenance: tuple[ProvenanceEntry, ...] = ()
    secrets: SecretSnapshot | None = None
    transition_validation: TransitionValidationResult | None = None


_LOAD_COMPANION = object()


def inspect_candidate(
    root: Path,
    *,
    secret_snapshot: SecretSnapshot | object = _LOAD_COMPANION,
) -> CandidateInspection:
    snapshot = snapshot_candidate(root)
    try:
        bundle = load_bundle_snapshot_structure(snapshot)
        normalized = normalize_bundle(bundle)
        semantic_findings = validate_loaded_bundle(bundle)
        if semantic_findings:
            return CandidateInspection(
                report=ValidationReport(validation_findings=semantic_findings),
                candidate_id=snapshot.candidate_id,
                authored_revision=snapshot.authored_revision,
                normalized_candidate_revision=normalized.config_revision,
                bundle=bundle,
                normalized=normalized,
                provenance=collect_provenance(bundle),
            )
        secrets = load_secret_companion(bundle.root) if secret_snapshot is _LOAD_COMPANION else secret_snapshot
        if not isinstance(secrets, SecretSnapshot):
            raise TypeError("secret_snapshot must be a SecretSnapshot.")
    except BundleValidationError as exc:
        return CandidateInspection(
            report=ValidationReport(validation_findings=exc.findings),
            candidate_id=snapshot.candidate_id,
            authored_revision=snapshot.authored_revision,
        )
    except ConfigurationCanonicalizationError as exc:
        finding = ConfigurationFinding(
            code="config.canonicalization.invalid",
            file_role="bundle",
            path="",
            message=str(exc),
        )
        return CandidateInspection(
            report=ValidationReport(validation_findings=(finding,)),
            candidate_id=snapshot.candidate_id,
            authored_revision=snapshot.authored_revision,
        )
    except SecretCompanionError as exc:
        path = "" if exc.line is None else f"line {exc.line}"
        finding = ConfigurationFinding(
            code=exc.code,
            file_role="secrets.env",
            path=path,
            message=str(exc),
            owner="secrets",
        )
        return CandidateInspection(
            report=ValidationReport(validation_findings=(finding,)),
            candidate_id=snapshot.candidate_id,
            authored_revision=snapshot.authored_revision,
        )
    blockers, warnings = validate_secret_snapshot(bundle, secrets)
    return CandidateInspection(
        report=ValidationReport(validation_findings=warnings, activation_blockers=blockers),
        candidate_id=snapshot.candidate_id,
        authored_revision=snapshot.authored_revision,
        normalized_candidate_revision=normalized.config_revision,
        bundle=bundle,
        normalized=normalized,
        provenance=collect_provenance(bundle),
        secrets=secrets,
    )
