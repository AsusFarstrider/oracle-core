# Minimal Household Deployment Template

This template initializes a provider-free standard Oracle Brain household. Its
canonical configuration source is [`../../config/`](../../config/), with all
optional integrations disabled through supported configuration.

Initialization records the exact private source commit and this manifest's Git
blob and SHA-256 identities. It copies the declared configuration roles into a
new isolated private household root, then replaces only the declared household
identity placeholders. The initialized definition has no continuing sync
relationship with this template; later template changes require an explicit
reviewed migration.

The default installation profile is `minimal-brain`, ingress is host-local, and
no logical secrets are required. `secrets.env` remains a separately supplied,
untracked authoritative companion and never enters a household artifact.

Use `scripts/household_deployment.py` to resolve and materialize a committed
private household definition. The resulting ledger and deployment revision are
identities over the explicitly selected payload, not over the multi-household
authoring repository.
