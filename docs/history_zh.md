# 历史行程、地图与统计

[English](history.md) · [安装与选项](guide_zh.md) · [文档入口](README.md)

> **可视化状态：早期 alpha。** 下方地图/UI/卡片用法仅记录当前实验实现，不是稳定产品或公开 demo；后续可重构、修改或随时撤回。历史采集、本地保存与查询契约单独维护。

## 选择展示方式

| 需求 | 使用方式 | 来源 |
|---|---|---|
| 当前车辆位置 | 设备位置 tracker / HA Map | 新鲜云状态；Recorder 可以留存已观察位置。 |
| 一趟或多趟路线与关联指标 | 随包历史地图卡片 | 账号本地档案，只有明确点击才获取云详情。 |
| 按日期看骑行 | 原生 Calendar | 本地记录，不自动补云、不带 GPS。 |
| 日/月变化趋势 | get_statistics 与显式 import_statistics | 已核验汇总，导入后使用原生统计图。 |
| 自动化/自定义仪表盘 | 有响应的 Action | 有界列表/详情分块、来源、覆盖范围。 |
| 新观察到的完成骑行 | Event 实体 | 启动 baseline 后的稳定新事件。 |

历史行程/点数组保存在档案与有界响应，不塞进实体属性，不为每趟/每点创建实体。打开历史页面不提高实时轮询频率。

## 开启保存与同步

在账号配置中检查**保存行程详情、自动同步历史详情、空间上限、每小时历史请求预算**。行程日历、自动同步历史详情和自动扫描历史行程均标注Beta，新账号默认关闭，已有选择保留。需要后台采集时再开启详情同步与历史扫描。关闭保存保留记录但暂停后台工作。位置响应/展示授权是另一个选项，仍须保护私有 GPS 档案与备份。

首次发现为每车保存固定 baseline，从客户端下界 `200001` 扫描到首次月份。这是**客户端搜索政策**，不是官方最早历史边界；空月不证明更早没有数据。工作按预算持续并可重启续接，初次可能较久。新月、当前/上月变化与旧缺口独立增量处理，稳定已存详情复用，不反复全量下载。

月页/详情共享账号持久预算，默认每小时60次逻辑请求，数字输入框支持1–120，填写0则取消每小时配额（不推荐），车间公平轮转，优先级低于实时状态/控制。重试/退避、认证、暂停、备份和容量守卫生效；重启不重置正数预算；0仅取消每小时配额，仍保留请求串行与云端退避。近期详情可以有限复查稳定性；迟到/修正不要求重扫全部旧月。150–180 天本地风险带优先更早结束的待保存详情，但这不是服务器保证删除日期。

### 手动与增量控制

设备页的**同步历史、暂停同步**诊断按钮（同步按钮同时恢复暂停任务）使用同一 scoped Action，不操作车辆锁。历史修改需 HA control 权限，查看状态只需读权限。保存必须开启；自动同步关闭时，明确排队的手动任务仍能执行，但不自动开启新的扫描。

```yaml
action: ninebot.sync_history
data:
  device_id: REPLACE_WITH_VEHICLE_DEVICE_ID
  operation: synchronize
  # 可选：1–12个不同月份，仅补指定月份。
  months: ["202609", "202610"]
response_variable: sync
```

重复触发合并到在途工作；`force: true` 重扫所选列表，无月份时为本月，不丢弃或强制重下已保护几何。`sync_status` 查看状态，`pause`/`resume` 保留断点。已发请求不能撤回，但提交前再次检查暂停/世代。

兼容保留 `start/continue/status/cancel` 范围任务（最多 360 月，每次最多三缺月）及 `repair/repair_continue/repair_status/repair_cancel` 散月任务。它们共享预算，“访问过范围”不等于完整云历史。现代增量优先使用 synchronize，不反复新建重叠任务。

### 正确理解进度

- **同步状态**是当前采集阶段。“等待请求预算”是本地调度，不是向九号申请权限。检查 next_request_at、retry_at、sync_phase 与错误。
- **月份扫描进度**区分处理、核验、缺口；页数/总数/成员/引用需一致，访问不等于核验。
- **详情保存进度**仅以已发现 ID 为分母，100% 不证明发现了所有云行程，也不代表每条详情都有完整路线。
- **最近成功同步**在接受页/详情进度时记录；任务完成另外由 last_completed 表示。当前待核验不会抹掉历史完成时间。
- **本月列表覆盖率**是最近一次返回列表的比例，不是累计归档。20/37 与本地累计核对齐 37 条是不同范围。

