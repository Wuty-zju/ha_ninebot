# v2.x 电池身份与设备模型契约

2026-10-04，Phase 6，基于已发布 2.0.0b5 / main `b13cfe96286c`。
此文补充主要设计第 11/19 节；不覆盖历史审阅报告的固定基线。

## 证据及实现边界

- **已由 fixture 确认**：`tests/fixtures/ninecli/0.1.7/battery-01.json`、
  `battery-02.json` 记录的真实结构各有一个 battery_list 条目，没有 battery_sn/sn。
  外层 battery_count 为字符串 "0"，不能据此否定列表或声称零个物理电池。
  have_bms_cycle_support=false 与 bms_cycle="100" 同时出现；100 不当真实循环数。
- **已由源码确认**：既有 adapter 支持 battery_sn/sn 命名身份、多包稳定键，
  电压 V / 温度 °C 契约沿用此前审计。当前阶段没有增加或升级 backend endpoint。
  这些身份别名的场景测试是合成数据，不是新的实车稳定 SN 证据。
- **已由官方文档确认**：[Device Registry](https://developers.home-assistant.io/docs/device_registry_index/)
  中 child 是物理产品的逻辑组成；可移动物理产品应单独评估普通设备。
  via_device 表示连接方式，不能用来猜电池组成。child API 仍可能调整。
- **待验证**：SN 在同一包跨查询、换装、跨车辆后的稳定性；固定逻辑通道身份；
  可换装与拓扑；电池计数、score、各 electricity、充电功率/保护等字段的完整语义。
  同数量匿名包被换装无法从当前数据识别，不以电压变化伪造换包事件。

## 当前实体身份和取值

`battery.py` 集中定义 observation 选择/签名和安全摘要；`compat.py` 集中检测
ChildDeviceInfo、创建与 lookup 公共 registry API。检测结果仅用于 diagnostics，
**不等价于电池有资格成为 child，也不自动触发 registry 迁移**。

| 数据变化 | vehicle 旧 bms_voltage/batt_temp/bms_cycles | 已发现的带身份分包实体 |
|---|---|---|
| 唯一上报条目（有/无 SN） | 读取唯一当前条目，不把历史绑定其硬件 SN | 若原身份仍明确匹配则可读取 |
| 一包 → 多包 | unknown，不选择第一条或沿用旧 SN 当 primary | 只读明确匹配的身份；匿名多包不新建分包实体 |
| 多包数组重排 | 仍 unknown | 身份匹配不依赖数组位置 |
| 多包 → 一个新包 | 读取当前唯一条目，保留车辆测量语义 | 旧包 unmatched 为 unknown，不继承替代包 |
| 包身份丢失 | 单包仍可提供车辆测量 | 不匹配 slot_N 占位符，不回退数组索引 |
| 无上报条目 | unknown | unknown |
| 支持标志 false/unknown | 循环数 unknown；不发布新循环实体 | 同样需要明确 true，外层 false 优先 |
| 相同明确身份重复 / 非空别名冲突 | protocol failure，既有 freshness/backoff 生效 | 不擅选一行或一别名覆盖历史 |
| 明确 SN 恰为 slot_0 / unidentified | 按明确/匿名命名空间区分 | 不与内部占位符混淆 |

保持现有 vehicle-scoped unique_id，包括旧 primary key 与
`battery_<sha256(identity)[:12]>_<key>`。不改 entity_id、用户名称、禁用选项或设备归属；
不删除历史设备/实体。分包 ID 表示该车辆下相应身份的观察，尚不是跨车电池档案。
同包移车目前保留各车辆独立观察历史，不合并为一生的物理包统计。

SOC estimator 的观察签名改为有序、类型明确的身份集合哈希；解决逗号组合歧义、
明确 serial 与 anonymous 占位符碰撞。旧 `vehicle_soc:` 字符串与当前旧编码匹配时，
升级编码并重建采样 baseline，保留 generation 和累计量，不跨升级区间估算。
真正观察身份改变仍走已有 source_changed/generation 路径，旧估算历史不重写。
哈希只是内部比较编码，不公开原始 SN，也不保证匿名换包可识别。

## 全部已观察 BMS 字段处置

A 正式实体；B 默认禁用实体；C 默认禁用诊断实体；G 未来组件 metadata；
H 有界 runtime raw；I 仅 diagnostics schema；J 暂不使用。下表只声明字段存在的
fixture 证据；未知含义/单位仍待验证。完整原始值不进入公开 diagnostics 或 state。

| path（battery 根） | 含义/证据边界 | 单位 | HA 表达/默认 | DC / SC / category | compatibility |
|---|---|---|---|---|---|
| `$`, `battery_list`, `battery_list[]`, `battery_main`, `charging_protection` | 已观察容器，shape 非物理组件数量证明 | — | H/I，无实体 | — | 有界 raw，旧 HA 相同 |
| battery_count | 字符串；与数组长度不符，枚举/计数语义未知 | 待验证 | H/I；不作为实际 pack count | — | 列表行数单独诊断 |
| battery_find_my_support | bool；不是 bell/buck/engine 权限 | — | H/I | — | 不开放控制 |
| battery_list[].bms_volt | 当前电压测量，沿用已有单位契约 | V | A，启用 | voltage / measurement / — | 旧/分包 ID 保留 |
| battery_list[].bat_temp | 当前温度测量，沿用已有单位契约 | °C | A，启用 | temperature / measurement / — | 旧/分包 ID 保留 |
| battery_list[].bms_cycle | 非负整数，必须明确支持 | 次 | B，禁用 | — / — / diagnostic | unsupported 不把100当真值 |
| battery_list[].electricity | 包上报量；与外层/main 不归并，时效/百分比待证 | 待验证 | H/I | — | 不替代 status SOC |
| battery_list[].score | raw score，不认定 SOH | 待验证 | H/I | — | 不创建健康度实体 |
| battery_main.electricity | main 层独立上报量 | 待验证 | H/I | — | 不归并为 pack/vehicle SOC |
| battery_type | 类型编码，枚举未确认 | — | G/H/I；metadata 暂不启用 | — | 无虚构型号 |
| charging | BMS flag；当前充电实体使用 status charging | — | H/I | — | 不增加语义未确认重复实体 |
| charging_power | 数值，未确认 W/缩放 | 待验证 | C，charging_power_raw 禁用 | — / — / diagnostic | 不用于 Energy Dashboard |
| charging_protection.status | 未确认保护枚举 | — | H/I | — | 不猜 problem binary sensor |
| charging_protection.url | 未审查来源/参数，可能个人信息 | — | J/I；不打开或自动下载 | — | schema only，无 URL 值 |
| electricity | 外层独立上报量 | 待验证 | H/I | — | 不覆盖真实 status dump_energy |
| have_bms_cycle_support | 明确 true/false/unknown | — | H/I；循环门禁 | — | 外层否定优先，缺失不猜 |
| remain_charge_time | BMS 字符串，当前 status 独立提供剩余时间 | 待验证 | H/I | — | 不混成 duration 统计 |

battery_sn/sn 与条目级 have_bms_cycle_support 为既有源码/合成别名契约，不在真实
两个 battery fixture 的已观察字段中；不把它们加入“实测字段”计数。
新增未知业务字段仍由 Raw layer 保留并输出安全 shape，不自动创建实体。
`battery_model` diagnostics 只输出条目数、有身份/匿名计数、车辆单包测量是否明确、
当前 vehicle assignment 和 unverified component model，不含 serial/hash/测量值。

## Child/普通 Device 后续启用条件及降级

本版 **不注册新的电池 device/child**，2026.1 与 2026.9+ 都继续 vehicle assignment。
因此本版不存在 child 到普通 device 的升级/降级转换、registry 迁移或备份重写。
集中检测缺任一公共 API 都返回 false；旧 Core import 不引用不存在的 Child 类型。
最低版本不提高，功能不能只凭 HA 版本号或 SN 字段存在而启用。

后续必须先取得可审查的脱敏重复观察，证明以下之一：

1. 稳定固定逻辑通道/不可独立迁移的组成：再实现 child 注册、parent 同 entry/subentry
   验证及旧版本 vehicle fallback；不得使用 serial/manufacturer 等独立硬件属性。
2. 可独立换装的真实电池硬件：评审普通设备、跨车身份和云连接路径；不可改 parent
   的 child 不适用。既有车辆测量不能直接变成物理包历史。

实际迁移前用一次性 registry/备份演练 entity_id/unique_id、用户名称/area/disabled、
parent/entry 所有权、重复发现、重新装配和旧 Core 恢复。新版 child 不能直接
promote/reparent；不能承诺旧 Core 无损读取新版 child registry，不能靠删设备修复。
此门槛未满足时维持当前模型，明确后置，而非为阶段数量强行创建设备。

## 阶段验证和请求预算

Tier A 验证单→多→单、身份丢失/冲突、重排/替换、namespace 与签名碰撞；
Tier B 实际 HA registry/state 生命周期验证旧 ID、用户名称、默认禁用循环、
设备归属、卸载/重载无重复及生产相同 recorded battery shape。
compat 的完整/缺失/部分 API 在离线替身及 CI 最低/稳定 Core 下校验。
升级估算签名验证 generation/累计量保留且不产生跨升级能量。

新增云查询 0、真实控制 0、生产 HA 写入 0；只修改 Git 工作区与一次性测试目录。
阶段完整 Ruff/format/mypy/pytest、最低/稳定 CI、Hassfest/HACS 及准确 release commit
证据见 `evidence/v2x-b6-validation.json` 和预发布 notes。

未交付项：真实 stable pack identity、物理/逻辑组成证据、child/普通电池设备实际迁移
及 child registry 降级备份演练。它们不是通过 mock 就能证明的硬件结论；待证据后
单独实施，不妨碍 Phase 7/8。
