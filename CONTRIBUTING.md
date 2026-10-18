# Contributing

[Development map](docs/development.md) · [Architecture](docs/architecture.md) · [Agent constraints](AGENTS.md)

Thank you for improving Ninebot for Home Assistant. Start with a focused problem, the current behavior and the relevant evidence. Use [Discussions](https://github.com/Wuty-zju/ha_ninebot/discussions) for usage/design questions, Issues for reproducible bugs or proposals, and [SECURITY](SECURITY.md) for sensitive reports.

## A small, reviewable change

1. Inspect the current checkout and preserve other people's work. Use a short-lived topic branch from `dev` for normal development.
2. Follow the [task-to-code/test table](docs/development.md#start-with-a-task-not-the-whole-repository), not the whole release history.
3. Preserve entity identity, account isolation, privacy, exact dependency pins and bounded asynchronous behavior.
4. Update the relevant guide and both primary language pages when user behavior changes. Keep runtime resources inside the integration directory.
5. Run the smallest meaningful offline checks from the [validation table](docs/development.md#select-the-smallest-meaningful-checks). State exactly what ran/skipped and why.
6. Open a PR describing the problem, resulting behavior, compatibility/identity effects and evidence. Do not create a new permanent report per PR or bump a version for prose alone.

## Branch targets and releases

- **`dev`** integrates reviewed development. Target it for features, development fixes and experimental UI work. Keep unfinished work isolated; do not make experimental cards necessary for core synchronization.
- **`main`** is the stable release/promotion line. Stable fixes and stable documentation/tooling corrections can target it. The current 2.0 version remains a prerelease until stable acceptance; creating these branch roles does not certify b94 as stable.
- Use `feat/…`, `fix/…`, `docs/…`, `chore/…` or `perf/…` for one bounded task. Name the goal, not a beta number. Squash short topics when appropriate and retire them after integration; never reuse a squashed topic for the next task.
- Promote an accepted development cycle to `main` with a normal merge, then synchronize `main` back into `dev`. Do not squash the long-lived branches. A stable hotfix must not merge unrelated next-cycle development; use a minimal fix or a traced backport.
- Only create a temporary `release/<version>` when candidate stabilization and next-cycle development truly overlap. Promote the frozen branch, not the advancing `dev`.

Feature PRs do not each require a release. Publish useful preview increments from `dev` (or a frozen release branch); publish accepted stable versions from `main`. Keep old tags/Releases immutable, preserve meaningful IDs/data, and do not delete active branches or worktrees. Branch cleanup needs verified integration and recovery evidence.

简体中文：普通开发从dev开短分支并PR回dev；main用于稳定提升/修复，当前2.0仍是预发布。长期分支正常merge，稳定补丁不夹带下一轮功能；按完整成果发预览版，保留旧tag/Release。发布前参考[预检与门槛](docs/development.md#branch-and-release-channels)。

## Documentation and examples

English is the maintenance source; README, setup/history/migration guides have corresponding Simplified Chinese pages. Architecture/development have Chinese summaries. Keep meaning, units, defaults and limits aligned; a matching outline alone is not a translation review.

Use repository-relative links and stable section anchors. Preserve or bridge existing public links when reorganizing. Synthetic examples must be labeled; review screenshots and diagnostics before sharing. Do not publish private captures, credentials, real vehicle/account identifiers or precise trails.

```sh
python3 scripts/check_docs.py
python3 -m unittest discover -s scripts/tests -p 'test_docs.py'
```

These standard-library checks do not log in, call cloud APIs, inspect HA configuration or download dependencies. For actual rendering changes, use the [synthetic preview](docs/development.md#documentation-assets-and-preview), not a production account.

## Parallel coding agents

Use parallel agents only when explicitly authorized. Assign each a fixed base, contract, owner/file scope and separate branch/worktree. One integrator owns shared contracts, manifest/version, aggregate translations, docs navigation and release decisions. Handoffs include commit/dirty state, affected behavior, checks/skips and remaining evidence; do not duplicate entire manuals in prompts.

Do not force-push, discard user changes, edit production registries/databases or perform vehicle controls to test a contribution. New live cloud/SMS/control testing needs a current specific authorization. See [AGENTS](AGENTS.md) for product invariants.

简体中文：先定问题与证据，按任务定位代码/测试，小改只做相关检查；英中关键行为同步，保留身份、预算和隐私。明确授权后才并行，独立 worktree、单一整合者；PR 如实记录运行/跳过，不为文档发空版本。