账号列表缺车时不凭过期归属继续查询；保留历史，重新正向发现后可接续。一次缺失不证明物理解绑。

## 添加历史地图

1. 安装并加载集成，检查历史/位置选项。
2. **仪表盘 → 资源**（必要时开启高级模式），添加 `/ninebot-static/ninebot-trip-card.js`，类型 **JavaScript 模块**。
3. 添加手动卡片，使用真实车辆 Calendar 或 HA device ID：

```yaml
type: custom:ninebot-trip-card
entity: calendar.REPLACE_WITH_VEHICLE_RIDE_CALENDAR
limit: 20
```

资源和可视化编辑器随包提供，无需另装卡片仓库。集成注册静态路径，**不会自动修改资源/仪表盘**。若提示 `Custom element not found: ninebot-trip-card`，确认集成已加载、资源类型正确，再强刷浏览器/App 前端；底部显示卡片版本。

关闭日历时可改用 `device_id: REPLACE_WITH_VEHICLE_DEVICE_ID`。多车最多五辆，可来自不同的已加载账号：

```yaml
type: custom:ninebot-trip-card
title: 行程历史
vehicles:
  - entity: calendar.REPLACE_WITH_FIRST_VEHICLE
  - device_id: REPLACE_WITH_SECOND_VEHICLE_DEVICE_ID
    name: 第二辆车
limit: 40
# 可选：含两端的上海业务日期。
# start_date: "2026-09-01"
# end_date: "2026-09-30"
```

limit 是**整页总数** 1–100，可以小于选车数；多车真正按最新优先合并，每车源页有界，空车不重复从头。修改筛选后本地读取，普通 HA 状态更新不反复查询历史。

### 地图、列表与曲线联动

- 最近行程（最多五年）、最近 7/30 天、上/下月或自定义日期；更早已存记录仍可选旧日期查看，不是五年删除策略。
- 选行程后摘要/地图/曲线同步；已存详情可自动本地读取，缺失不会自动抓云。
- 最多比较五趟，可跨车；标签切换主行程，汇总仅含选中的记录，不是筛选范围全部。
- 点击路线、曲线悬停、点滑块、拖选、序号播放共享焦点/范围，不发请求。“显示全部轨迹”才主动缩放，普通切换保留视角。
- “本地记录已更新，读取最新内容”来自授权的纯元数据通知。点击仅刷新本地列表/主详情并保持选择、视角、播放。重连清旧游标，不自动扫描云。
- 编辑器支持车辆、标题、日期、页数，高级字段保留 YAML；手机/窄卡片上下排列。

**获取云端详情**显式获取当前一趟/月份，可保存业务 payload。**显示已存轨迹**仅读本地且需坐标权限；尚未保存时先显式获取，或等后台保全后本地刷新。云返回成功不代表已持久化，响应会说明是否已保存。

每轮最多四块、每块 512 个源行，“继续读取已存点”接续；浏览器每条最多 4,096 个展示点，抽稀不裁剪归档，保留首尾/序号/全扫描边界。无效块也推进，缺口保持分段；读完保存响应不等于完整物理路线，服务器简化单点只能预览位置。

### 地图隐私与单位

Leaflet **1.9.4** 与许可证/资源本地随包分发，不依赖外部 JS CDN。底图**默认关闭**；开启 OpenStreetMap 后向服务商发送可见区域瓦片请求及网络信息，保留归属标注。

按原坐标绘制，坐标系未确认，不自动 WGS/GCJ 转换或道路匹配。逐点速度/点间距离单位尚未确认；横轴是**源序号**，0.5 秒播放步长只是展示，不代表骑行时间/速度。服务端最高速度与距离/时长算的总平均速度分开，不能用采样均值替代。

用户/连接/日期/车辆切换、撤权、卡片移除时清私有投影；完整轨迹/raw 不进入实体属性、Recorder、诊断或 localStorage。显式 GPS Action 响应仍可能进入自动化 trace。

## 行程摘要与车辆事件

