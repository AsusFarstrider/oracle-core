# Shareable Configuration Bundle

Status: active canonical V2 model. The engine, importer/equivalence path,
immutable generation and activation lifecycle, Brain adoption, satellite
projection lifecycle, fleet cutover, and legacy-authority retirement are
implemented. Trusted-boundary System Mode transport remains Stage 10 work.

## Shape

Oracle uses one fixed multi-file bundle, not one monolithic YAML file and not a
deployment-defined include tree. Required roles are:

- `bundle.yaml`
- `brain.yaml`
- `access.yaml`
- `household.yaml`
- `satellites.yaml`

Fixed optional domain roles and their ownership are defined in
[`configuration-schema.md`](../reference/configuration-schema.md).

The complete generic example is under [`examples/config/`](../../examples/config/).
Example files never sit inside a live bundle root.

## Rules

- Files divide operator concerns, not authority.
- File presence never enables behavior.
- Unknown YAML roles, includes, wildcards, overlays, inheritance, and YAML merge
  behavior are forbidden.
- Provider selection and fallback are explicit and domain-owned.
- `secrets.env` is a separate deployment-local companion containing raw values;
  YAML contains logical secret references only.
- Public examples contain no household identities, hosts, paths, addresses, or
  raw secrets.
- Runtime consumes normalized immutable JSON generations, not authored YAML.

## Deployment Repositories

Reusable core owns schemas, migrations, tools, and generic examples. The private
deployment authority owns explicitly isolated household configuration. A
standard target consumes only its exact materialized household deployment
revision and separately supplied secrets; it does not require repository
access. The installed store owns runtime generations, projections, reports, and
audit.

Read-only/GitOps deployments supply the same complete bundle through
`external_read_only` authoring mode; Oracle does not create an override layer.

## Retired Inputs

The old `config/oracle.example.yaml`, `config/satellites.example.yaml`, JSON
definition files, environment variables, and retired CLI surfaces were
migration inputs, not the canonical target. They are rejected by canonical
startup and are not a supported runtime configuration path.
