# Development and validation

[Documentation](README.md) · [Architecture](architecture.md) · [Product constraints](../AGENTS.md)

## Start with a task, not the whole repository

Inspect Git state and user changes first. Read the relevant guide/contract, then its code and fixture provenance. Release notes explain history; they are not instructions to reimplement completed milestones. No private workstation path or external developer workspace is required to understand this repository.

| Task | Main source boundary | Relevant offline evidence |
|---|---|---|
| Authentication/setup | config_flow/session/runtime/client | test_config_flow, test_sms_login, test_session, test_client |
| Fields/entities/identity | adapters/models/entity/sensor/binary_sensor/migration/registry | Field inventory, fixture metadata, entity/migration/translation tests |
| History acquisition | bootstrap/history_plan/preservation/incremental_runs/history_store | History lifecycle/protocol/retry/coverage tests |
| Archive/integrity | ride_archive/detail_codec/object_checks/local_reconstruction | Archive/corruption/recovery/recorded-trip tests |
| Maps/local query | frontend/, history_websocket, recorded_actions | tests/frontend/, tests/browser/, authorization/detail tests |
| Documentation/branding | README/docs/NOTICE/brand | Links, examples, factual/bilingual parity, asset provenance and packaging |

Names identify module/test families; use `rg --files` and targeted search to select actual files rather than treating every family as a command.

## Repository layout

```text
custom_components/ninebot/   All installable runtime code and assets
  frontend/                 Bundled card, modules and vendored map library
  brand/                    Integration branding
  licenses/                 Runtime attribution and source pins
  translations/             HA locales; strings.json is the English source
tests/                      Offline HA tests, synthetic browser/Node checks
  fixtures/                 Recorded sanitized and synthetic inputs with metadata
scripts/                    Development-only generation/profiling utilities
.github/workflows/          Small automatic checks and scoped/manual test jobs
docs/                       Public usage, architecture, migration and development
```

## Select the smallest meaningful checks

| Change | Required scope |
|---|---|
| Public prose/links | Link/anchor and bilingual-fact checks, reviewed examples/assets; no HA environment. |
| Python behavior | Ruff/format, affected typing and targeted pytest/fixture replay. |
| Frontend behavior | Related Node tests; synthetic browser when rendering/interactions change. |
| Auth/identity/schema/compatibility | Failure/migration/authorization regressions; broader pinned HA matrix once at a major boundary. |
| Release packaging | HACS/Hassfest against the candidate, correct manifest/tag/release and runtime paths. |

**Checks** automatically runs lint; its complete HA/coverage matrix is `workflow_dispatch`, not every beta. **Focused statistics**, **History protocol** and **History browser** have affected-path scopes. **History profile** is an explicit synthetic measurement, not a timing SLA. Read current YAML before dispatching; a green lightweight badge does not prove the full matrix ran.

In an existing compatible isolated environment:

```sh
ruff check custom_components tests scripts
ruff format --check custom_components tests scripts
mypy custom_components/ninebot --follow-imports=silent
python -m pytest -q tests/test_config_flow.py  # Choose affected tests.
node --test tests/frontend/*.mjs
```

Do not install heavy HA environments for docs. Prefer Actions for expensive compatibility tests; do not repeat a successful unchanged suite without a new risk. Record candidate SHA, environment, checks and skips. Mock/fixture/CI success is not real cloud or physical-vehicle verification.

## Fixtures and privacy

Start with [fixture metadata](../tests/fixtures/ninecli/0.1.7/metadata.json) and [field-source mapping](../tests/fixtures/field_inventory/source-map.json). Distinguish recorded sanitized, synthetic and maintainer-confirmed inputs. Unknown units/aliases/CRS stay unverified until appropriate evidence exists.

No live cloud/SMS/control tests by default. No production HA writes, registry/database edits or automatic restart/deployment. Never commit credentials, real identities/locations/trails or local capture paths. A synthetic screenshot needs source/version and a demo label; private evidence remains outside this repository.

## Collaboration

Parallel work requires explicit authorization. Use isolated branches/worktrees with a fixed base, contract, owner and file scope. One integrator owns shared interfaces, manifest/version, translation aggregation, primary docs navigation and releases. Do not switch branches in another worker's checkout or concurrently mutate shared environments.

Handoffs state base/head, scope, changed contracts, checks/skips and unresolved evidence. Keep them concise; do not create a permanent manual for every task/agent. Preserve user changes, branches, meaningful IDs and Recorder continuity. No force-push/destructive reset.

## Synthetic profiling

`python -m scripts.profile_history --output /tmp/ninebot-profile.json` uses disposable deterministic 5k/50k/200k summaries and 512/4096/20000-point details. It does not read HA configuration, real accounts or existing archives. Same-runner comparisons use the identical profiler; reopening includes validation and is not OS-cold I/O. Never present small-sample results as a hardware SLA or change storage format before measuring.

## Publication

Keep stages reviewable. Documentation-only updates do not bump versions or create empty prereleases. Actual behavior/packaging releases need relevant authorized checks, a real GitHub Release and truthful notes. Publishing, deploying production HA and applying for HACS default inclusion are separate actions; do not infer permission from a local docs task.

简体中文摘要：按任务读取指南/模块/fixture，小改相关检查、重大边界一次矩阵。默认离线、保护身份与私有数据；明确授权才并行，独立 worktree、单一整合者，文档-only 不发空版本。
