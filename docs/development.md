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

**Checks** automatically runs **Core lint** and the small standard-library **Release policy** guards on PRs and pushes to `main`/`dev`. HACS/Hassfest use unique check names. The complete HA/coverage/frontend matrix requires `workflow_dispatch` with **full_validation=true**; its default is false. **Focused statistics**, **History protocol** and **History browser** have affected-path scopes. **History profile** is an explicit synthetic measurement, not a timing SLA. Read current YAML before dispatching; a green lightweight badge does not prove the full matrix ran.

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

No live cloud/SMS/control tests by default. No production HA writes, registry/database edits or automatic restart/deployment. Never commit credentials, real identities/locations/trails or local capture paths. Synthetic test captures need source/version and an explicit development-fixture label; private evidence remains outside this repository. Early-alpha UI/card captures are retained as development evidence, not promoted in README demos.

## Collaboration

Parallel work requires explicit authorization. Use isolated branches/worktrees with a fixed base, contract, owner and file scope. One integrator owns shared interfaces, manifest/version, translation aggregation, primary docs navigation and releases. Do not switch branches in another worker's checkout or concurrently mutate shared environments.

Handoffs state base/head, scope, changed contracts, checks/skips and unresolved evidence. Keep them concise; do not create a permanent manual for every task/agent. Preserve user changes, branches, meaningful IDs and Recorder continuity. No force-push/destructive reset.

## Synthetic profiling

`python -m scripts.profile_history --output /tmp/ninebot-profile.json` uses disposable deterministic 5k/50k/200k summaries and 512/4096/20000-point details. It does not read HA configuration, real accounts or existing archives. Same-runner comparisons use the identical profiler; reopening includes validation and is not OS-cold I/O. Never present small-sample results as a hardware SLA or change storage format before measuring.

## Publication

Keep stages reviewable. Documentation-only updates do not bump versions or create empty prereleases. Actual behavior/packaging releases need relevant authorized checks, a real GitHub Release and truthful notes. Publishing, deploying production HA and applying for HACS default inclusion are separate actions; do not infer permission from a local docs task.

## Branch and release channels

