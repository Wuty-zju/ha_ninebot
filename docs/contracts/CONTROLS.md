# 控制、权限与状态回读

现行规则来自b15云端最终鉴权、b14归属守卫、b16回读协调。旧Phase7的UNKNOWN全部拒绝策略已被覆盖。
此文记录已实现行为，不构成新的实车动作测试授权。

## 发送条件

统一control_decision用于Button availability、排队前及取得mutex后复查：
认证/runtime有效；controls显式启用且车辆在allowlist；车辆存在、profile/status fresh、最近查询无错误；
固定backend实现该动作；没有明确DENIED、重复/歧义能力记录。失败不发送，状态变化排队后仍能阻止。

UNKNOWN始终未知，不伪造ALLOWED；本地满足条件允许将固定命令交云端最终鉴权。
capability.allowed（审阅证据）与decision.allowed（本地可发送）是两种事实。
ready表示“可发送命令”，不表示权限已证实或动作已完成。未知动作、任意host/endpoint不可配置。

| 命令 | HA表示 | 语义边界 |
|---|---|---|
| bell | Button | 发声命令，接口接受不证明实际鸣响 |
| buck | Button | 原命令名称对应操作，不据接口接受宣称座桶已打开 |
| engine-start/engine-stop | Button | 不等同Lock.lock/unlock；不乐观修改锁/电源状态 |
| refresh | 只读Button | 不受controls opt-in影响，遵循有界查询/生命周期 |

## 一次发送与回读

| 结果 | 行为/返回 |
|---|---|
| 接受且status成功 | 正常返回；accepted/refreshed，物理结果未验证 |
| 接受但回读失败/跳过/车消失 | control_readback_failed；不重发POST，不把旧缓存当回读 |
| 非认证错误/超时 | 有效runtime可协调一次status；仍control_uncertain，原命令错误不被GET覆盖 |
| 明确认证失败 | reauth，跳过追加GET/POST |
| 排队/调用/回读取消 | 按阶段取消本调用，不声称动作物理撤销，不自动重试 |

可合并有效正在运行的同车读取；已完成的命令前manual任务不能作为命令后证据。
per-group freshness/backoff、partial failure保留，不因控制而设置长期高频轮询。

## 诊断与证据

control_results只内存，全局64条、每车每动作最近调用；卸载清空，旧调用晚完成不覆盖新槽位。
输出固定outcome/ErrorKind、UTC尝试/结束时刻、readback类别及physical_outcome_verified=false。
不输出SN索引、上游raw、位置或异常原文，也不将accepted缓存永久变成权限或新完成Event。

既有b15单轮记录四命令HTTP200/ok=true/空data，回读选定状态未变化；有接受证据，无物理观察者。
b14 mock accepted标记不是实际raw字段；控制代理仅ok/data，12路径与136遥测路径分计。
[旧实测记录](../archive/contracts/v2x-云端鉴权控制与实测契约.md)和[对应证据](../evidence/v2x-b15-live-controls.json)仅供溯源，不能据此重复控制测试。

维护：相关capabilities、control_entities/results、coordinator、native_encrypted_controls离线回归。
能力新枚举、未知permission及真实物理效果须fixture/专门证据；本地HA在开发期间只读。
