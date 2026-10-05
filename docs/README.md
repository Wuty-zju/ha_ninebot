# 开发资料入口

新会话先读 [Agent快速入口](agent/START_HERE.md) → [当前状态](agent/CURRENT_STATE.md)，
再按主题定位。不要默认把所有历史报告加载到上下文。

| 用途 | 入口 |
|---|---|
| 当前实现、发布基线、待验证项 | [CURRENT_STATE](agent/CURRENT_STATE.md)（唯一当前摘要） |
| 最小代码/测试/证据阅读集合 | [MAP](agent/MAP.md)，`python scripts/agent_context.py --topic travel` |
| 连续开发、测试、并行与交接 | [WORKFLOW](agent/WORKFLOW.md)、[工作区维护](agent/WORKSPACE_ORGANIZATION.md) |
| 全部文档的角色/覆盖关系 | [catalog](agent/catalog.json)（需要追溯时读取） |
| 公开证据来源/hash | [evidence-index](agent/evidence-index.json)；原证据仍在evidence，fixture来源在metadata |
| 最新实体精简约束 | [b24实体精简与迁移](v2x-实体精简与额定参数迁移.md) |
| 最新源码参考研究/增量路线 | [NinePlus生态专项](NinePlus生态源码审阅与ha_ninebot数据解析应用方案.md) |
| 已观察字段统一表 | [字段清单](v2x-当前字段利用与调试摘要.md#4-原始字段到当前ha用途)、[机器清单](evidence/v2x-current-field-usage.json) |
| 历次执行与真实检查范围 | [v2.x实施记录](v2x-实施与验收记录.md)、[2.0初始记录](2.0-实施记录.md) |

旧报告保留原文件名与日期，按catalog逻辑归档；“尚未实施”“默认关闭”等以当时版本为准。
具体主题契约可仍然适用，但控制策略以b15之后、实体与估算以b24之后的代码和契约核对。
公共文档不链接个人目录，不包含未脱敏raw，不依赖仓库外运行文件。
归档原则见[长期资料规则](长期开发索引与归档规则.md)。