See [CONTRIBUTING](../CONTRIBUTING.md#branch-targets-and-releases) for PR targets. `dev` is the reviewed integration/preview branch; `main` receives stable promotions and stable fixes. During the current transition, 2.0 is still prerelease. Historical tags/Releases remain intact. Short topic branches retire after integration; only main/dev are permanent by default. A temporary `release/<version>` isolates a frozen candidate only when development must advance concurrently.

Run **Release preflight** manually from **main** with channel, exact manifest version (without `v`), source branch and reviewed full commit SHA. It has read-only permissions, reads fetched Git objects without executing candidate code, and **never creates a tag or Release**. It requires the SHA to equal the fetched source tip, the committed Ninebot version to match and the version tag to be unused. Preview requires an `a`/`b`/`rc` suffix and source `dev` or matching `release/<version>`; stable requires an unsuffixed version on `main`.

The same offline check is available after fetching branches/tags:

```sh
python scripts/check_release.py --channel preview --version 2.0.0b95 \
  --source dev --sha REPLACE_WITH_REVIEWED_FULL_SHA
```

This is request validation, not runtime or packaging certification. Before actual publication, verify candidate checks, final manifest/tag/ZIP/checksum, fresh remote source/tag state and authorized release scope. Use a draft with all assets attached before publishing; previews are prerelease/non-latest. Never move/reuse a published tag or silently change its assets. Code rollback does not reverse persisted schema migrations: use the matching HA backup when required.

Record the actual tested SHA/tree, workflow/dependency/fixture/environment and results before reusing CI. Identical content can reuse related evidence, but changed content/configuration needs affected checks and every release still needs final channel/package verification. Full suites belong at meaningful compatibility/migration/RC boundaries. Production HA deployment and physical controls remain separate authorizations.

简体中文：main/dev职责与当前2.0预发布状态分开判断。发布预检只读，不执行候选代码/发版本；核对来源tip、manifest、未使用tag与通道。完整HA/前端矩阵需显式勾选full_validation；普通PR相关检查即可。稳定修复不能带入整条下一轮dev，迁移回滚须匹配备份。

## Documentation assets and preview

Use `python3 scripts/check_docs.py` and `python3 -m unittest discover -s scripts/tests -p 'test_docs.py'`. Both use only the standard library. The **Documentation** workflow runs these on public documentation/tool changes without HA/pip/browser dependencies. It checks local Markdown/HTML-image references and anchors, NOTICE targets, paired language outlines and the synthetic-image manifest; translation meaning, remote availability and screenshot privacy still require review. Ignored local override files are excluded.

`python3 scripts/docs_preview.py` starts a loopback-only preview of the existing `tests/frontend/map-fixture.html`, never a HA account. Use the printed URL with `lang=en` or `lang=zh-Hans`; optional `scale=0.8` changes only the documentation shell. Select the second synthetic Compare checkbox for the desktop example. CSP blocks external connections/images; keep tiles off. Stop the process after use. The ordinary browser smoke fixture remains unchanged.

The [asset manifest](assets/manifest.json) records runtime commit, fixture digest, image language/size/hash, synthetic provenance and attribution. Desktop images are viewport captures of the real bundled card; the Chinese mobile example is the earlier b94 full-page synthetic render. Do not replace them with real accounts or a fabricated future UI. Preserve upstream Leaflet attribution; an image's synthetic flag alone is not a privacy audit.

## HACS readiness and branding

HACS custom-repository installation, validation, default-list admission and HA Core quality certification are different states. This repository currently advertises **Custom repository**. Follow the current [general](https://www.hacs.xyz/docs/publish/start/), [integration](https://www.hacs.xyz/docs/publish/integration/) and [default inclusion](https://www.hacs.xyz/docs/publish/include/) requirements when preparing an application.

| Maintainer check | Repository state / next action |
|---|---|
| Single integration and runtime placement | Only `custom_components/ninebot`; runtime assets stay within it. Docs/preview/test utilities are development-only. |
| Manifest | Domain/name/version/docs/issues/codeowners present; service/cloud_polling classification and exact dependency pin retained. |
| HACS manifest | Name, integration layout, HA minimum and `country: CN` retained. English docs do not establish support outside CN. |
| Open-source license and notices | MIT recognized; dependency/source limitations, runtime recon and Leaflet attribution retained. Do not imply the project MIT license grants trademark/image rights. |
| Public documentation | EN/Chinese setup/history/migration and synchronization/data contracts, troubleshooting/upgrade/security and contribution paths. UI/cards are early alpha; screenshots are withdrawn from README. |
| Description/topics/issues | Already present. Suggested About text: `Ninebot vehicle integration for Home Assistant — battery, charging, ride history and maps.` Existing About has a spelling error; changing remote settings is a separate maintainer step. |
| Required validation | HACS and Hassfest must pass on the candidate without errors/ignores. A green old commit does not prove a new candidate. Documentation CI supplements, not replaces them. |
| Release | Publish a full GitHub Release after successful checks when a meaningful authorized update is ready; a tag alone is insufficient. Do not create an empty prerelease just to publish docs. |
| Default-list application | Not submitted by this docs change. Later prepare an alphabetically placed editable PR to `hacs/default`'s integration list as owner/major contributor. Recheck domain conflicts and the application template then. |

The current packaged `brand/icon.png` is 256×256 RGBA PNG, introduced in [479068c](https://github.com/Wuty-zju/ha_ninebot/commit/479068cc936efb0cda50ff9c845c3d60d33540c3). Repository history proves its inclusion, **not original artwork permission**. Maintainer confirmation of its source/rights remains necessary before claiming a completed provenance audit.

Local review at 96/32 px found the existing black/transparent icon readable on white and poor on dark fallback. The minimum icon file is present; an approved dark variant and optional @2x are quality follow-ups, not fabricated assets or a claim that they already exist. Use `scripts/docs_preview.py` → `/branding` to compare the unchanged asset. Do not replace it with another project's logo or an HA-official mark.

Per [HA brand documentation](https://contributors.home-assistant.io/core/integrations/brands/), local custom-integration images are supported from HA 2026.3; older supported HA can fall back to an upstream image/placeholder. Do not raise the 2026.1 minimum solely for branding. Official icon conventions are 256×256, optional 512×512 @2x, transparent/trimmed PNG and dark variants where needed; use a separate landscape logo only if legitimate artwork exists.

Before an actual store application, review the final **released** tree, validation results, description/topics and brand rights again. A prerelease is not automatically disqualified merely because it is a prerelease; the explicit prohibition concerns custom integrations used for testing/overriding Core integrations. Never mark the repo HACS Default or Gold before the respective process actually grants that state.

简体中文：最低分发结构、manifest、许可证与本地图标已具备，文档改版不等于默认收录。检查最终发布版本的无 ignore HACS/Hassfest；不为文档发空版本。现图标暗色回退不清晰且原始授权仍待维护者确认；不编造品牌许可、不擅自更换资源/抬高最低 HA，不自动提交商店或修改远端设置。

简体中文摘要：按任务读取指南/模块/fixture，小改相关检查、重大边界一次矩阵。默认离线、保护身份与私有数据；明确授权才并行，独立 worktree、单一整合者，文档-only 不发空版本。