月/日/最近行程数值继续使用Sensor，保留有意义的身份、单位与历史。Wh/km统一称为**平均能耗**。本月/今日/昨日都有距离、次数、时长、能耗、平均能耗；日均能耗使用同一个完整行程集合的能耗/距离，不拿日图距离混算部分列表能耗。无行程或零距离时不虚构平均值。最近行程是单条记录，保留距离、时长、能耗、平均能耗和最高/平均速度，不新增恒为1的重复次数实体。

**车辆行程事件**合并此前两个最近起止Timestamp实体。通过原事件的`latest_start_time`、`latest_end_time`和小体积、不含GPS的`latest_ride`属性读取，事件身份保持，引用旧Timestamp的自动化需改用这些属性。Event状态是HA观察到事件的时间，不是物理起止时间。`reported`为新近行程上报，`updated`为上报内容修订，`completed`为稳定完成确认，`late_reported`为迟到完成确认。不上电即推断起骑，历史导入只更新最新记录属性，不补发旧事件。

**车辆状态事件**逐项发布具体类型：`powered_on/off`上电/断电、`charging_started/stopped`充电开始/停止、`locked/unlocked`车辆上锁/解锁、`seat_locked/unlocked`座桶锁定/解锁。每个事件的`changes`保留同轮完整变化，`summary`为该项本地化摘要；`observed_at`为云观测时间，`observation_revision`标识同一回执。旧`state_changed`只用于恢复旧显示，不再新发，相关自动化过滤条件需更新。

默认开启的**启用车辆事件（Beta）**同时控制两类事件，保留用户逐实体禁用选择。启动、重新开启、重连、未知值及观测断档只建基线。完成确认保持持久去重；实时上报/修订采用有界运行时游标，不宣称物理推送，不新增云端轮询。

时长底层原始秒保持，默认以小时显示**四位小数**，日历详情同步。显示迁移只执行一次，之后用户自定义单位/精度继续保留。工作台紧凑显示仍按秒表达**45秒、31分46秒、1小时2分3秒**。

**本月行程列表完整率**是调试诊断：本月返回的唯一合法行程数/服务端本月次数，与**历史行程扫描进度**（已核验月份）及**行程详情保存进度**（已发现ID的详情保存比例）分别统计，任何一个都不等于完整GPS历史。诊断分类及无统计state class不会自动排除普通Recorder记录，必要时显式设置过滤。解析调试视图和脱敏诊断也不等于完整原始响应导出。

## 查询 Action

在开发者工具动作或支持响应的脚本中使用，目标为 **HA 车辆 device ID**，不是九号 SN。月份 YYYYMM，日期含两端并按上海业务日。字段权威来源为 [services.yaml](../custom_components/ninebot/services.yaml)。

| Action | 范围 | 云与写入行为 |
|---|---|---|
| get_recorded_trips | 日期分页、1–100 条、可选 sort: desc | 纯本地标量、收据、精度、来源、覆盖、detail_saved。 |
| get_trips | 单月 page/limit、可选详情 | 本地优先，缺数据或显式 refresh 可抓云；返回页不证明全量分页。 |
| get_trip_detail | ID、已验证 query_month、点分块 | 默认本地优先；local_only: true 才保证无云 fallback。 |
| get_history | 月份范围 continuation | 可以补缺月，不是本地日期浏览。 |
| get_statistics | 1–6 月，按需日统计 | 默认纯本地；refresh: true 显式读月观察，不抓详情。 |
| import_statistics | 已结束核验周期 | 写 Recorder，不抓云，不接受 refresh: true。 |
| sync_history | 同步/进度/校验/重建 | 同步可抓云，verify/reconstruct 纯本地。 |
| get_entity_migration | 身份映射 | 纯本地，包含私有身份信息。 |

```yaml
action: ninebot.get_recorded_trips
data:
  device_id: REPLACE_WITH_VEHICLE_DEVICE_ID
  start_date: "2026-09-01"
  end_date: "2026-10-08"
  limit: 20
  sort: desc
response_variable: rides
```

下一页带回 next_cursor 为 cursor，设备/日期/排序不变；默认 asc 兼容旧自动化。desc 游标成功授权读取后单次使用，15 分钟过期，每账号最多八个 continuation。单次日期范围最多 1,830 天、5,000 扫描记录、8 MiB；超限缩小范围，不静默丢记录。未来/进行中/冲突/无日期行程不伪造为已完成。

