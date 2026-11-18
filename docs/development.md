# Development and validation

[Documentation](README.md) · [Architecture](architecture.md) · [Contributing](../CONTRIBUTING.md)

## Start with a task, not the whole repository

Start with [AGENTS](../AGENTS.md), inspect the checkout, then read the relevant guide and source boundary below. Current behavior is documented by topic; [CHANGELOG](../CHANGELOG.md) records its evolution.

| Task | Source boundary | Offline evidence |
|---|---|---|
| Account setup and authentication | `config_flow`, `account`, `session`, `runtime`, `client` | Config-flow, reauth, session, account-display and options tests |
| Current data and entities | `adapters`, `models`, `entity`, platform modules, `registry`, `migration` | Field fixtures, normalization, identity, entity and translation tests |
| Polling and recovery | `polling_policy`, `polling_activity`, `coordinator`, `realtime`, `demand`, `query_broker`, `backend` | Demand, broker, partial discovery, availability and cancellation tests |
| Controls and events | `lock`, `control_polling`, `native_control`, `button`, `event`, `ride_reports`, `vehicle_events` | Confirmation, native business routing, replacement command, latch observation and baseline tests |
| History acquisition | `bootstrap`, `history_plan`, `preservation`, `incremental_runs`, `history_store` | Pagination, coverage, retry, budget and restart tests |
| Day/month statistics | `daily_sync`, `travel_statistics`, `period_statistics`, `ride_presentation` | Daily-index repair, period completeness and rollover tests |
| Archive and transfer | `ride_archive`, `account_archive`, `archive_transfer`, `archive_evidence`, `archive_options` | Schema, ownership, merge, retention, upload/download and backup tests |
| Integrity and repair | `object_checks`, `archive_audit`, `local_reconstruction`, `startup_recovery` | Corruption, raw replay, reference and recovery tests |
| Local history and maps | `recorded_actions`, `history_websocket`, `archive_browse`, `frontend/` | Service/authorization tests, Node tests and synthetic browser fixtures |
| Optional Assistant API | `assistant`, `assistant_api`, `assistant_ask`, `assistant_policy`, `assistant_queries`, `assistant_views`, `assistant_limits` | Account consent, active-user/ownership guards, native AI Task evidence queries, local Action queries, safe projections, read admission/cancellation and installed native API/MCP canaries |
| Documentation and releases | `docs/`, `scripts/`, `.github/workflows/` | Link/diagram checks, release-policy and package tests |

Use `rg --files` to locate test families and [fixture metadata](../tests/fixtures/ninecli/0.1.7/metadata.json) to establish provenance. Each source file stays responsible for its own layer; shared contracts belong in the relevant model or protocol.

