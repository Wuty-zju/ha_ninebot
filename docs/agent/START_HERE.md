# Agent 快速入口

用途：新会话、上下文精简后恢复、独立子任务接手。先读本页与[当前状态](CURRENT_STATE.md)，不要默认通读全部研究报告。

1. 核对 `git status --short --branch`、`git log -3 --oneline`。有用户修改先理解，不能覆盖。
2. 读[当前状态](CURRENT_STATE.md)，区分已发布代码、仅本地文档、已完成阶段和待验证数据。
3. 用 `python scripts/agent_context.py --topic travel` 等获取该主题的最小阅读清单；不传topic只显示短启动信息。
4. 读对应contract→code→相关tests→所需evidence。只在结论不足时回溯[历史目录](catalog.json)。
5. 继续长期工作前查看本任务交接记录；新任务按[工作流](WORKFLOW.md)建立一份小记录，避免把整个聊天复制进手册。

| 问题 | 命令topic | 先解决什么 |
|---|---|---|
| 原始数据、未知字段、NinePlus | raw | 当前RawStore已存在；NR是增量设计 |
| 行程/速度/日表/历史Actions | travel | max与avg、coverage、日表证据、detail归属 |
| BMS、包身份、child | battery | 稳定身份与旧实体连续性 |
| 登录/SMS/配置/reauth | auth | 密码不入argv/ConfigEntry，多会话隔离 |
| 控制/权限/回读 | controls | UNKNOWN≠允许字典、接受≠物理完成 |
| 实体、名称/翻译/迁移 | entities | b24精简不恢复，unique_id不变 |
| GPS/Image/刷新效率 | polling | opt-in、source freshness、功能需求图 |
| 新版本发布/测试选择 | release | 看实际检查范围，不为小版本重跑大矩阵 |
| 工作区、资料、并行协作 | workspace | 单一状态源、责任边界、handoff |

完整路由见[代码/证据地图](MAP.md)。机器目录[资料目录](catalog.json)、[证据目录](evidence-index.json)用于定位，**不需要在每个会话全部加载**。

证据顺序：自有真实recorded fixture/既有采集关系 → recon有取证支持的协议 → server命令/透传 → NinePlus parser行为 → 字段名猜测。
合成fixture、mock通过和旧发布CI分别证明不同事项；不相互替代。

公共docs没有本机私有地址。需要本地未脱敏材料时，从工作区根README进入private索引；默认只读索引/元数据，按需读取必要文件，不全文输出凭据/位置。

维护本入口应保持简短：细节写主题contract；当前事实写CURRENT_STATE；过去事实留版本记录。不要建立第二份“最新总手册”。
