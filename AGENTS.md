# Maintaining Ninebot for Home Assistant

The installable integration is `custom_components/ninebot/`. Pick the user-visible behavior first, then open the corresponding row of the [module and test map](docs/development.md#start-with-a-task-not-the-whole-repository). [Architecture](docs/architecture.md) describes ownership. [Changelog](CHANGELOG.md) and [2.0 history](docs/releases/2.0.md) record decisions tied to a release.

## Contracts to preserve

| When working on | Keep these together |
|---|---|
| A current reading | Adapter, units, observation group, freshness, entity identity and translated label. Multiple entities reuse the same coordinator read. |
| Login or control | Account isolation, pinned dependency, authenticated loopback, password in request body, per-vehicle consent, bounded confirmation and observed state. |
| Historical acquisition | Source month, vehicle and ride identity, verified page and durable checkpoint. Retain the highest-quality saved geometry. |
| Assistant tools | Operation schema, source projection, original HA context, target-specific permission and final ownership check. Public help carries its version and permanent citation. |
| Map and replay | One map, shared selection and the active sample. Compare authoritative ride scalars. Keep incomplete tracks and missing measurements visible. Honor location access and clear geometry when access is withdrawn. |
| Measurement semantics | `ec` is Wh and `charging_power` is W. Preserve unknown enum values, point units and coordinate system. Trip maximum, whole-ride average and a raw point sample have different meanings. |

## Make the change reviewable

Start ordinary changes from `dev`. Identify the public contract, the smallest representative input and the check that exercises the normal entry point. Use synthetic accounts and rides for permissions, maps and model-host evaluations. Keep private captures and local paths outside the repository.

For independent changes, assign separate worktrees and file ownership. One integrator owns shared contracts, version and publication. A good handoff names the commit, what the user observes, the verification and any open question. Trace existing registry identity, cache ownership and cancellation paths before replacing them. [Contribution and release process](CONTRIBUTING.md)

Update the relevant guide in both languages, retain public section links, and pin changed retrieval material to a committed source revision. Runtime resources must be present in the packaged ZIP. [Documentation and validation](docs/development.md)
