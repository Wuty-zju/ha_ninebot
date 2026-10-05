# 连续开发、交接与并行工作流

## 一轮工作的边界

先核对分支/用户修改和[当前状态](CURRENT_STATE.md)，用topic选资料。把当前用户要求写成一个可review/revert增量：目标、约束、触及文件、验收、证据来源。
不把历次会话粘成新总手册；不依据旧goal自动开始已完成Phase、采集、控制或自动化。

修改代码时保留稳定架构/identity；修改文档时保持历史日期与未验证项。证据变更后刷新index并检查引用。
结束时交接只写结论、准确Git状态、改动/检查、未决项、下一步；不能把“建议下一步”写成已授权行动。

## 跨会话恢复

本机任务记录放工作区`private/sessions/<task-id>/HANDOFF.md`，采用仓库[handoff模板](templates/HANDOFF.md)；只放非敏感进度，不复制密码/账号/raw/位置。
它是任务状态，不是第二份产品手册。任务完成移到sessions/archive并保留索引；不塞进业务fixtures。
换会话先读handoff约百行，再核对Git与当前代码；不能将前一会话test输出当成修改后的新结果。

本机session路径仅工作区README索引，公共docs不建立私有链接。云端/他人clone可在其工作区建同类本地目录，不能运行时依赖此目录。

## 仅在用户授权并行时

1. 一位integrator确定base SHA、任务界限、各分支/worktree及预期接口。调用实际agent工具前遵守用户授权；此文不强制自动spawn。
2. 每位worker使用独立worktree/branch，按[TASK模板](templates/TASK.md)领任务。共享本机目录的agent不能假定隔离。
3. 先分开parser/model、HA表示、fixtures/证据审阅等可独立任务。翻译总表、manifest、CURRENT_STATE、公共registry/coordinator接口、发布由指定integrator统一协调。
4. 不通过文件锁占用整项目；通过OWNERS.md记录“任务→路径→owner→状态”。变更跨边界先约定接口，不覆盖另一任务文件。
5. worker交付已提交SHA、针对性结果与限制；integrator审阅干净提交后顺序整合。不要复制未提交工作或在同checkout并行切分支/commit/release。
6. 整合后对受影响交界做一次回归，避免每个worker重复完整HA矩阵；独立任务合并冲突必须审语义。

工作树：本机统一`worktrees/<task-id>`；inactive位于worktrees/archive，不能当作最新源。
已有旧branch/worktree不自动删除；确认干净再用Git worktree move/受管理工具迁移，不手改.git，不reset丢内容。
worktree最好分别安装测试环境；共享venv仅复用已验证依赖，不能并行pip修改环境。

## 测试与发布

| 改动 | 满足要求的检查 |
|---|---|
| 文档/组织 | agent_context --check；涉及本机路径再运行workspace audit；新工具的相关离线契约检查 |
| 小runtime修复 | Ruff/format、必要mypy、相关pytest/fixture replay；修改到的translations/schema/migration回归 |
| 重大功能边界 | 一次综合pytest/coverage及必要兼容矩阵，实际Hassfest/HACS/CI；不为每个小prerelease重复整个矩阵 |
| schema/endpoint不确定 | 先现有样本/fixture/取证；必要真实只读只在明确任务范围内单轮，控制另需具体授权 |

现有本机工具环境从workspace README定位；仓库CI约束看pyproject与workflows，不在新脚本隐含安装依赖。
Checks完整矩阵当前仅workflow_dispatch；日常lint新增4项标准库导航检查与公开索引校验，无HA安装或全套pytest。检查未运行不能写“全部通过”。
用户既定runtime开发发布：一个增量完成后合main、推送并发下一prerelease，记录精确SHA/真实检查与skip。
docs-only不改变manifest、不伪造功能版本；组织任务不自动push/merge/deploy。发布与生产HA安装分开。

## 资料生命周期

唯一当前摘要= CURRENT_STATE；主题事实=contract；设计理由=研究；过去执行=实施记录/版本evidence；原始证据=fixture或私有采集批次。
[catalog](catalog.json)定义用途/覆盖关系；名字含“当前”的旧报告仍受其中日期/适用范围限制。
新文档只为新主题/新证据建立，更新catalog而不是再复制旧矩阵。
大附录按需链接；旧GitHub文档路径保留，逻辑归档不等于删除源码历史。

证据文件新增/修正后：`python scripts/agent_context.py --refresh-index`，再`--check`；index可重建，不修改证据正文或原采集hash。
private原始采集不去重；重复工具/发布草稿可用相对symlink归一，必须hash验证并记处理记录。Git仓库运行必需的strings/en即使相同也都保留。

## 何时维护索引

- 实现/发布改变：CURRENT_STATE +相关contract+实际evidence。
- 新设计/协议结论：catalog +研究文档 +来源证据，标verified/推测/待验证。
- 会话结束/切换：本任务handoff，精确branch/HEAD/dirty/检查与未决项。
- 路径迁移：公开相对链接 +本机根索引/迁移hash；核验老入口仍可读。
- 没有实质变化：不为每条聊天追加文档、复制全文或重建全部历史。
