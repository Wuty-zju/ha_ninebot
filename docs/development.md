# Development and validation

[Documentation](README.md) · [Architecture](architecture.md) · [Contributing](../CONTRIBUTING.md)

## Start with a task, not the whole repository

Start with [AGENTS](../AGENTS.md), inspect the checkout, then read the relevant guide and source boundary below. Current behavior is documented by topic; [CHANGELOG](../CHANGELOG.md) records its evolution.

| Task | Source boundary | Offline evidence |
|---|---|---|
| Account setup and authentication | `config_flow`, `account`, `session`, `runtime`, `client` | Config-flow, reauth, session, account-display and options tests |
| Current data and entities | `adapters`, `models`, `entity`, platform modules, `registry`, `migration` | Field fixtures, normalization, identity, entity and translation tests |
| Polling and recovery | `polling_policy`, `polling_activity`, `coordinator`, `realtime`, `demand`, `query_broker`, `backend` | Demand, broker, partial discovery, availability and cancellation tests |
| Controls and events | `lock`, `control_polling`, `button`, `event`, `ride_reports`, `vehicle_events` | Confirmation, replacement command, latch observation and baseline tests |
| History acquisition | `bootstrap`, `history_plan`, `preservation`, `incremental_runs`, `history_store` | Pagination, coverage, retry, budget and restart tests |
| Day/month statistics | `daily_sync`, `travel_statistics`, `period_statistics`, `ride_presentation` | Daily-index repair, period completeness and rollover tests |
| Archive and transfer | `ride_archive`, `account_archive`, `archive_transfer`, `archive_evidence`, `archive_options` | Schema, ownership, merge, retention, upload/download and backup tests |
| Integrity and repair | `object_checks`, `archive_audit`, `local_reconstruction`, `startup_recovery` | Corruption, raw replay, reference and recovery tests |
| Local history and maps | `recorded_actions`, `history_websocket`, `archive_browse`, `frontend/` | Service/authorization tests, Node tests and synthetic browser fixtures |
| Documentation and releases | `docs/`, `scripts/`, `.github/workflows/` | Link/diagram checks, release-policy and package tests |

Use `rg --files` to locate test families and [fixture metadata](../tests/fixtures/ninecli/0.1.7/metadata.json) to establish provenance. Each source file stays responsible for its own layer; shared contracts belong in the relevant model or protocol.

## Documentation ownership

| Question | Maintained source |
|---|---|
| What should a user do now? | Setup, history and migration guides, with paired English/Chinese pages. |
| How do layers and contracts fit together? | Architecture and the task-to-source/test map above. |
| Why did the implementation evolve? | [2.0 design timeline](releases/2.0.md); individual changes remain in CHANGELOG/releases. |
| What comes next? | Roadmap, scoped issue/PR and a concise handoff. |

Update the owning topic instead of adding overlapping reports. A release overview summarizes a version cycle; it does not replace current configuration or parser contracts. Agent handoffs link evidence and exact commits rather than repeating manuals.

## Repository layout

```text
custom_components/ninebot/   Installable Python, translations and bundled assets
  frontend/                 Experimental history card and vendored map library
  brand/                    Integration artwork
  licenses/                 Runtime attribution and source pins
tests/                      Offline HA, Node and synthetic browser tests
  fixtures/                 Sanitized recorded responses and synthetic inputs
scripts/                    Documentation, profiling and release utilities
.github/workflows/          Automatic scoped checks and manual release jobs
docs/                       User guides, architecture and development contracts
```

## Select the smallest meaningful checks

| Change | Validation |
|---|---|
| Documentation | `python scripts/check_docs.py`, documentation unit tests and generated-diagram check |
| Python behavior | Ruff/format, typing and affected offline pytest cases |
| Frontend | Related Node tests; synthetic browser render for interaction/layout changes |
| Identity, authentication or storage | Migration, authorization, cancellation and failure cases; both HA boundaries at major milestones |
| Release candidate | Lightweight candidate checks, one full HA boundary run and final package verification |

**Checks** runs Core lint and Release policy on PRs and `main`/`dev` pushes. Documentation, HACS and Hassfest have separate jobs. **Runtime checks** builds one deduplicated test plan for every PR from `.github/ci.json`, comparing the topic with its shared ancestor so later base changes do not inflate the selection. Known account/history/statistics/live scopes are combined; a new unmapped runtime file falls back to the offline suite. Critical account and compatibility changes select both HA boundaries. Frontend changes keep their separate Node/browser job. The plan and test reports are retained as Actions artifacts.

When the selected plan already contains every offline test file, Runtime checks collects the same 91% coverage gate and reports in that run. A successful complete two-boundary candidate result can be reused; run Node tests once on the frozen frontend inputs.

For an RC or major compatibility change without that complete result, dispatch **Checks** with `full_validation=true` on the fixed candidate. The current full matrix pins:

