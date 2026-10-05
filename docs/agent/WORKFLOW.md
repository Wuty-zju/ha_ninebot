# 连续开发与工作区维护

## 默认阅读与一轮增量

START_HERE → CURRENT_STATE → `python scripts/agent_context.py --topic <topic>` → 一个现行contract → 相关code/tests → 必要证据。
不要启动时遍历全部docs、fixtures、references、private或venv。大字段表用rg定位路径，研究报告读指定章节。
旧goal、未脱敏手册、版本报告是上下文，不是重复开发已完成阶段/采集/控制的指令。
目标以当前用户要求为准；先核对branch/HEAD/用户修改，再做可独立review/revert增量。

## 文档预算：更新优先，不按版本新建

| 信息 | 唯一位置 | 更新规则 |
|---|---|---|
| 当前发布/实现/未决事实 | CURRENT_STATE | 重大事实变化更新，保持短 |
| 实现契约/语义 | contracts的5个主题文件 | 修相关节；不新增bXX专项契约或重复全文 |
| 问题→决定→进度→验收 | development/PROGRESS | 重大阶段一行，小修更新已有行；运行限制明确 |
| 用户发布变化 | 根CHANGELOG | 每release一条短变化与限制，不复制设计报告 |
| 全字段用途 | reference/FIELD_INVENTORY +已有机器清单 | 单一维护位置，避免每报告重印136表 |
| 测试/调查证据 | 已有evidence + fixture metadata | 新测试摘要优先更新组织/验收账本，不每小阶段建Markdown；新raw/schema才独立样本 |
| 新协议/架构研究 | research | 仅现有主题无法承载的独立研究；标commit/许可/证据，不混同实现 |
| 已失效版本稿 | archive | 冻结适用版本、指出现行contract，不继续追加 |
| 任务dirty/owner/交接 | 本地private/sessions | 不进入产品设计，50行左右，不存raw/密码 |

新增文档先判断上述位置能否承载；不能承载才新建，并登记catalog角色/范围/来源。
同一事实出现多处改为链接。检验指标是默认阅读集合大小，而不是盲目删除历史证据。
原报告路径在catalog.legacy_paths映射；`--resolve docs/旧稿.md`查新路径/现行contract。内部引用已修正，不建大量跳转Markdown。
历史Git提交保留原稿；当前archive只用于特定追溯，不能把其中“默认关闭/未来”当当前需求。

## 本地布局与兼容入口

工作区名称ha_ninebot-workspace，不是Git仓库；repos/ha_ninebot是产品；worktrees/<task-id>是任务checkout。
references放固定上游；private/samples放不可改写的敏感批次；private/archive放旧稿/临时草稿/工具/验证输出。
private/planning只做规划导航，已完成A–F私有手册归档，不维护第二份最新计划。
private/indexes只存本机路径/hash/引用索引；coverage/Release原输出归archive/validation。
environments可重建，不纳入全文检索；tools只作离线目录维护，runtime不依赖workspace根。
用户要求归拢的订阅模板位于private/archive/adjuncts，不是Ninebot实现/SDK，不能部署或混入产品。
旧工作区/旧review/旧Documents/旧模板位置仅兼容symlink；新资料不放旧入口或临时目录。
公共文件不包含本机私有链接；历史metadata中的原路径只作证据不反向改写采集hash。

## 并行与交接

只有用户授权并行后才spawn agents。integrator确定base SHA、接口、owner/path/worktree，用[TASK](templates/TASK.md)记录。
每worker独立branch/worktree，禁止共享checkout切分支/commit；没有实际隔离不能假定工具自动隔离。
manifest、translations总表、CURRENT_STATE、registry/coordinator接口和发布指定单owner；跨边界先约定接口。
worker交付干净提交SHA与实际针对性结果；integrator顺序整合、审语义冲突、只回归受影响交界。
共享venv只能复用已验证依赖，不能并行pip；需要不同依赖用独立环境。
任务交接用[HANDOFF](templates/HANDOFF.md)只记目标、Git/dirty、结果/skip、未决与下一步建议；完成归sessions/archive。
旧branch/worktree保留，移动用Git worktree move/repair；不force-push、reset-hard或删除用户branch。

## 测试与发布

| 改动 | 必要检查 |
|---|---|
| 文档/组织/导航工具 | `agent_context --check`、相关标准库工具测试；本机迁移才workspace audit/hash |
| 小runtime修复 | Ruff/format、必要mypy、相关pytest/fixture replay及翻译/迁移边界 |
| 重大功能/兼容边界 | 一次综合pytest/coverage及必要多HA矩阵、实际Hassfest/HACS/CI |
| schema疑点 | 先既有资料/fixture/mock；当前任务明确要求才极少量真实只读；控制须具体授权 |

Checks日常lint/format+轻量导航检查；完整HA矩阵仅workflow_dispatch，未运行不冒称全部通过。
同一提交通过后不重复全测，除非实质改动/失败/风险要求。
已有runtime发布要求：完成增量合main、推送下一prerelease，核实main/tag/CI实际范围；组织docs-only不虚构功能版本、不自动push/部署。
生产HA只读；没有修改配置/registry/数据库/生产custom_components或重启的授权。

## 引用与证据检查

公共相对文件链接必须留在仓库内；外部源码尽量固定commit，动态官方资料标核实日期。
单位/alias/枚举标签区分源码、fixture、真实请求、推测/待验证；synthetic数值不证明实车语义。
`python scripts/agent_context.py --check`校验所有docs角色、topic/旧路径目标、公共本地引用与evidence元数据一致。
evidence改变后显式`--refresh-index`再检查；启动/topic只读路径，不加载私有值。
本机tools/audit_layout.py只在目录/引用变化时运行，输出索引留private；不要每个coding回合扫全部目录。
原样本/ZIP/fixtures不按相同字节删；相同归档工具/草稿可hash确认后symlink归一，保留出处。
