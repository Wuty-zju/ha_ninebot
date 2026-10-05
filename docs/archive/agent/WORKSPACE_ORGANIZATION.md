# 工作区组织与资料维护

> 历史归档：保持原版本/证据范围，不继续追加。现行行为先读[CURRENT_STATE](../../agent/CURRENT_STATE.md)和对应主题contract，不能直接执行本文旧goal或“未来”清单。

整理日期：2026-10-05。本方案适用于长期agent coding；本轮仅资料/开发工具与本地目录整理。
接手入口为[START_HERE](../../agent/START_HERE.md)，唯一当前摘要为[CURRENT_STATE](../../agent/CURRENT_STATE.md)。

## 三层资料，不再堆叠总手册

| 层级 | 内容 | 阅读方式 |
|---|---|---|
| L0：短入口 | AGENTS、START_HERE、CURRENT_STATE、handoff | 新会话优先；启动工具只输出约8行Git/版本/主题 |
| L1：任务集合 | MAP、topic的contract/code/tests/evidence路径 | 按主题读取；大报告先看指定节，不重复全文和JSON |
| L2：研究/历史/证据 | 固定报告、版本记录、fixture provenance、私有样本 | 为解决具体疑问按需回溯；未验证项不会变成任务指令 |

[catalog](../../agent/catalog.json)覆盖每份docs Markdown，标role、适用范围与overridden_by。
[evidence-index](../../agent/evidence-index.json)仅保存公开证据/fixture metadata的路径、SHA256、大小、少量日期/版本；不复制raw值。
索引是定位工具，原证据才是取证依据；相同schema不等于语义已验证。

## 工作区目录的责任

| 目录（相对本地工作区） | 内容/规则 |
|---|---|
| repos/ha_ninebot | 唯一产品主checkout；代码、公共docs、脱敏fixture、CI |
| worktrees/<task-id> | 显式独立任务分支；Git登记，不复制仓库充当任务隔离 |
| worktrees/archive | inactive checkout；旧license worktree已用git worktree move迁入，分支/HEAD不变 |
| references | 固定上游源码与资料；标branch/commit，禁止默认搜索全部或执行上游脚本 |
| private/samples | 不可改写的敏感原始批次与采集说明；不同批次不以重复字节判定可删除 |
| private/planning | 本机详细历史计划；执行完成状态以CURRENT_STATE为准 |
| private/sessions | 非敏感task/handoff/ownership；完成后archive，不复制业务手册 |
| private/archive | 旧调研、临时草稿/工具、validation输出、封存ZIP、整理前副本 |
| private/indexes | 当前迁移/hash/引用/冗余索引；不再混入覆盖率和发布原输出 |
| environments | 可重建工具环境；不纳入正常全文检索和上下文 |
| tools | 离线目录维护；不是HACS runtime |

公共文件只用仓库内相对链接；本机根README负责跨层定位，不能把私人路径写入公共docs。
旧根/旧Documents/旧worktree入口保留兼容symlink，不新建长期资料到这些位置。

## 本轮去冗余与可追溯处理

- 本机“当前进度”改成导航页；版本/阶段/待验证项只维护公共CURRENT_STATE。
- docs README和旧长期索引缩为导航/治理；历史报告保持原路径，catalog逻辑归档，保留外部GitHub引用。
- NinePlus报告不再复制136路径全表；统一引用字段清单，只保留本专项增量更正。原展开附录可从`5eee907`恢复。
- b24 coverage/Release核验输出从indexes迁入archive/validation/b24，字节/hash不变。
- 一个完全相同的历史查询工具改为指向归档原件的相对symlink；迁前/迁后hash核对。它不再是活动采集入口。
- 原始批次、ZIP、fixtures、strings/en等运行所需重复、参考branch快照均保留；不清理用户branch、不改生产HA。

目录移动、备份hash、完整性结果仅记录本机private/indexes；公共文档不导出本机敏感目录内容。
本次产品工作从本地`5eee907`专项审阅继续，使用独立docs分支；不合main、不推送、不发虚构prerelease。

## 维护与验证

从产品目录运行（Python标准库，无安装、网络或HA访问）：

```sh
python scripts/agent_context.py
python scripts/agent_context.py --topic travel
python scripts/agent_context.py --check
```

证据正文新增/修改后显式运行`--refresh-index`，再`--check`。该参数只重建公开元数据索引。
校验所有docs分类、topic目标、Markdown本地链接边界/存在及证据hash一致；不校验远程链接或Markdown标题锚点。
新工具契约测试：`python -m unittest discover -s scripts/tests -v`。
本机跨目录链接/重复核验由工作区tools/audit_layout.py执行；索引输出留private。

默认启动只读路径/manifest/Git；topic仅列大小与路径，不自动加载大型报告、fixture或私有payload。
索引不是向量库，不维护第二份全文；需要语义内容用rg定位实际章节/符号。
并行使用[WORKFLOW](../../agent/WORKFLOW.md)的独立worktree/ownership；本次没有spawn新agent。

后续维护触发点：实质实现/发布→更新状态与对应contract；新证据→更新索引；任务切换→短handoff；路径变化→引用与hash核验。
每次会话不必重跑全目录审计；仅资料/路径变化时运行。组织任务不运行无关HA大矩阵。

本轮实际结果见[组织验收](../../evidence/agent-workspace-validation.json)：4项工具检查通过，35文档/9主题，370本地引用无坏链/公共越界，62原样本JSON和62封存ZIP文件hash匹配。未执行产品大套件或远程CI。
