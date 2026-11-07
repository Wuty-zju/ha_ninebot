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
| Optional Assistant API | `assistant`, `assistant_api`, `assistant_policy`, `assistant_queries`, `assistant_views`, `assistant_limits` | Account consent, active-user/ownership guards, local Action queries, safe projections, read admission/cancellation and the installed native API/MCP HTTP canaries |
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

**Checks** runs Core lint and Release policy on PRs and `main`/`dev` pushes. Documentation, HACS and Hassfest have separate jobs. **Runtime checks** builds one deduplicated test plan for every PR from `.github/ci.json`, comparing the topic with its shared ancestor so later base changes do not inflate the selection. Known account/history/statistics/live scopes are combined; a new unmapped runtime file falls back to the offline suite. Critical account and compatibility changes select both HA boundaries. Frontend changes keep their separate Node/browser job. The plan and test reports are retained as Actions artifacts. Scope and HA report names include the run attempt so rerunning a failed job preserves earlier evidence without upload-name conflicts. HA jobs allow 25 minutes for installation, typing, tests and report upload; selected tests and coverage requirements are unchanged. Release evidence uses verified job/step results, independently of report artifact names.

When the selected plan already contains every offline test file, Runtime checks collects the same 91% coverage gate and reports in that run. A successful complete two-boundary candidate result can be reused; run Node tests once on the frozen frontend inputs.

For an RC or major compatibility change without that complete result, dispatch **Checks** with `full_validation=true` on the fixed candidate. The current full matrix pins:

| Home Assistant | Python | Test plugin |
|---|---|---|
| 2026.1.0 | 3.13 | 0.13.305 |
| 2026.10.4 | 3.14 | 0.13.375 |

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


## Assistant development

The archive's internal `DetailScope` restricts claim identities before the candidate limit and checks frozen fingerprints before changing state. Optional `DetailBudget` counters bind a random task ID to that scope, vehicle and fixed attempt limit. Initialization is idempotent; a missing counter cannot be recreated by a claim. Task consumption, ride attempts and account pacing commit in the same actor transaction. These primitives confer no permission and are not exposed as AI execution tools; human approval and task lifecycle remain separate work.

The optional [Assistant preview](assistant.md) uses the existing native API registry. `assistant_api` adapts protocol objects; `assistant_policy` checks active administrators and separate account/vehicle consent; `assistant_queries` calls existing local Actions; `assistant_views` constructs a small reviewed output; `assistant_limits` bounds read admission. Disabled accounts do not load the native adapter or create additional polling.

The native API canary runs on the published Core 2026.11.0 without a mismatched test plugin. `scripts/check_assistant_mcp.py` uses the real native HTTP view and MCP dispatcher with synthetic authorization, registry and snapshots. It checks user Context, read-only filtering, consent revocation and API removal. OAuth and model-provider behavior need their own evidence.

[Synthetic evaluation tasks](../tests/fixtures/assistant/evaluation-tasks.json) define 52 scenarios for staged host/model evaluation. Their values are invented, all measurements start as null, and the task count is not a pass rate. Use the companion fixture and actual tool results; do not turn permission-injection test content into instructions. Record model, host, source revision, tool trace summary and measured results separately. Tests against HA Actions and SQLite establish tool contracts, not model accuracy.

### Versioned public help

`assistant_knowledge.py` lazily reads the bundled JSON catalog in an executor on an authorized help query. Eleven bilingual topics carry immutable first-party source commits/hashes, content hashes, evidence status and a numeric version range. No private history or external source is indexed. Lexical English/code tokens and Han bigrams rank at most three chunks in a 4 KiB body. Unsupported versions do not borrow newer knowledge. Runtime corruption fails only help.

Run `python -m unittest scripts.tests.test_assistant_knowledge -q` without HA. Before changing source citations, use `python -m scripts.check_assistant_knowledge --verify-sources` with the cited Git objects present; this verifies source blobs and anchors offline. Catalog hash validation detects changed content, not whether a claim is true: review every summary against its cited contract. Corpus versions and supersedes entries track intentional replacements.

The same catalog owns eight bounded read procedures (SK01–SK08) with bilingual triggers, inputs, steps, stop/verification rules, permitted tool keys and call budgets. They have independent content hashes and fixed public sources. A topic references one procedure; retrieval includes it once within the same 4 KiB limit. The validator rejects execution tools, invalid budgets and inconsistent applicability. They are task guidance for an existing host, not a runtime planner or a new authorization source. Synchronization procedures SK09/SK10 belong to the future approved-task API and are not exposed here.

