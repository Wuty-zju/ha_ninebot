# 开发资料

先读[START_HERE](agent/START_HERE.md)与[CURRENT_STATE](agent/CURRENT_STATE.md)，只加载本任务相关契约。

| 唯一维护位置 | 内容 |
|---|---|
| [BACKEND_AUTH](contracts/BACKEND_AUTH.md) | serve、native发现/cache、密码/SMS、账户与reauth |
| [RAW_DATA](contracts/RAW_DATA.md) | RawStore、未知字段、debug、diagnostics与证据门槛 |
| [TRAVEL](contracts/TRAVEL.md) | Ride/轨迹/单位、月日表、历史Actions与Event |
| [VEHICLES_ENTITIES](contracts/VEHICLES_ENTITIES.md) | 身份/精简/规格/BMS/GPS/Image、请求需求图 |
| [CONTROLS](contracts/CONTROLS.md) | 权限三态、门禁、一次发送/状态协调、物理结果边界 |
| [FIELD_INVENTORY](reference/FIELD_INVENTORY.md) | 唯一136路径全字段用途表；按字段查，不默认全文读 |
| [PROGRESS](development/PROGRESS.md) | 阶段的问题→决定→结果→实际证据，及待实施NR门槛 |
| [WORKFLOW](agent/WORKFLOW.md) | 测试/发布/交接/并行与文档维护规则 |

research保留独立[NinePlus专项](research/NinePlus生态源码审阅与ha_ninebot数据解析应用方案.md)及[ninecli依赖审计](research/ninecli实现解读与依赖审计.md)，按具体章节取证。
archive是历史基线，不是待执行goal。完整角色/旧路径迁移见[catalog](agent/catalog.json)，证据定位见[evidence-index](agent/evidence-index.json)。
公共文档只引用仓库内相对文件或固定来源链接；原始敏感证据留本机private。
