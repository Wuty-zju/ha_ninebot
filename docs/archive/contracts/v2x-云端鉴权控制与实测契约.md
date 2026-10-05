# v2.x 云端鉴权控制与实测契约

> 历史归档：保持原版本/证据范围，不继续追加。现行行为先读[CURRENT_STATE](../../agent/CURRENT_STATE.md)和对应主题contract，不能直接执行本文旧goal或“未来”清单。

2026-10-04，b15；基线 main `4d5efe6a9c8f4df820415fae3f5aa7da81e4f8b0`。
用户恢复目标时明确授权改变软件门禁并测试真实车辆动作，覆盖此前“未知权限
必须拒绝发送”的策略。生产 HA 本地配置、安装代码、registry、数据库与运行
进程继续只读。本阶段是独立门禁修复，不把此前未确认量猜成正式实体。

## 1. 发送条件与权限事实分开

已由源码确认：`coordinator.control_decision` 继续统一驱动 Button availability、
诊断及进入队列前/获得 mutex 后的执行检查。以下条件全部成立才发送一次：

- 当前 runtime 未关闭且认证可用；
- 用户明确启用控制，目标车辆在允许列表；
- 车辆仍在账户中，profile/status 在有效期内，最近对应查询没有错误；
- 固定 backend 声明实现该动作，仅 bell、buck、engine/start、engine/stop；
- 没有明确的 support/permission DENIED，也没有重复、歧义的能力记录。

`UNKNOWN` 仍为 UNKNOWN，不生成虚假的 ALLOWED、semantics_verified 或证据。
`ControlCapability.allowed`/`VehicleCapabilities.allows` 仅表示完整审阅能力证据；
`ControlDecision.allowed` 表示本地可以发送，二者不能混用。诊断明确标注
`dispatch_policy=cloud_authorization`，原有三态、语义/证据布尔仍输出真实值。
空 evidence 或未确认物理语义不再阻止名称直接对应 ninecli 命令的 Button，
也不会把 engine 命令映射成 Lock。未知动作、明确拒绝、认证/数据/账户问题
继续拒绝；任何 upstream timeout/错误都不自动重发。

状态翻译 `ready` 改为“可发送命令”/“Ready to send”，而非“控制成功”或已获
云端权限。诊断最多表示至少一个命令可发送，每个动作属性独立显示；已知某
动作拒绝不会误禁其它动作。options/错误说明完整中英适配，不添加实验名称、
额外不必要开关或破坏 identity。仍保留用户主动禁用的按钮。

## 2. 已授权实测及返回

已由真实请求确认 R：生产会话复制到700/600权限临时目录，用未修改的固定
ninecli 0.1.7 和认证 loopback client；真实账号没有放入 argv、Git 或日志。
最初一次车辆发现与两车预读发现两车均 pwr=true/locked=true/charging=false。
随后从只读 HA registry 中精确定位当前 entry 的车辆，不重复车辆发现，只
选择一辆锁定、非充电车辆。pwr 不被当成已经核实的 ignition/动作完成标志。

| 命令 | 实际尝试 | loopback HTTP | ok | 业务 data | 回读 | 物理效果 |
|---|---:|---:|---|---|---|---|
| bell | 1 | 200 | true | 空对象 | 成功，选定状态未变化 | 待验证 |
| buck | 1 | 200 | true | 空对象 | 成功，选定状态未变化 | 待验证 |
| engine/start | 1 | 200 | true | 空对象 | 成功，选定状态未变化 | 待验证 |
| engine/stop | 1 | 200 | true | 空对象 | 成功，选定状态未变化 | 待验证 |

每个命令一次，失败不会重试；启动/关闭是分别授权的两条命令，不是对启动的
重发。启动前回读仍须锁定且不充电，否则停止该测试序列；关闭为单次独立结束
命令。没有物理观察者，不能宣称车辆鸣响、座桶打开或电门完成切换，也不能
宣称已恢复初始物理状态。四次选定回读均 pwr=true、locked=true、charging=false；
立即回读不变化不能据此判定动作无效或成功，不用重复控制/循环查询追求结果。

整个研究阶段12次逻辑操作：1次CLI车辆发现、3次status预读、4次控制、4次
status回读；native内部认证/fanout不计入这个逻辑数量。中间本地registry读取
适配 owner config_entry_id，不产生额外云查询。临时进程回收和目录删除，生产
会话文件前后摘要一致。HA自行正常写日志不被误称为本次开发写入。

脱敏元数据见 [实测证据](../../evidence/v2x-b15-live-controls.json)。没有身份、token、
精确位置、行程轨迹或错误原文。四命令共有的选定代理形状保存在
`tests/fixtures/ninecli/0.1.7/control-accepted.json`，仅包含审核过的 ok/data；
它不是直接云端密文或原始网络响应的逐字节副本。真实 data 没有任何成员，
不能把 b14 合成 `data.accepted` 误当作真实字段。

## 3. 命令字段到 HA 表达

四条命令分别有三个选定代理路径，共12条；与136条业务 telemetry 路径分开
计数，见当前机器清单 `observed_command_responses`。不改写旧97路径历史清单。

| 端点 | 路径 | 类型/单位 | 证据 | 分类 | HA用途 / 默认 / class / compatibility |
|---|---|---|---|---|---|
| 四条控制分别记录 | $ | dict / 无 | R，选定proxy shape | H/I | client结构检查；无实体/单位/state class，旧HA同实现 |
| 四条控制分别记录 | $.ok | bool / 无 | R=true | H | 验证命令调用被代理接受；Button调用返回，无乐观车辆state |
| 四条控制分别记录 | $.data | dict / 无 | R=空对象 | H/J | 正常消费空结果，没有可创建实体的物理字段，不存大raw属性 |

Button 本身由用户启用 controls+逐车allowlist 后默认显示；USER禁用保留。
命令返回不是 EventEntity 的物理完成事件，也不是电源/锁sensor的新状态。
未来若发现可靠结果字段，先补 fixture/语义证据和此表，再决定 response/event
表达；不能用某次接受缓存永久授权后续所有动作或其它车辆。

## 4. 验证与未完成边界

- 单元：UNKNOWN 不伪造权限、DENIED 保留、重复/未知动作拒绝、既有本地条件。
- HA流程：真实 parser 空能力时有授权 Button 可用；模拟 press 每命令一次与回读；
  已知拒绝在排队中更新仍阻止；USER禁用、认证、freshness、错误边界保留。
- recorded replay：真实空成功 data 四路HTTP，无需访问云端；命令字段独立分类。
- 协议：原有 b14 加密接受/拒绝模拟保留，合成字段继续明确标注。
- 发布：必要一次完整本地验收，以及精确 merged main 的三组Checks/Hassfest/HACS。

生产仍为 b8，本轮没有安装或重启。新源码和隔离真实命令通过不等于生产界面
已经修复。没有验证另一辆车、共享账号、全部车型、任意权限位图或物理效果；
能量/轨迹单位、坐标系、稳定pack身份与历史完整性保持原有待验证项。
