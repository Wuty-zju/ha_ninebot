# Backend、认证与车辆发现

现行基线：b24 / ninecli==0.1.7 / HA>=2026.1.0；本页是本主题的唯一现行契约。
改动理由按阶段写[PROGRESS](../development/PROGRESS.md)，原验证记录留[历史目录](../agent/catalog.json)。

## 受管理传输与账户隔离

HA → backend abstraction → NinecliBackend/client → authenticated loopback serve → 九号云。
随机Bearer、loopback-only、密码在本机HTTP body、禁止任意上游host、精确依赖pin均保留。
serve由runtime管理启动/退出/超时/卸载；不退回每个查询新起subprocess。
车辆发现是受控例外：固定native JSON命令准备business-line缓存；其余查询走serve。
REST vehicles正常不表示native缓存已完整初始化，缓存不可由HA根据业务类型猜造。

每ConfigEntry独立私有session/config目录；这是same-user信任边界，不抵御同权限/高权限进程。
ConfigEntry.data放连接必需信息，options放轮询、位置、控制allowlist、调试和额定参数功能。
密码不长期写ConfigEntry，也不进argv、日志或diagnostics。没有runtime从仓库外下载二进制的脚本。

password与两步SMS flow均已实现；候选session先验证身份/查重，再事务提交。
reauth/reconfigure保持同账户；取消、失败、重复账户、回滚均清理候选目录。
rollback不能安全卸载时保留journal/backup，通过Repair请求用户处理，不假称提交成功。
真实SMS流程未验证；不要从mock通过推导短信发送或真实验证码已成功。

## native发现、partial结果与归属

| 结果 | 缓存与车辆处理 | 控制边界 |
|---|---|---|
| 完整合法列表 | 通过同一profile parser后提交native路由 | 每车独立记录实际观察时间 |
| partial正向列表 | 合并已知路由；更新实际观察车辆 | 缺席不刷新其profile时间，也不确认解绑 |
| 空/业务失败/非法/超限/取消 | 有界读取stdout/stderr、回收child并恢复缓存 | 保留可用token刷新，不以exit0冒充发现成功 |
| status明确返回sn | raw/telemetry更新前检查匹配 | 错车拒绝；缺失/null不能宣称身份已核验 |

发现质量与每车profile freshness分别诊断；一个车辆成功不为其它车辆续期。
未知业务类型不猜owner/权限；车辆nickname不用作跨账户身份。

## endpoint与职责

| 能力 | 当前路径/命令 | 使用 |
|---|---|---|
| vehicles | 固定native `ninecli --json vehicles` | profile及native路由缓存 |
| status | `/vehicles/{sn}/status` | 车辆当前状态 |
| battery | `/vehicles/{sn}/battery` | BMS与包身份观察 |
| travel month | `/vehicles/{sn}/travel?month={month}` | 月聚合/行程列表 |
| travel detail | `/vehicles/{sn}/travel/{detail_id}` | 按需详情，不常规轮询 |
| password/SMS/token | client/session的固定认证协议 | flow候选验证、刷新、reauth |
| controls | 固定四动作 | [控制契约](CONTROLS.md)，不自动调用 |

异常按固定ErrorKind分类，不输出上游敏感错误原文；认证错误转reauth，暂时失败按组退避。
多ConfigEntry隔离、多车独立freshness，单账户backend串行；与[需求调度](VEHICLES_ENTITIES.md#刷新与请求依赖)共同控制云请求。

## 维护与验收

改认证/缓存须检查session、config_flow、native_vehicles/native_cache_routing、client相关tests；fixture/fake server优先。
升级ninecli先比较JSON/endpoint、补fixture与针对性兼容，再改精确pin；不使用>=或自动latest。
[旧b11契约](../archive/contracts/v2x-ninecli输入输出与车辆缓存契约.md)与[旧b17发现契约](../archive/contracts/v2x-不完整车辆发现与缓存恢复契约.md)保留取证原因，不再各自追加新版本章节。