### Small model baseline

The optional **Checks → assistant_evaluation** dispatch runs Q03, Q04, P06 and S02 through a fixed CPU model and the installed native MCP HTTP handler. It compares a single prompt without tools against a bounded tool loop. Authorization, registry, snapshots and Action returns are synthetic; this run does not connect to a production HA instance or exercise a real archive. Q03 supplies two vehicles; P06 supplies a selected ride and revokes consent inside its detail read.

Select `assistant_profile=retrieval` for the separate R02/R04 comparison: the same bounded Agent runs with public help unavailable and available, while all other read tools and task assertions remain unchanged. The host rejects calls to tools absent from that arm before sending an RPC. Reports retain the exposed tool names and actual help results, including no-match responses. This profile measures help use and answer support; it does not repeat or replace the baseline cases. Fixture preflight is excluded from both conversations and supplies no help text to the model.

The current CPU candidate is Qwen3-4B-Instruct-2507 in a Q4_K_M GGUF conversion. `scripts/assistant_model.py` pins the [Unsloth conversion](https://huggingface.co/unsloth/Qwen3-4B-Instruct-2507-GGUF/commit/a06e946bb6b655725eafa393f4a9745d460374c9), its SHA256, the [official tokenizer](https://huggingface.co/Qwen/Qwen3-4B-Instruct-2507/commit/cdbee75f17c01a7cc42f958dc650907174af0554) and the [llama.cpp b12182 engine](https://github.com/ggml-org/llama.cpp/releases/tag/b12182) with its release-asset digest. The model is Apache-2.0 and the engine MIT. Both are temporary evaluation inputs, outside the integration package.

Earlier 0.6B/1.7B cohorts did not meet clarification, observation-time or factual requirements. Their original results stay attached to their source and host revisions. The new host uses the same native MCP contract and task assertions, with a subprocess deadline covering model loading and prefill. Each arm has at most three model turns, four tool calls and 256 generated tokens per turn. It starts no inference listener, uses no provider credentials and installs no PyTorch dependency. Temporary engine/model files are removed after the job.

The `assistant-model-<attempt>` artifact contains source/dependency versions, visible replies, real tool requests/results, token counts and timing. For the quantized host, token counts use the official tokenizer on the rendered prompt and visible output; engine prompt/decode counters are reported separately when emitted. They are not provider billing usage. The engine's exact terminal marker is removed before output accounting and its presence recorded. Historical artifacts retain their original accounting. Reasoning blocks are omitted. Task completion and fact accuracy remain unset until the answers and evidence are reviewed; a successful job means the comparison ran. Small-model results describe these samples, not the quality of every compatible AI host. Protocol and artifact-boundary unit tests need only the standard library.

Model tool messages contain the returned [MCP text content](https://modelcontextprotocol.io/specification/2025-11-25/server/tools#tool-results), following the [Transformers tool-message contract](https://huggingface.co/docs/transformers/v4.57.1/en/chat_extras). Call IDs associate replies with requests; the full JSON-RPC envelope stays in the recorded trace. Unexpected or inconsistent responses stop the case. Reports identify this host contract separately from the model revision; older envelope-based runs remain exploratory evidence.

Controlled comparisons use the fixture's fixed UTC clock for snapshots, query periods and view context; latency still uses the real monotonic clock. `tool_calls` counts model-selected reads; host-context calls and conversation HTTP requests have separate counters. Fixture preflight and cleanup are outside those measurements.

```sh
gh workflow run checks.yaml --ref YOUR_BRANCH -f assistant_evaluation=true
```

## Fixtures and privacy

Record fixture source/version and distinguish recorded, synthetic and maintainer-confirmed values. Field semantics are indexed in [source-map.json](../tests/fixtures/field_inventory/source-map.json). Units, aliases and coordinate transformations need evidence before entering a production parser.

Keep credentials, account/vehicle identifiers and precise routes out of Git. Use sanitized fixtures and mock servers for development. Live cloud, SMS, controls and production changes require specific authorization. Share reviewed diagnostics through Issues and use [SECURITY](../SECURITY.md) for sensitive reports.

## Collaboration

Follow [CONTRIBUTING](../CONTRIBUTING.md#parallel-coding-agents). Give each authorized worker a fixed base, scoped files and a separate branch/worktree. One integrator owns shared models, versions, translations and publication. A handoff records the commit, changed contracts, check results and remaining work.

## Synthetic profiling

`python -m scripts.profile_history --output /tmp/ninebot-profile.json` measures disposable 5k/50k/200k summaries and 512/4096/20000-point details. Compare changes on the same runner and fixture. Startup includes validation; report the workload with each measurement. Database indexes, compression or journaling changes should follow those measurements.

## Publication

New maintenance previews use numeric versions such as `2.0.4` with the GitHub **Pre-release** flag. Tags and release titles add `v`. Existing `a`, `b` and `rc` releases remain available. Each new release includes a complete `ninebot.zip` and SHA256 checksum. Release notes use English with an equivalent collapsed Simplified Chinese section.

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
python scripts/check_release.py --channel preview --version 2.0.4 \
  --source dev --sha REPLACE_WITH_REVIEWED_FULL_SHA
```

Preview publication accepts `dev` or the matching release branch and sets `prerelease=true`. A numeric version can be a preview: the GitHub flag determines distribution, independently of the version suffix. Once published, that version is reserved; a later stable release uses a higher unused version. Never reuse a preview tag or replace its package. Stable publication uses an unsuffixed version from `main`. The explicit `candidate` channel accepts only an `rc` version from `main` and publishes it as a regular Release with **Latest**, as selected for the 2.0 release candidates. The RC suffix identifies the candidate stage; the GitHub flag controls distribution. See [GitHub release API](https://docs.github.com/en/rest/releases/releases#create-a-release).

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


## Marketplace catalog preparation

Ninebot is currently installed as a custom repository. Before submitting it to the shared [Marketplace/HACS catalog](https://contributors.home-assistant.io/marketplace/guides/catalog/), record the reviewed release and successful HACS/Hassfest runs, confirm repository description/topics and issue handling, and verify these items:

- One integration at `custom_components/ninebot`, with all runtime resources in that directory.
- A matching manifest/tag/release, `ninebot.zip` and `SHA256SUMS`; a clean install from the selected package.
- Local `brand` assets, suitable in light and dark themes, and the `vehicles_transport.bikes_scooters` category.
- Accurate authentication, platform and verified service-region information in the guide; no inferred global server support.
- Existing naming, unique IDs and Recorder history preserved. Custom views use HA's display-name helper where available.

The catalog is maintained in `hacs/default`. Prepare a sorted integration-list change and the current submission checklist; inclusion is complete only after upstream review/merge and catalog processing. Catalog inclusion and Core inclusion are separate processes.

The generated HA test plugin currently pins an older Core release. `.github/ci.json` therefore separates its compatible full/scoped test boundaries from the exact published Core used by `scripts/check_ha_api.py`. The latter checks installed native API contracts without the plugin or network requests; it does not replace the runtime regression suites. Update the paired runtime boundary when a matching plugin is available. Do not bypass dependency constraints to claim a new-version pass.

简体中文：默认目录申请需附真实发布与官方校验记录，核对包、品牌、分类、区域说明和用户文档；审核合入后才可声明默认可搜。最新 Core 的独立 API 检查与配套测试插件的运行回归分开记录，保留最低版本兼容和实体身份。


## Package download audit

Run **Actions → Package download audit → Run workflow** on the maintained branch. The read-only workflow paginates the full release catalog (100 per page), excludes drafts and checksums, deduplicates asset IDs and counts only uploaded `ninebot.zip` assets. It includes previews, updates and repeat downloads. Its job summary and `package-download-counts` JSON artifact provide totals, per-release counts, asset IDs and the capture interval. Failed reads fail the job and do not publish a zero count.

The integration sends no installation telemetry. Marketplace/HACS may show its selected release's count, while the README badge is supplied by Shields and its cache. GitHub source archives, clones, mirrors and offline copies cannot be reconstructed as a single installation total. Keep published release assets fixed; the audit does not rewrite README or create a metadata branch.

The counter is a standard-library maintenance tool: `python -m scripts.download_counts --repository OWNER/REPOSITORY --output downloads.json`, using `GH_TOKEN` with read access. Keep the output outside the runtime directory. Unit checks use synthetic metadata and no network.

简体中文：下载审计通过现有 GitHub Actions 手动运行，完整分页统计规范 ZIP，排除草稿与校验文件；输出逐版本报告，不新增遥测、不改发布资产、不自动提交计数日志。原生商店所选版本计数与跨版本审计是不同口径。