Support evidence stays local and bounded. `diagnostics` projects current/previous cached months and history progress without acquisition or database scans; `history_progress` owns the percentage semantics. Query failures retain reviewed `FailureContext` metadata, and `control_results` emits one DEBUG outcome per explicit invocation. Preserve the original dispatch, confirmation, cancellation and ownership behavior when extending these records. See [data and control troubleshooting](guide.md#missing-data-and-controls).

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

The job verifies installed versions and dependency consistency, runs mypy and the full offline pytest suite, and uploads JUnit/coverage reports. Node tests run in the same dispatch. Both complete CI entry points use `python scripts/ci.py --run-full` or the equivalent full selection. The gate compares the covered and total statement-plus-branch counts from Coverage XML against 91% before rounding. The terminal report shows two decimal places. The complete run includes the bundled public-help tests. Documentation-only topics run the lightweight jobs. Same-repository PRs may reuse a successful full result only when runtime, tests and relevant execution inputs are unchanged; record the source SHA/run in the PR instead of launching another matrix. Both focused and complete HA jobs use the exact boundary/plugin pairs in `.github/ci.json`; update a boundary and its plugin together. Dependency consistency is checked after installation. Reuse unchanged successful evidence between candidates; repeat only affected checks after a fix.

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

Start with the failing boundary. The [user guide](assistant.md) owns the tool contract, account options and response examples. The Assistant uses Home Assistant's native API registry and the existing account archive. The model is configured in the user's host.

| Boundary | Implementation | Contract to protect |
|---|---|---|
| Registration and reads | `assistant`, `assistant_api`, `assistant_policy`, `assistant_limits` | Preserve the authenticated HA Context. Check the active administrator, account, vehicle grant and runtime owner at admission and before returning a result. |
| Local measurements | `assistant_queries`, `assistant_views`, `assistant_evidence` | Reuse deterministic business queries. Preserve units, business dates, observation times, partial coverage and sources. Precise coordinates and raw vehicle responses stay private. |
| Optional AI Task Action | `assistant_ask`, `assistant_plans` | Bind the selected vehicle to the task. Keep an independent copy of accepted tool evidence. Recheck access before returning partial results after a provider error. |
| Localized public help | `assistant_knowledge`, bundled corpus | Return applicable sources and the corpus version within the existing response budget. The display-ready Action answer is derived from the checked results. |
| Human-reviewed acquisition | `assistant_tasks`, `assistant_task_store`, `assistant_execution` | Preview work locally, persist the scoped request, and require the original requester to approve it. Reuse the existing history worker and account budget. |

Read-only tools run without cloud refresh. Recorded-list cursors bind the account, vehicle, date range and archive revision. Every page is authorized independently. Multi-vehicle queries need an explicit selection. A model host can phrase a reply differently from its inputs. Keep evidence fidelity and free-form answer quality as separate review decisions.

### Local task preview

The account archive examines up to 31 business days and selects at most 20 missing details. The preview counts saved rows, missing mappings and uncertain identities separately. Each selected ride keeps its verified source month and a fingerprint. The executor rechecks these facts before claiming work. Empty, ambiguous and already-saved selections consume no cloud budget.

### Task confirmation contract

1. Freeze the requester, account, vehicle, dates, selected identities and attempt limit in the plan. A tool can submit the proposal for review.
2. The original administrator opens the native review panel. A short-lived, one-use confirmation binds the reviewed plan and parameters. Cancellation removes only this task.
3. The archive actor records actual claims and budget consumption together. A resumed driver rechecks the requester, grant, vehicle and saved scope. Completion verifies the selected saved details.
4. Changing consent invalidates the old grant immediately. Progress and review reads check the same grant again before delivering data. A late or cancelled worker cannot revive an obsolete grant or close a replacement runtime.
5. Portable account archives contain ride data. HA backup hooks exclude live approval material from a restored task. Retain the distinction between a plan, an approval, a request attempt and a verified saved result.

See [native review](#native-task-review-panel) and [task notices](#task-notices) for frontend and notification ownership.

### Versioned public help

The shipped catalog contains 19 bilingual topics and 10 advisory checklists. Each entry carries an applicability interval, a pinned source commit and anchor, and a content digest. When changing the manifest, exercise common searches against **that manifest version**, including the upper boundary and a query with no applicable match. Expand a version interval only after verifying the referenced guide still describes the behavior.

```sh
python -m scripts.check_assistant_knowledge --verify-sources
python -m unittest scripts.tests.test_assistant_knowledge
```

The source check reads Git objects and requires the referenced commits locally. `search_help` returns at most three matches within 4 KiB. Match citations precede excerpts. General help and checklists share the same bound. Checklists guide tool use and stopping conditions. Authorization resides in the program.

<a id="small-model-baseline"></a>
<a id="synthetic-model-cases"></a>

<a id="reference-host-evaluation"></a>
<a id="historical-cpu-and-local-gpu-baselines"></a>

### Assistant validation

| Change | Smallest useful check |
|---|---|
| Corpus or citations | Check immutable source links and both languages. Exercise the actual manifest and the display-ready evidence response. |
| Native API, identity or disclosure | Use the native API, MCP and Conversation checks on Core 2026.11.0. Include a revoked scope and an explicit API URL. |
| Task review or persistence | Exercise proposal, independent approval, cancellation, restart and saved-result verification. The milestone workflow includes a synthetic browser, native HA WebSocket and SQLite. |
| Model-host behavior | Select the affected cases from the [synthetic evaluation set](../tests/fixtures/assistant/evaluation-tasks.json). Review the answer alongside the actual tool receipts. |

`scripts/check_assistant_api.py`, `scripts/check_assistant_mcp.py`, `scripts/check_assistant_conversation.py` and `scripts/check_assistant_ask.py` exercise the native Core and Action boundaries. The global MCP endpoint and per-API URLs have different selection semantics. The [MCP settings contract](assistant.md#check-mcp-api-selection) covers both. Confirm that client-supplied device metadata cannot broaden the selected scope.

Model evaluation is a separate, optional workflow dispatch. Select `assistant_evaluation=true` and the affected profile in **Actions → Checks**. Compare direct prompts with a bounded tool loop on the same synthetic input. Record the exact host, model revision, tool calls, final answer, citation, elapsed time and any missing evidence. Distinguish a completed workflow from a correctly answered task. Development-only reference hosts, including local Ollama and CPU experiments, add no installation dependency. [Earlier host protocols and recorded evaluation methods](https://github.com/Wuty-zju/ha_ninebot/blob/502aedbb4309fcac3d77ee8ceb61d77549c8ed7c/docs/development.md#assistant-validation)

## Fixtures and privacy

The curated field disposition matrix selects its three inventory contract checks. Recorded payloads, source maps and unknown shared fixture inputs retain the conservative complete-suite selection.

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

The archive contains the contents of `custom_components/ninebot` at its root. Verify the downloaded ZIP/checksum against the tagged tree. GitHub downloads count this asset, including updates and repeat downloads. Published tags and package assets remain fixed. Editing a release description does not require a new version: preserve its opening format, title/tag, distribution flags and assets, and use the [changelog](../CHANGELOG.md) for the current release and the [2.0 development history](releases/2.0.md) for the original refactor.

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

A maintainer may explicitly reclassify a historical GitHub release. Snapshot its tag, body and asset digests, update only its distribution flag, then verify those identifiers and the current Latest release. Publishing a new build always uses a new tag.

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


### Native task review panel

The task workflow adds a separate, off-by-default task option beneath Assistant consent. `assistant_panel` lazily registers a packaged administrator-only HA custom panel at `/ninebot-assistant`; it uses the existing static route and native HA WebSocket connection. Headless installations keep their API without loading a frontend. Panel registration is serialized with the API lifecycle and removed when the last task-enabled account opts out. A path collision is reported without overwriting another panel or breaking vehicle features.

`ninebot/assistant/vehicles` is a narrow local discovery command which reuses the task query layer's original Context, consent, safe projection and admission limits. The review panel lists only that user's tasks for the selected authorized vehicle. Preparation displays the server's exact scope before a separate approval click; the nonce remains in memory and the redacted `token` message, never in DOM, URL or browser storage. Leaving the view or changing identity/vehicle clears it. Approval and cancellation are never automatically retried.

The page refreshes only the selected vehicle's local task metadata: ten seconds for active work, thirty seconds for pending review, and no periodic refresh for a finished or empty list. Hidden/disconnected pages stop polling; at most one page request is in flight. Refreshing the page does not query Ninebot Cloud. Both languages use HA theme variables and safe text nodes. Node state tests and the isolated browser proof cover the approval sequence, cancellation, stale responses, revoked access and light/dark mobile layouts with synthetic data. Task support is available from 2.1.2 and remains optional.


### Task notices

`assistant_notifications` publishes only generic, fixed English/Chinese messages through HA's native persistent-notification API. A hashed entry key coalesces notices without exposing account or task identities. The Store computes meaningful phase changes only after the atomic write and envelope acknowledgement. New applications and terminal outcomes notify; nonce rotation, checkpoints within a phase and waiting/retry transitions do not. Remaining pending reviews take precedence over another task's result.

The owned Store checks current policy, runtime owner and lifecycle blocks before and after the lazy helper import. Revocation/removal dismisses its notice even when authority-file removal fails. Notification failures do not invalidate a saved task. Ordinary reloads retain task metadata; a notification is a UI convenience, not a durable queue or an authorization record. The task API prompt sends the original requester to the HA panel using the same administrator identity, without returning a nonce or adding an approval tool.

The Assistant milestone acceptance workflow connects the packaged review panel to an ephemeral HA WebSocket server and the real account archive. It approves one synthetic scope, runs the existing scheduler with a fake upstream, and reads the saved result back in the browser. Run it at task-flow milestones; ordinary runtime checks skip this browser-only case. The workflow retains a screenshot and a small result summary without authentication payloads.