desc 绑定本车目录版本，不因别车/纯进度改变失效；旧 asc 保留账号级 revision。重载/重开连接失效；覆盖/收据每次新读，不是跨请求/跨账号不可变快照。

```yaml
action: ninebot.get_trip_detail
data:
  device_id: REPLACE_WITH_VEHICLE_DEVICE_ID
  ride_id: REPLACE_WITH_RIDE_ID_FROM_RECORDED_LIST
  query_month: "202610"
  local_only: true
  include_track: true
  point_offset: 0
  max_points: 500
response_variable: detail
```

每次 1–2,000 点，按 track_summary.next_offset 继续，包括无效行；GPS 需位置开关和日历/tracker 读权限。local_only 不可与 refresh: true 同用。抓云需当前有效归属/认证及唯一车辆/月份/详情映射；缺映射先以正确月份 Get trips，不能猜另一车详情 ID。

field_states、来源、单位、精度和 detail_context 区分缺失/null/零、列表/详情、旧几何/新标量及部分原件完整性，不返回任意完整 raw。相同纯本地详情在途合并，无持久 GPS 缓存；消耗型分页不共享，卡片不自动重放云请求。

受限用户需车辆 Ninebot 日历读权限，GPS 另需 tracker；full reader 依 HA 权限。执行前/返回前再次验证归属与世代；同步修改/统计导入需 control。无 user context 内部自动化遵循 HA 可信自动化行为。

历史详情同步关闭时，已启用的日统计仍会按需逐页补齐当月列表，每账号每轮最多一页，共享每小时额度、归属/暂停守卫和有界退避，只保存规范化列表事实。全历史扫描、详情/轨迹下载仍关闭；完整索引直接复用，缺报或冲突指标仍明确显示未知，不补成零。

## 覆盖与统计

云月汇总、单次列表、累计本地行程、已存详情、几何质量、完整性检查是**不同证据范围**。已存列表完整或缺月为零不证明云端全历史；upstream_history_complete 仍未验证。

今日/昨日次数、时长、Wh 需完整选择，或本地累计 ID/次数/里程与月汇总对账；能量/时长另外独立核对。缺失/冲突未知，确认空窗才为 0。按上海**结束日**整趟归属，不虚构午夜分摊；月边界可能需相邻月证据。

数值实体在短暂断网或重启后保留同一日期／月份的已验证值。属性保留实际采集时间，区分 current（当前）、cached（缓存）、awaiting_day_close（等待日结回报）、reported_history（历史回报）；缓存不会冒充新云回报。昨日可先显示当日最后一次已验证数据，等待日结刷新；新日期／月份不会沿用上个周期的总数。配置完成、上海零点会及时调度有需求的行程刷新，沿用请求合并与退避；本地归档更新会直接通知实体，无需再轮询。

严格的 get_statistics 日响应和 Recorder 导入仍要求历史日在结束后被观察、今日来源足够新；实体连续展示不等于历史导入已确认完整。首次添加账号仍可能在补齐索引期间未知；零里程的平均能耗没有定义，以 availability_reason: zero_distance 标明，不虚构平均值。

get_statistics 分开云月总数与有条件日值；未来填充不当 0，缺月使对应完整范围汇总未知。Wh/km 为同数据集 sum(Wh)/sum(km)，不是各趟比例均值；距离为零不能算强度。日/月归零汇总不强设 total_increasing。

### 原生趋势图

```yaml
action: ninebot.import_statistics
data:
  device_id: REPLACE_WITH_VEHICLE_DEVICE_ID
  start_month: "202608"
  end_month: "202609"
  include_daily: true
response_variable: imported
```

读取已有库，最多八条原生系列：日/月 km、Wh、次数、秒。需 Recorder 与 HA 时区 Asia/Shanghai；只导入已结束且核验周期，不把月 Wh 摊到日。queued 是队列接受，不是数据库持久提交。重复导入覆盖同周期，修正后续累计避免重复计数；新值缺失保留旧已核验点并提示。元数据/单位冲突或每系列超 12,000 点拒绝批次。

使用返回的 dashboard_cards，或实际 statistic_id：

