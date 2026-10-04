# v2.x 权限门禁与能力证据契约

> b15后续覆盖：用户明确授权改变发送门禁并测试真实动作。最新策略见
> [云端鉴权控制与实测契约](v2x-云端鉴权控制与实测契约.md)。本文保留b7历史
> 设计；当前UNKNOWN不伪造ALLOWED，显式配置后的发送由云端最终鉴权，已知
> 拒绝/本地条件仍阻止。按钮表达以b12、执行政策以b15为准。

2026-10-04，Phase 7；基于 2.0.0b6 的状态/身份边界，延续 b1 引入的 fail-closed
原则。没有新增云查询/实际控制，也不宣称已实现官方权限位图解码。

## 必须区分的三个层次

1. **源码确认的 transport support**：固定 ninecli==0.1.7 的 backend 实现
   bell、buck、engine/start、engine/stop endpoint。存在 HTTP/CLI 命令仅表示软件
   可发送请求，不证明某车硬件支持或该账号拥有执行权限。
2. **车型支持及用户权限**：VehicleCapabilities 对每个动作分别保存
   unknown/allowed/denied。缺失、null、未经解释的整数/对象/bitmask 不转换成 allowed。
3. **已验证动作语义**：semantics_verified 与非空的内部 reviewed contract label
   必须同时成立。label 是证据定位标记，不是证据本身，不来自任意 raw 字段/用户选项。
   diagnostics 仅输出 evidence_available 布尔，不导出 label（可能包含个人信息）。

**当前生产 parser 不生成 verified control capability**。既有 recorded fields
不足以确认权限协议，用户勾选 controls 和 allowlist 仍不会使硬件按钮可用。
只有后续审阅后的明确 parser contract 才可提供支持/权限/语义证据；合成测试里的
mock-contract 不是生产解锁方式。也不因 vehicle owner/is_common_user 推导权限。

## 原始字段评估（真实记录存在不等于语义确认）

| endpoint / path | 已有证据 | 当前处置 | 开放前还需什么 |
|---|---|---|---|
| vehicles / common_user_permissions | 真实 null | H/I；permission unknown | 非空 schema、官方/可靠源码枚举、账号/动作范围 |
| vehicles / common_user_version、latest_support、support | 真实 null | H/I；support unknown | 明确字段契约，不能猜 bitmask |
| vehicles / is_common_user、businessType、vehicle_type | 真实整数，枚举未证 | H/I/F；不代表控制授权 | 车型/角色定义及每动作能力证据 |
| status / permissions | 真实 null | H/I；所有动作 permission unknown | 同车/账号非空返回与协议语义交叉验证 |
| status / is_common_user | 真实整数 | H/I；不推导 owner 或 allowed | 共享角色及能力/授权具体定义 |
| status / loc.acc | 真实整数，acc 含义未证 | H/I；不当 ignition/accuracy/control support | 来源实现/模型/单位验证 |
| status / loc.lock、lock_status、pwr | 锁/电源读状态已有 parser 契约 | 已有 state 表达；非授权标志 | 不从观察值推导控制含义 |
| status / barrel_lock_status | 存在但枚举/反馈未证 | H/I；不增加桶锁 LockEntity | 桶锁状态/动作回读及车型差异 |
| battery / battery_find_my_support | bool，具体能力未证 | H/I；不是 bell/buck/engine 权限 | 单独功能语义/协议证据 |
| 未来 capabilities / ownership / connectivity / report timestamps | 部分仅候选，不冒充实测 | 新字段有界 raw/schema，逐项评审 | 真实 shape、时间/角色语义；不得用查询时间冒充上报时间 |

A–J 分类、全部已观察字段仍见主设计与机器字段清单；此表专门记录权限相关决策。
Raw layer 保留允许的业务信息；diagnostics 只有安全字段名/类型，不输出权限原始对象、
账号、owner、phone、token、位置、MAC 或任意 evidence 文本。

## 单一决策与执行

`capabilities.decide_control` 返回 immutable ControlDecision；coordinator.control_decision
补充运行条件。controls_enabled、按钮 availability、排队前/取锁后执行检查以及
诊断都使用同一决策。unknown_action、重复同动作 capability 均拒绝；重复条目
不擅选第一条，即使两个都声称 allowed。

必须全部满足：

- runtime 未关闭且 authenticated；
- 用户 enable_controls **明确 true**，车辆在 allowlist；
- 车辆 present，profile/status 未过期，且最近对应查询没有 error；
- backend 声明实现该 control action；
- 上游 support=allowed、permission=allowed；
- semantics_verified **明确 true**，有非空内部证据 label。

诊断 `control_policy[action]` 输出 allowed、blockers、support、permission、
semantics_verified、evidence_available、matching_records。blockers 采用固定白名单码：
运行停止、需认证、缺启用/白名单、不存在、过期、查询失败、transport不支持、
unknown_action、ambiguous_capability、support/permission unknown 或 denied、
semantics_unverified、evidence_missing。没有 SN 或原始错误文本；可同时看到多个原因。
`backend_support` 单独列软件 endpoint/action 契约，不叫车辆 capability。
保留旧 diagnostics.controls 的 boolean 以避免诊断消费者突然失去字段。

等待队列中权限/freshness 可改变，所以进入 mutex 后再检查。真实控制不得自动
重试、不得 optimistic 更新 state；发起一次后走既有 status readback。
取消/卸载/auth failure/uncertain outcome 沿用原有策略与精简 mock 测试。
普通 timeout/5xx 不生成 Repair；认证用现有 HA reauth。未知权限本身不是故障，
不创建永远无法由用户修复的 Repair，也不提示用户删 session/registry 来解除门禁。

## HA 表达和兼容

bell/buck 保持原 Button key/ID、默认禁用；旧 Lock 保留读状态和历史，
lock/unlock 调用继续返回 engine_lock_unverified。
engine-start/stop 未证明等价锁/解锁，不增加具有误导语义的 Lock API，也不增加
永远不可用的 engine buttons 来宣传未确认能力。未来可在确认证据后单独设计
默认禁用的动作，但实车测试须用户授权具体车、动作、次数。

options 中英说明明确“启用本身不开放硬件控制”；不增加连接/密码 data 字段，
不改变 config flow、reauth、reconfigure 或存储 schema。纯 domain policy 无新 HA API，
最低 HA2026.1 与 stable 共用；没有 entity migration、production data writes。

## 验收与未来验证

离线验证 tri-state、缺证据/空白label、重复/未知动作、transport缺实现、用户启用/
白名单、present/fresh/error/auth/stop、排队后权限变化；执行与diagnostics结果一致。
所有允许的执行场景只用fake client/backend；不会调用真实 bell/buck/engine。
诊断测试检查恶意 evidence label 不泄露，现有全诊断隐私与 HA availability 测试保留。
精确版本/阶段CI证据见 `evidence/v2x-b7-validation.json` 与 release notes。

仍待验证：非空权限 schema、支持位图/车型范围、共享用户权限、各动作实际硬件语义和
返回/回读完成判据。真实控制验证不属于本任务当前授权；不通过追加只读 polling
猜控制是否成功。独立可实施的 Image/GPS/依赖轮询继续推进。
