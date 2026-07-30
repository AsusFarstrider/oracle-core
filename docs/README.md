# Oracle Documentation

Oracle's reusable documentation is organized by authority and purpose.

Start with the repository [README](../README.md) for the project overview and
minimal runtime boundary. When documents disagree, use this order:

1. `contracts/` defines required behavior and ownership.
2. `architecture/` explains how the reusable system is structured.
3. `config/` explains canonical configuration rules and operator-facing
   configuration surfaces.
4. `reference/` records schemas, interfaces, and other detailed descriptive
   material.
5. `runbooks/` provides bounded operational procedures.

Roadmaps, histories, and audits are development records rather than runtime
law. A core copy is included only when a reviewed distribution representation
is useful to core consumers.

## Useful Entry Points

- [configuration contract](contracts/configuration.md)
- [configuration architecture](architecture/configuration-bundle.md)
- [configuration schema](reference/configuration-schema.md)
- [standard configuration setup](config/setup.md)
- [API architecture](architecture/api.md)
- [routing architecture](architecture/router.md)
- [dispatch architecture](architecture/dispatch.md)
- [health architecture](architecture/health.md)
- [speech architecture](architecture/speech-stack.md)
- [test entrypoint](architecture/testing.md)

## Documentation Rules

- Do not change behavior without updating the applicable contract.
- Do not treat fallback or compatibility behavior as primary behavior.
- Keep household configuration, secrets, operational evidence, and private
  deployment details outside reusable documentation.
- Core documentation is derived from reviewed committed files in Oracle's
  private development authority. Corrections begin there and arrive through a
  later approved snapshot promotion.