| Home Assistant | Python | Test plugin |
|---|---|---|
| 2026.1.0 | 3.13 | 0.13.305 |
| 2026.10.2 | 3.14 | 0.13.373 |

The job verifies installed versions and dependency consistency, runs mypy and the full offline pytest suite, and uploads JUnit/coverage reports. Node tests run in the same dispatch. The combined line/branch threshold is 91%, based on the full RC audit; 95% remains the improvement target. Documentation-only topics run the lightweight jobs. Same-repository PRs may reuse a successful full result only when runtime, tests and relevant execution inputs are unchanged; record the source SHA/run in the PR instead of launching another matrix. Both focused and complete HA jobs use the exact boundary/plugin pairs in `.github/ci.json`; update a boundary and its plugin together. Dependency consistency is checked after installation. Reuse unchanged successful evidence between candidates; repeat only affected checks after a fix.

In an existing compatible environment:

```sh
ruff check custom_components tests scripts
ruff format --check custom_components tests scripts
mypy custom_components/ninebot --follow-imports=silent
python -m pytest -q tests/test_config_flow.py
node --test tests/frontend/*.mjs
```

Select the test files for the change. Cloud CI provides isolated heavy environments; local documentation work needs only Python's standard library. A local test plan needs no HA installation:

```sh
python scripts/ci.py --plan --base FULL_BASE_SHA --head FULL_HEAD_SHA --output ci-scope.json
```

Update the owning scope in `.github/ci.json` when adding a source/test boundary. CI selection has standard-library regression tests for new files, shared fixtures, overlapping scopes and renames. Documentation validation runs for every candidate so the release gate can always find its result. Workflow-only changes run a small pipeline canary; they do not automatically render the map or repeat archive profiling.


## Fixtures and privacy

Record fixture source/version and distinguish recorded, synthetic and maintainer-confirmed values. Field semantics are indexed in [source-map.json](../tests/fixtures/field_inventory/source-map.json). Units, aliases and coordinate transformations need evidence before entering a production parser.

Keep credentials, account/vehicle identifiers and precise routes out of Git. Use sanitized fixtures and mock servers for development. Live cloud, SMS, controls and production changes require specific authorization. Share reviewed diagnostics through Issues and use [SECURITY](../SECURITY.md) for sensitive reports.

## Collaboration