```yaml
type: statistics-graph
title: 每日骑行里程
chart_type: bar
period: day
days_to_show: 90
stat_types: [change]
entities:
  - entity: ninebot:REPLACE_WITH_RETURNED_DAY_DISTANCE_ID
    name: 里程
```

需 HA History 集成（通常在 default_config）；月系列用 period: month，不同单位分图，小时视图不代表实际骑行分布。旧导入仍保存，调整窗口查看。卡片 statistics_entities 最多 20 个已有累计 SUM sensor/ninebot: ID，不会导入；原生日历/图表日期不随 trip-card 联动，精确范围用 get_statistics(refresh: false)。

### 日历与事件

```yaml
type: calendar
entities: [calendar.REPLACE_WITH_VEHICLE_RIDE_CALENDAR]
initial_view: dayGridMonth
```

只读日历展示本地有效过去行程，不补月/详情、不写 Recorder。只有过去记录时 state 可能 off，应按日期浏览。完整列表撤回影响当前选择但保留证据；部分遗漏保留已知。最多五年、5,000 条、8 MiB；新版 HA 可订阅变更范围，旧支持版正常读/导航。

行程事件是最近上报、修订或完成事件的观察时间，不是行程起止。十分钟内至少两次独立不变 travel 观察才稳定；启动/重启用 baseline，不重播历史。完成事件先持久去重，后修订可发修订事件但不重复发完成事件；首次未知正常。恢复旧 timestamp 不触发自动化，停机期间行程可能漏事件。

原生 `type: map` 加 tracker 和 `hours_to_show: 24` 仅显示 Recorder 实际观察位置，不会重建下载的历史 trail，指定历史路线用本卡片。

## 校验与恢复

分账号 SQLite 保存归一化事实及去凭据/个人资料的压缩业务原件。详情保留最新候选和独立选中的更佳几何，简化/空响应不覆盖旧好轨迹。**无自动 TTL**，但容量/磁盘/备份政策仍适用；180 天样本不是云保留契约，尽早保全可能被简化的详情。

空间满暂停写入，不静默淘汰；允许时健康读/实时状态仍可用，结构启动错误严格处理。HA 备份钩子暂停/排空写入，保留允许的读取；它本身不创建或上传备份。见[升级与回滚](migration_zh.md#存储升级与回滚)。

| 本地操作 | 边界 |
|---|---|
| verify/verify_continue/verify_status | 有界原件/引用校验，手动最多八对象；后台共用低优先 timer/断点，不用云预算。 |
| 对象凭据 | 身份/hash/来源/checker 绑定，未变可免重复解码，日常有界深扫仍保留；不是未来磁盘保证。 |
| quarantine | 有界复制坏字节但保留源；超大原位，缺失只登记，不伪装复制。 |
| 部分读取 | 最新/选中几何独立核验，幸存角色可读且标来源/警告；写入严格，读通不清 Repair。 |
| reconstruct 系列 | reconstruct/continue/status/pause/cancel，持久本地重建、私有恢复点、归属/身份/引用/字节预算，不猜值不抓云。 |
| 恢复源搜索 | 仅受管理副本、持久有界 continuation、精确摘要/归属/ride/来源；文件名捕获范围不等于不可变文件系统快照。 |
| 启动缺原件恢复 | schema5 窄范围验证，保留源后原子发布；不自动修复任意 schema/index。 |

重建旁存 `.recovery-<job_id>.sqlite3`，复制前查空间并验证 hash，不覆盖/自动删既有恢复点。缺失/损坏进入 blocked_recovery，不无限重试，先恢复匹配原件再明确继续。备份本身可包含坏派生索引，不是认证可加载镜像。

仅质量索引修正保留新标量与选中几何；身份冲突、缺父/链接、无法证明成员保持不可用/阻塞。坏字节仅按精确匹配管理原件恢复，先有界隔离，部分修复保留可读角色并报告缺口。旧 checker 断点不追认更大新契约。原件、引用、归一化投影与云完整性分别验证。

用最新 sync_status/verify_status/重建响应区分 pending/invalid/repaired/with_gaps。不为提高百分比删库或覆盖运行库，保留未解决 Repair。技术边界见[架构](architecture.md#storage-and-integrity)，版本性原始变化留[发布历史](../CHANGELOG.md)。
