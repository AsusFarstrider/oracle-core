# Oracle Configuration Validation

## Canonical V2 Pipeline

The shared engine performs:

1. snapshot known candidate files;
2. restricted YAML parsing;
3. normalization and safe-default expansion;
4. whole-bundle typed validation;
5. RFC 8785 canonical JSON and semantic hashing;
6. secret-presence and activation compatibility checks;
7. semantic diff, provenance, restart impact, and safety acknowledgements;
8. satellite projection generation/validation; and
9. immutable persistence before atomic pointer selection.

Reports separate validation findings, activation blockers, and operational
readiness. Severity and `blocks_activation` are separate. Validators are
deterministic and never call providers or execute domain behavior.

Invalid candidates remain non-selectable. Successfully normalized invalid
graphs receive a normalized candidate revision; parse failures retain only
candidate/authored revisions. Raw failed secret submissions are deleted.

## V1 Migration Surface

Legacy Brain, interaction-runtime, and control-service resolution/reporting is
retained for importer and characterization coverage, not as live deployment
authority. Canonical Brain startup audits the fixed V1 environment/local/domain
locations through the generated inventory and rejects any populated mapped,
retired, malformed, or unclassified behavior input. Canonical satellite
components similarly reject legacy behavior CLI/environment inputs.

## Runtime Reporting

Canonical reports identify selected and applied activation/config/secret or
projection generations, restart requirement, provenance, and drift. Public
health stays shallow and never exposes config or secret detail.