Follow [CONTRIBUTING](../CONTRIBUTING.md#parallel-coding-agents). Give each authorized worker a fixed base, scoped files and a separate branch/worktree. One integrator owns shared models, versions, translations and publication. A handoff records the commit, changed contracts, check results and remaining work.

## Synthetic profiling

`python -m scripts.profile_history --output /tmp/ninebot-profile.json` measures disposable 5k/50k/200k summaries and 512/4096/20000-point details. Compare changes on the same runner and fixture. Startup includes validation; report the workload with each measurement. Database indexes, compression or journaling changes should follow those measurements.

## Publication

Versions use `2.0.0a1`, `2.0.0b1`, `2.0.0rc1` and `2.0.0`; tags and release titles add `v`. Every published version has a complete `ninebot.zip` and SHA256 checksum. Release notes use English with an equivalent collapsed Simplified Chinese section.

A stable release summarizes the full version cycle and compares against the previous final version. An RC marked as a regular GitHub Release or Latest retains its RC version identity and is excluded from that stable baseline. Preview and candidate notes compare against the preceding published version. Link installation and usage details to the matching README language; keep the development timeline in [the version history](releases/2.0.md).

### Packaged publication workflow

Run **Publish packaged release** from `main`, providing channel (`preview`, `candidate` or `stable`), version, source branch, full candidate SHA and bilingual notes. RC and stable publication also requires the ID of a successful full **Checks** dispatch. `publish=false` prepares artifacts for inspection; `publish=true` creates a draft, verifies its assets, then publishes it. The candidate must pass Core lint, Release policy, Documentation, HACS and Hassfest. The maintained workflow reads candidate Git objects and checks the source tip again before publication.

The publisher verifies the selected run belongs to this repository, ran the maintained Checks workflow, completed both pinned HA suites and the frontend tests, and used identical runtime/test/CI/dependency Git inputs. Its run ID, source SHA and input digest are included in the publication report. The digest covers runtime, HA/frontend tests, installation/selection code, the four execution workflows (Checks, Runtime, history browser and history profile), `.github/ci.json` and dependency declarations. Only the manifest version is excluded from that behavior digest; all other manifest values remain inputs. Version/package/HACS checks still run on the candidate. Issue templates and publication/documentation-only workflows keep their own lightweight checks. Publication/documentation tools keep their own current-SHA lint and policy tests; a change confined to those tools can reuse unchanged HA/frontend evidence. Selected Runtime regressions cannot stand in for this evidence.

Same-repository topic PRs and normal `dev` → `main` / `main` → `dev` promotions can reuse a successful full dispatch with matching Git inputs. Fork PRs always use the scoped plan. Before opening a release topic PR, freeze and validate its head once; a later change to behavior inputs requires fresh evidence. If no matching evidence is available, the existing regression plan runs. Lint, documentation, HACS, Hassfest and source-tip gates still run for the promoted SHA. History browser/profile workflows also support explicit manual dispatch for changes to their own commands or dependencies.

The archive contains the contents of `custom_components/ninebot` at its root. Verify the downloaded ZIP/checksum against the tagged tree. GitHub downloads count this asset, including updates and repeat downloads. Published tags and package assets remain fixed. Editing a release description does not require a new version: preserve its opening format, title/tag, distribution flags and assets, and keep the substantive release overview in [the version history](releases/2.0.md).

## Branch and release channels

- `dev`: reviewed development and preview publication.
- `main`: accepted-cycle promotion, recommended release candidates and stable fixes.
- Short `feat/`, `fix/`, `docs/`, `chore/` or `perf/` branches: one bounded task, PR into `dev`.
- Temporary `release/<version>`: only when a frozen candidate must coexist with next-cycle development.

Promote long-lived branches with normal merges, then merge `main` back into `dev`. Retire integrated topics after recording their PR/recovery reference. Use a minimal backport for stable fixes that must exclude later development.

Run the read-only preflight after fetching branches/tags:

```sh
python scripts/check_release.py --channel preview --version 2.0.0rcN \
  --source dev --sha REPLACE_WITH_REVIEWED_FULL_SHA
```

Preview publication accepts `dev` or the matching release branch and sets `prerelease=true`. Stable publication uses an unsuffixed version from `main`. The explicit `candidate` channel accepts only an `rc` version from `main` and publishes it as a regular Release with **Latest**, as selected for v2.0.0rc1 and later explicitly promoted for v2.0.0rc2. The RC suffix identifies the candidate stage; the GitHub flag controls distribution. See [GitHub release API](https://docs.github.com/en/rest/releases/releases#create-a-release).

Code rollback and persisted-schema recovery are separate operations. Follow the [migration guide](migration.md#archive-schema-and-rollback) with a matching backup.

## Documentation assets and preview

```sh
python -m unittest discover -s scripts/tests -p 'test_docs.py'
python scripts/check_docs.py
python scripts/render_architecture.py --check
```

Documentation CI validates local links/anchors, paired language outlines, NOTICE references, asset hashes and generated SVGs. Review English/Chinese meaning and rendering alongside these checks.

The sixteen architecture SVGs (four diagrams × two languages × two themes) are generated by `scripts/render_architecture.py`: data flow, acquisition, controls and account archive lifecycle, in English and Chinese. Edit the source, regenerate and inspect the affected images in both palettes. GitHub `<picture>` sources select the dark variant; the light image is the portable fallback. CI checks every source path, theme parity and text contrast. See [GitHub theme-aware images](https://github.blog/changelog/2022-08-15-specify-theme-context-for-images-in-markdown-ga/).

`python scripts/docs_preview.py` provides a loopback synthetic frontend preview, including `/branding`. The [asset manifest](assets/manifest.json) records the existing synthetic captures and their source digests. Keep product screenshots separate from architecture diagrams; README currently uses diagrams only.

## HACS readiness and branding

The repository distributes one integration under `custom_components/ninebot`, with runtime assets inside that directory. `manifest.json` supplies integration metadata; `hacs.json` selects the packaged ZIP and declared minimum HA. Installation currently uses **HACS Custom repository**.

Before applying for default inclusion, review the [HACS integration requirements](https://www.hacs.xyz/docs/publish/integration/) and [submission process](https://www.hacs.xyz/docs/publish/include/), then check:

- Final released manifest, package layout, public guides and issue tracker.
- Successful HACS and Hassfest validation on the release candidate.
- MIT license, [NOTICE](../NOTICE), runtime attribution and artwork permission.
- Brand variants and readability on light/dark backgrounds.

The bundled `brand/icon.png` is a 256×256 transparent PNG. Its source/rights and a dark variant remain maintainer follow-ups. HA supports local custom-integration branding from 2026.3; older supported HA may use an upstream image or placeholder. HACS download/icon presentation also depends on the installed HACS version.

简体中文：RC／正式发版须提供完整Checks运行ID，发布器核对仓库、工作流、两HA和Node步骤及输入摘要；主线提升只复用相同输入的成功结果，轻量同SHA门禁照常。按任务定位源码与测试，普通改动做相关检查，RC 在固定候选上完成一次最低／当前 HA 综合验证。英文与中文同步维护默认值、单位和迁移说明。短分支回 dev，验收后正常合 main 并回灌；预发布保留对应标志。历史库与身份迁移按专门指南操作，图表、资源和运行文件各有明确目录。
