# v2.x 图片、位置与请求依赖契约

2026-10-04，Phase 8，基于已发布 2.0.0b7。本文描述增量实现，不替换设计报告的历史基线。

## 图片来源与隐私边界

**真实只读证据**：私有会话副本、随机 Bearer 的本机 ninecli 0.1.7 serve，
一次 vehicles 业务查询返回两辆车的三类图片字段：`img_url`、`v6_light_img_url`、
`v6_dark_img_url`。六个 URL 都使用 HTTPS、默认端口和
`oms-oss-public.ninebot.com`，无 URL 用户信息/fragment，只有不透明
`nbchecksignv1` 查询参数。没有把签名认定为无害格式参数。

对其中一个已返回资源移除所有查询参数后，一次不跟随跳转的匿名 HEAD 返回
200 / image/png。未下载图片正文。这只证明所选资源可匿名访问，不证明所有
车型、所有资源或未来 URL 同样可用。公开证据见
[图片来源摘要](evidence/v2x-image-origin.json)。完整 URL、签名、真实车辆和会话
只在 Git 外私有目录，不进入 diagnostics、fixtures 或实体。

`image_urls.py` 集中处理图片 URL：精确审核域名、HTTPS/443、无用户信息、无
fragment、有限长度与合法图片路径；仅识别上述单一签名参数并移除，不转发
签名。未知域名/参数、畸形路径、非图片扩展拒绝。首选 light，非法时允许安全
img_url 回退；dark 保留 schema 评估，暂不生成额外实体。不能接受任意 HTTPS
URL，也不能借图片 URL访问 loopback/内网或传递凭据。

继续使用 HA ImageEntity 的 HTTP 客户端、TLS 验证、内容解码及图片缓存。
只覆盖 `_fetch_url` HTTP 钩子，禁止 redirect，错误日志不含 URL/异常正文。
此处覆盖受保护钩子是为了收紧 Core 默认的跳转和失败 URL 日志；最低/稳定
Core 上的实际实体测试是兼容门槛，不声称它是永远稳定的公共 SDK。
没有自建下载器或修改共享客户端设置。未知/不可访问图片安全失效，不自动携带
原始签名重试。HTTP 请求失败不污染车辆其它数据组。

图片 URL 不变时保留 `image_last_updated` 和 Core bytes cache；实际 URL 改变
或消失才清缓存，移除 URL 时不可用。签名轮换归一化后 URL 不变，不应反复下载。
车型 picture 和 tracker 的 picture 复用同一安全 profile URL。HA 原生图片请求
只指向经过审核的匿名资源；并未独立实测所有图片正文和 App 车型展示。

## GPS 与 HA 原生模型

保留 coordinates opt-in、默认禁用 tracker、双坐标有限范围验证和现有 unique_id。
采用最低版本已经导出的 `device_tracker.TrackerEntity` / `SourceType`，无需散落
版本判断。标准 GPS tracker 由 Core 提供 Map/Zone/device overview 状态；不添加
自建地图、GPS trail attributes、`loc.acc` 精度推测或坐标转换。

**合成 HA 测试**：实际加载 tracker 与 zone，坐标进入 home 时状态 home，离开时
not_home；值不经 GCJ/WGS 转换；单坐标非法使不可用；撤销位置选项、重载后实体
身份保持且状态不含坐标。此测试证明 Core 集成语义，不能证明实车坐标系或某个
用户的地理围栏精度。真实路线仍只有显式查询 action 在双重授权下返回，不进入
事件/常规实体。诊断不返回坐标。

## 有类型的功能请求图

`demand.py` 区分注册到 coordinator 的实体消费者和内部模型。`async_contexts()`
中的无类型 discovery/audit listener 不代表全部组需求；每个 typed context 按车辆
隔离。disabled entity 不注册消费需求。需求只决定是否参与调度，不改写各组
next_due、freshness、失败退避、jitter、串行锁、reauth、手动刷新或控制回读。

| 消费者 | 周期组 | 上月最近行程回退 |
|---|---|---|
| SOC/range/锁/充电/电源/GPS | status | 否 |
| BMS 测量 | battery | 否 |
| 月汇总 | travel | 否 |
| 最近行程实体 / 启用的 Ride Event | travel | 当前月无最近行程时按原规则 |
| controls button | status，仍须完整安全门禁 | 否 |
| 已启用 SOC estimator 内部模型 | status + battery，即使估算/BMS实体禁用 | 否 |
| image、本地参数、只读 refresh button | profile 基础发现 | 否 |
| 历史查询 action | 单次按需 month/detail、共享有界缓存 | 不制造永久周期需求 |

bootstrap 仍对新车做原有各组初始发现，之后无需求时停止定期 battery/travel。
没有 battery 成功返回前，实体可能无法创建，故继续按既有失败退避发现；成功
但空 inventory 或匿名多包无法形成测量实体时，保留每小时最多一次稀疏发现机会，
按最后尝试时间限频。已有可表示的单包/带身份分包全部被用户禁用、且无 estimator
时，不保留这种 probe。这是避免发现死锁的明确例外，不宣传所有 disabled 场景
都绝对零请求。车辆列表每小时发现保留；不自动遍历详情或所有历史月份。

需求切换在下一次现有 scheduler 周期生效，不另建轮询器；Core 的用户启用/
禁用与 options reload 重新建立注册消费者。激活时已有 freshness/next_due 继续
生效，最迟在既有 due 周期查询，不为启用一个实体强制连续刷新。事件无独立
额外 polling，重新启用仍建立 baseline。缺失车辆不产生功能需求。

diagnostics 只导出固定组名、need/reason 和 last_ride 开关，不导出 context 的车辆
标识。所有请求周期都是本项目策略，不是官方公布的 API rate limit。

## 验收与回退

单元、fake-clock、记录样本和真实 Core fixture 共同验证：需求隔离、bootstrap/
失败恢复、稀疏 BMS 发现、disabled/重新启用、event/estimator 内部依赖、month-only
不额外上月请求、图片缓存/URL移除/签名移除/redirect/隐私日志、GPS Zone/撤销。
完整阶段测试以及最低/稳定 Hassfest/HACS 门槛记录于实施记录、机器验收证据和
对应 prerelease。没有新增实体翻译键、unique_id、ConfigEntry 或 Store schema。
无生产部署、配置/registry/数据库写入、HA 重启或真实车辆控制。

本阶段新增一条 vehicles REST 业务查询和一条匿名图片 HEAD；ninecli 内部鉴权
请求不计入 REST 业务操作数，不声称只发生两个网络包。复制源会话文件的前后
哈希相同；临时子进程回收、会话副本删除。公开 fixture 仅保留选择后的 URL 字段
shape/域名/参数名，身份、路径和签名均为合成值，单独标注而不冒充完整原始响应。

代码回退不会更改 registry/recorder，恢复旧轮询策略和图片处理即可；历史实体仍
保留。尚待：未来新图片来源的证据审核、匿名资源可用性、真实坐标系、物理电池
身份、权限语义与真实动作授权。不能靠增加周期请求解决这些未知量。

参考：[HA Image](https://developers.home-assistant.io/docs/core/entity/image/)、
[GPS tracker](https://developers.home-assistant.io/docs/core/entity/device-tracker/)、
[Coordinator contexts](https://developers.home-assistant.io/docs/integration_fetching_data/)。
