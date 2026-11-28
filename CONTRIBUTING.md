# Contributing

Start with a problem a user can observe. For a bug, include the installed Core and Ninebot versions, the smallest reproduction and the expected result. For a design change, link the existing behavior it will affect. Use the [issue tracker](https://github.com/Wuty-zju/ha_ninebot/issues) for product bugs and proposals, [Discussions](https://github.com/Wuty-zju/ha_ninebot/discussions) for usage questions, and the [security policy](SECURITY.md) for private reports.

## A small, reviewable change

1. Branch from `dev` and find the implementation, guide and nearest existing fixture in the [source map](docs/development.md#start-with-a-task-not-the-whole-repository).
2. Reproduce the behavior with a synthetic observation or an existing redacted fixture. Include the relevant device, date, units, permissions or coverage assumptions.
3. Change the owner of the behavior. Follow one query from caller through authorization to its result, or one UI interaction from selection through its rendered view.
4. Run the smallest check that distinguishes the old behavior from the fix. Report its result in the PR. Let the repository’s workflows select broader runtime and packaging checks.
5. Describe the effect on users, any upgrade step and how to roll back. Keep one independently reviewable patch per concern.

## Branches and releases

| Branch | Purpose | Delivery |
|---|---|---|
| Short-lived topic | Focused implementation or documentation based on `dev` | Open a PR to `dev` with the relevant evidence. |
| `dev` | Integrated development | Publish a useful numeric 2.2.x preview from its reviewed head. |
| `main` | Accepted stable code and public-facing documentation | Promote a tested candidate, or apply an isolated stable fix and merge back into `dev`. |
| `release/<version>`, when needed | Stabilization while the next cycle advances | Promote the frozen candidate with a normal merge. |

The release workflow builds `ninebot.zip` from committed runtime files. Stable promotions require the full validation receipt for the reviewed inputs. Preserve existing public tags and assets. The default README can describe a named preview while the code on `main` remains stable. [Publication and download accounting](docs/development.md#branch-and-release-channels)

## Documentation and examples

| What changed | Where to write |
|---|---|
| Setup, defaults or configuration | Everyday guide |
| Archive, map, replay or statistics | History guide |
| Tools, grants, citations or human approval | Assistant guide |
| A dependency, ownership boundary or public response | Architecture and development reference |
| A shipping behavior | Changelog with its version |

English and Simplified Chinese user guides should agree on examples, names, quantities and limits. Preserve public anchors or add aliases. Diagram sources generate light and dark SVGs. Run `python3 scripts/check_docs.py` for links, paired outlines, brand assets and public file boundaries. Rebuild diagrams with `python3 scripts/render_architecture.py` after changing their source. [Documentation index](docs/README.md)

Edit the integration’s `strings.json` and English translation together. Preserve message placeholders in each locale. Share synthetic examples in screenshots and tests. Raw GPS, tokens, account IDs, production logs and private workspace material belong outside public PRs.

## Parallel coding agents

Use independent branches and worktrees when tasks have separate owners. For a map and Assistant change, agree on the data contract first. One writer can own selection and rendering while the other owns evidence projection and permissions. The integrator reviews both against the same retained ride or synthetic question and handles the shared documentation, merge and release. [Engineering handoff](AGENTS.md)

简体中文：普通改动从 `dev` 开始，选择一个可复现的问题和相应测试。将用户可见的变化同步到中英文手册。长期分支通过正常合并保留开发记录，正式提升和回灌由整合者负责。[源码与验收入口](docs/development.md)
