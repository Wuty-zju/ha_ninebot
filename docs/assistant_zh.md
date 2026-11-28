# Ninebot Assistant

[English](assistant.md) · [启用](#开启方式) · [工具参考](#查询本地数据) · [数据授权](#隐私与限额) · [历史任务](#可审核的历史任务beta)

**2.2.12 预览版。** 你可以询问某辆车最近一次上报的电量、找出一次已保存的行程，或核对昨天的骑行次数。Ninebot 将本地证据交给你选择的对话宿主。旧版本请查看 [2.2.0 正式版指南](https://github.com/Wuty-zju/ha_ninebot/blob/v2.2.0/docs/assistant_zh.md)和[统一工具入口迁移说明](#升级至统一的-ninebot-api)。

<a id="ninebot-assistant--21-预览"></a>

## 开启方式

1. 在支持原生工具的 Home Assistant（**2026.11** 起）中，进入**设置 → 设备与服务 → Ninebot → 配置**，找到目标账号。勾选**配置 Ninebot Assistant**，再开启该账号的助手。
2. 选择一辆车，勾选要共享的数据。用**编辑另一辆车**继续配置，最后保存。新发现的车辆初始没有助手权限。
3. 在支持工具调用的对话代理或原生 MCP Server 中选择 **Ninebot**，以活跃的 HA 管理员身份登录。为一辆车开放**汇总统计**后，可先问“昨天骑了几趟？”

| 逐车选项 | 宿主可以取得的内容 |
|---|---|
| 车辆状态 | 电量、锁状态等已缓存的标量，以及它们的观测时间和新鲜度。 |
| 汇总统计 | 指定日或月的指标、单位、覆盖情况，也可针对一项指标排查。 |
| 逐次行程 | 本地行程列表、已有详情的标量，以及继续查询需要的行程标识。 |
| 同步健康 | 全档案数量、保存与校验进度、调度器记录的状态。 |
| 历史任务 | 独立设置是否允许预览缺口、读取自己的任务或提交申请供人工审核。 |

每次私有查询都会校验登录用户、车辆当前归属和这项操作的权限。查询期间撤权会使结果失效。设备页面的车辆控制与助手授权分别设置。

## 选择宿主和模型

| 入口 | 配置方式 | 返回内容 |
|---|---|---|
| Assist／对话代理 | 在已有对话代理中选择 **Ninebot** 工具集。 | 宿主根据工具结果组织回答。 |
| 外部 MCP 客户端 | 登录 HA 提供的 MCP Server，专用地址为 `/api/mcp/ninebot`。 | 返回原生工具说明及当前 HA 用户获准读取的结果。 |
| 自动化／开发者工具 | 给 `ninebot.ask` 传入已有且支持工具调用的 `ai_task` 实体。 | 有界的工具回执，以及支持时由回执整理的答案。 |

模型可在本地运行，也可由供应商托管。支持 MCP prompts 的客户端可以在提示列表中选择 Ninebot。模型配置由宿主管理，身份验证由 HA 负责。[HA LLM API](https://contributors.home-assistant.io/core/internals/llm-api/) · [MCP Server](https://www.home-assistant.io/integrations/mcp_server/) · [AI Task](https://www.home-assistant.io/integrations/ai_task/)

## 核对MCP的API选择

总入口 `/api/mcp` 遵循原生 MCP Server 的配置。要使用指定工具集，先关闭**公开所有 AI 工具**，再选择所需集合。总入口的**只读**设置会过滤可创建申请的写工具。若开启公开全部工具，后续新增的集合也会被公开。

专用入口 `/api/mcp/ninebot` 直接选择 Ninebot API。总入口的集合选择与只读过滤独立生效。要在所有宿主上阻止某辆车提交申请，可将该车任务级别设为**关闭**或**仅预览**。最多可以发现八个查询工具、四个任务预览或查询工具和一个申请工具。发现列表取各车授权的并集，调用时仍需目标车辆的对应权限。[MCP 配置说明](https://www.home-assistant.io/integrations/mcp_server/#configuration-options)

## 通过已有AI任务查询

```yaml
action: ninebot.ask
data:
  ai_task_entity_id: ai_task.your_model
  device_id: your_ninebot_device_id
  question: 昨天骑了几趟？
response_variable: ninebot_answer
```

先看 `outcome`。若为 `evidence` 或 `partial`，读取 `results`，也可以显示 `{{ ninebot_answer.get('answer', {}).get('text', '') }}`。可选的 `answer.text` 由本地已校验回执整理，`request_ids` 标出所引用的调用。对话宿主可以自行生成另一份表述。

多辆车匹配时，`needs_input` 可通过 `next_step.choices` 返回候选。带上目标车辆的 HA `device_id` 再次提问，即使两辆车同名也能选定。指定后整轮查询绑定该车。这个只读 Action 可直接指定发现列表前二十辆以外的车辆。查询遭拒时丢弃私有结果。

## 查询本地数据

| 原生工具（统一前缀 `ninebot__`） | 参数 | 读取内容 |
|---|---|---|
| `get_vehicles` | 无 | 已授权的 HA 车辆引用、名称、`allowed_reads` 和本地数据是否就绪。 |
| `get_vehicle_status` | `vehicle_ref`，可选 `metric` | 单项读数或车况摘要，附来源分组、观测时间和新鲜度。 |
| `get_statistics` | 车辆、`period`，可选 `metric` | 按业务日或月份统计。询问一个数值时指定单项指标。 |
| `explain_statistics` | 车辆、`period`、`metric`，可选 `language` | 指定指标、质量，以及这辆车获准取得的进一步依据。 |
| `get_recorded_trips` | 车辆、`start_date`、`end_date`，可选 `limit`、`cursor` | 按时间降序返回与日期范围重叠的一页行程。最多 31 天，默认每页 10 趟，上限 20 趟。 |
| `get_trip_detail` | 车辆、`ride_id`、`query_month` | 本地已保存的详情标量。使用行程列表给出的原始标识和 `YYYYMM` 月份。 |
| `get_sync_health` | 车辆 | 已保存的不同车次、详情及简化轨迹数量、计划进度和请求节奏。 |
| `search_help` | `query`，可选 `language`、`include_procedures` | 有版本和出处的公开帮助，可按需附带匹配的操作清单。 |

统计支持 `today`、`yesterday`、`this_month`、`last_month`、`YYYY-MM-DD` 或 `YYYY-MM`，范围从 2000 年到当前业务日期。日期采用 **Asia/Shanghai**。经 Action 在午夜前发起的问题会保留本轮开始时的业务日。在 Assist 或 MCP 中，同一问题的后续查询应沿用首份回执的明确日期。本月统计截止当前业务日。

<a id="statistics-check"></a>

## 理解数据依据

**举例。** 昨天骑了两趟，以及完整档案中某天明确为零趟，都是可核对的结果。2.2.12 的 `explain_statistics` 只返回所问指标。`evidence.outcome` 说明实际排查到了哪一步。

| 结果 | 实际读取 | 如何理解 |
|---|---|---|
| `metric_verified` | 一次本地统计读取。周期已结束，数值可用、有观测时间，来源也已确认覆盖。 | 可报告数值、业务日期、单位、观测时间和来源。 |
| `coverage_review` | 先看该指标，再按预算检索适用的公开帮助。只有该车开放了同步权限才会读取同步状态。 | 引用已记录的指标原因。`data.steps` 列出实际执行的步骤。未开放同步健康时会注明 `sync_health_outside_grant`。 |

日统计以已校验的行程窗口判断完整性，月统计要求有月末之后的服务端汇总观测。今天可以已有数值，同时仍处于累计中。`quality.partial` 表示排查证据不完整。空值保留为未知。数据缺口的具体原因仍需对应证据。

| 返回依据 | 含义 |
|---|---|
| `query.as_of` 与 `received_at` | 前者为本次查询时间，后者为实际读数或档案的观测时间。 |
| `distance_basis` 与业务日 | 每日里程可能取自服务端日图。按行程计算的统计将整趟计入结束日；行程列表则按时间重叠选择。 |
| 列表覆盖 | `quality.page_as_of` 表示游标分页的选择截止点。月观测与 `has_more` 有助于区分一页为空和整个周期确认为空。 |
| `archive_counts` 与 `details` | 全档案统计含导入记录和仅有摘要的行程；当前同步计划另外计算进度。 |
| 车辆状态 | ACC 表示附件／点火电源，座桶锁字段表示锁舌。查看某项读数时，用其来源分组的观测时间与新鲜度。 |
| 速度与能耗 | 行程最高速度和里程除以时长得到的平均速度分别计算。逐点样本单位仍待核验。电池健康评分沿用上报评分。 |

精确轨迹经仪表盘单独授权读取。要重放轨迹或比较整趟统计，可打开行程卡片。[档案与地图](history_zh.md)

## 版本化帮助

使用指南、字段说明及支持案例随集成分发，检索时核对适用版本，最多在 4 KiB 内返回三项。结果附知识版本、语言、固定版本的来源链接以及可直接显示的 `citation`。“地图怎样比较两趟行程”可直接查公开文档。询问某辆车的具体情况时，需要该车的数据回执。

## 任务流程

需要分步处理问题时传入 `include_procedures: true`。匹配的清单包含所需输入、可用读取方式、调用上限、停止条件与核对依据。例如 **SK05** 先确认车辆、周期和指标；后续读取仍遵守同一辆车的权限。普通检索默认只返回主题和引用。

## 可审核的历史任务（Beta）

1. 将目标车辆的历史任务权限设为**仅预览**或**允许申请**，默认**关闭**。一次可预览最多 31 个业务日，查看待补详情和尝试次数上限。
2. 宿主只有取得**允许申请**权限才可提交本地申请。成功后收到 `task_recorded: true` 及任务 ID。若应答中断，先查原申请人的任务列表。
3. 申请人以同一个 HA 管理员身份打开侧栏 **Ninebot Assistant**，核对车辆、日期与尝试限额，选择**批准并同步**，随后由现有历史调度器执行。
4. 可核对已保存结果，或选择**取消任务**终止该任务的剩余工作。面板记录实际尝试数、重试、到期时间与持久进度。HA 通知可链接到申请。

| 任务工具 | 逐车权限 |
|---|---|
| `task_vehicles`、`preview_detail_sync`、`get_tasks`、`get_task` | 仅预览或允许申请。已保存的任务属于原申请人。 |
| `request_detail_sync_approval` | 允许申请，审核服务就绪，宿主允许写工具。 |

每个计划固定最多 20 份缺失详情。尝试预算为所选数的两倍，上限 20，重试共用预算。`requests.maximum_detail_attempts` 及旧别名 `maximum_after_approval` 表示这一额度。人工确认有效期为 10 分钟，已批准工作在 24 小时后到期。从官方 HA 备份恢复后需要重新批准。

## 隐私与限额

选定的模型宿主可能收到车辆名称、日期、标量及行程引用。助手结果排除精确位置、完整轨迹、凭据、原始响应和日志。Ninebot 专属工具不提供车辆控制或数据库导入；HA 的其他工具集合各自授权。

本地查询每名用户滚动一分钟最多准入 20 次，整个 API 最多两个并行读取。单次工具返回限时 10 秒、上限 16 KiB，每条列表记录最多 2 KiB。统计排查占一次初始准入，随后按实际执行的读取计数。幂等的本地 Action 遇到确定的暂忙响应最多重试一次，`query.local_action_attempts` 记录尝试次数。游标和被拒的读取不会自动重放。

## 升级至统一的 Ninebot API

从 2.2.9 预览版起，专用入口为 `/api/mcp/ninebot`。在宿主的工具选择中移除 **Ninebot tasks**，选择 **Ninebot**，保存后开启新会话。旧入口 `/api/mcp/ninebot_tasks` 返回 404。之前开放过旧任务权限的账号可进入**审核现有任务权限**，逐车设定并应用。既有申请沿用原审核规则。[版本切换与回退](migration_zh.md#在-22-各版本之间更新)

## 常见问题

| 表现 | 优先检查 |
|---|---|
| 看不到 Ninebot 工具 | 核对 Core 的原生工具支持、账号的 Assistant 开关及宿主中选定的集合。 |
| 提示 `not_authorized` 或需要选车 | 核对登录管理员、所选 HA 设备和该车的读取授权。拒绝读取不能推断行程是否存在。 |
| 统计未知 | 指定指标和业务日期，再看它的观测时间、逐项质量与覆盖依据。 |
| 地图只有起终点 | 查看已保存详情的轨迹类型。只有两个端点的记录无法还原中间路线。 |
| 看不到申请工具或需要等待 | 检查该车任务级别、宿主只读选项、审核服务和 `retry_after_seconds`。 |
| 需要反馈故障 | 在 HA 启用调试日志，复现一次，下载脱敏诊断。[日常使用指南](guide_zh.md#常见问题) |

[开发入口](development.md) · [合成评测案例](../tests/fixtures/assistant/evaluation-tasks.json)
