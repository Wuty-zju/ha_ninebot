# v2.x ninecli 输入输出与车辆缓存契约

> b17更正：原生退出0不保证完整发现。新实现有界读取stderr、保留部分正向
> 结果、失败恢复cache、按车记录身份时效；原b11的stderr=DEVNULL保留为历史实现。
> 当前契约见[不完整发现与缓存恢复](v2x-不完整车辆发现与缓存恢复契约.md)。

调研日期：2026-10-04。起始运行基线 b10；b11 实现修复及验收见第 5 节，研究与生产安装状态分别记录。

## 1. 新确认的缓存缺口

**已由真实只读请求确认 R：** 本机生产集成 b8 的会话目录没有 `vehicles.json`。隔离副本的 REST `/vehicles` 能成功返回两辆车，status 也能成功；随后 battery 返回 HTTP 400、`ok=false`、`error.code=no_cache`。错误属于 native proxy 的车辆路由缓存前置检查，不能解释为账号错误、无 BMS 或上游关闭 API。对未准备缓存的请求不继续循环重试。

**已由 native 二进制静态确认 S：** 固定 ninecli 0.1.7 的 `config.LoadVehicleCache`/`SaveVehicleCache` 使用 `vehicles.json`。Go 反射类型信息显示根对象 `vehicles` 数组；每行仅有 `wnumber`、`vehicle_name`、`device_name`、`business_line` 四个 JSON 字段。`cmd.vehicleCacheFromLists` 分别处理 ebike/motor 来源；不能凭型号、昵称或一个未经核实的 `businessType` 数字猜业务线路。REST `handleVehicles` 没有直接保存缓存的调用。本地静态读取不是完整 Go 源码审计。

**已由真实只读请求确认 R：** 新的私有会话副本只执行原生 `--json vehicles`，生成 cache，所选两车业务线均为 ebike。随后同目录新启动、随机 Bearer 保护的 serve 查询两车 battery 及第二辆 status 全部成功。每车只有一包；当前 BMS 数值路径为 `battery_list[].bms_volt/bat_temp/bms_cycle/electricity/score`。两车 status 的 `permissions` 仍是 null。原始数据和会话只保存于 Git 外的私有研究目录；公开证据不含值、序列号、账号、位置、token。

研究中总计五次本地 battery 路由调用：三次 `no_cache` 失败；最终缓存流程中的两车 battery 查询成功。应以私有请求记录区分次数与是否到达上游，不能把本地路由调用数冒称九号云精确请求数。原始失败流程不覆盖后续成功证据。生产文件哈希前后相同，production writes=0、controls=0、images downloaded=0。

## 2. b11 修复契约

- 将车辆发现与 native cache 生成视为同一操作，避免额外周期重复列表查询。
- 优先使用固定版本原生 `--json vehicles` 的真实缓存写入契约，不能手写猜测业务线的缓存。
- 保持密码仅通过随机 Bearer loopback HTTP body 登录；原生车辆列表命令不含密码、token或host override argv。
- 与 REST I/O 共用有界队列、串行锁、超时、输出大小限制和取消回收；不允许通过 root 脚本引入运行依赖。
- CLI/serve 共用私有会话目录。必须明确 native server 是否在启动时读取 cache；更新 cache 后需要保证后续 REST 使用新版本，不沿用旧缓存。
- 子进程 stderr 不进入日志/异常。CLI 非零退出不靠任意字符串猜鉴权；认证判定需使用可验证的契约。
- 离线测试覆盖空账号、两业务线、cache 更新/移除、超时/取消/超大输出/卸载，以及无 cache 的原生 REST 拒绝；最后仅在存在新的证据缺口时执行只读 smoke。

## 3. ninecli 能力的 HA 表达

| 能力 | 已核对的输入/输出边界 | HA 表达与剩余问题 |
|---|---|---|
| password login | CLI 参数；REST account/password body；两段 token | 当前 Config Flow/reauth/reconfigure；密码继续不进入 argv/ConfigEntry |
| SMS login | REST send-code account、consume account/code；CLI login-code | 可后续增加登录方式；本轮未发送短信，不假称已实现 |
| refresh / whoami | REST auth/refresh、whoami；native token管理 | 会话/认证生命周期；不创建展示账号或 token 的实体 |
| vehicles | CLI JSON 合并列表 + cache；REST 合并列表不能替代 cache 准备 | 车辆设备、型号图片、动态身份、缓存准备；本阶段重点 |
| status | SN；电量/续航/充电/锁/坐标/permissions 等 | 当前状态实体、tracker；permissions=null 不冒充允许 |
| battery | SN + native vehicle cache + business_line | BMS 测量；需修复缓存后才能正确动态创建，循环支持检查和未知单位策略保持 |
| travel / detail | SN + month / travel ID | 最新行程实体、历史 response Actions、小事件；轨迹不进入 recorder |
| bell / buck | 原生 bell/open_buck endpoint 和明确 operation | Button；不能因 CLI 有命令而声称实车执行成功；控制策略决定待用户澄清 |
| engine-start / stop | 原生 engine_start/engine_stop command | 应作为明确远程启动/关闭 Button 或 action；不映射 Lock，不用“实验”产品命名 |
| serve / MCP / JSON | 服务与输出形式 | serve 是传输；MCP 不额外引入 HA；JSON 是原始层，不直接复制为 state |
| completion / help | 终端辅助 | 开发工具，不强行创建 HA 实体 |

13 个主要子命令 help 已在无会话隔离配置下执行并保存，不触发登录、短信或云查询。PyPI 最新仍 0.1.7；集成精确 pin 不变。公开原始字段清单与行程/BMS专项契约仍为字段语义依据，新发现字段需增量评估而非自动建实体。

## 4. 未完成与安全边界

缓存修复已进入 b11 工作区并完成隔离测试，但未升级生产插件，不能声称生产电池或控制已经恢复。控制 policy 仍需解决旧设计的未知权限拒绝与用户要求正常操作之间的冲突；不能伪造权限 parser。真实控制须具体动作授权，本轮为零。

未知字段优先进入有界 RawStore 和白名单 schema diagnostics；未来 debug 表达应仅暴露安全的小型摘要或查询响应，不能在实体 attributes 放全量 JSON、账号、任意未知字符串或 GPS 轨迹。

来源：[ninecli PyPI](https://pypi.org/project/ninecli/)、固定版本真实 help / native 反射与调用分析、隔离会话只读请求、当前 client/backend 源码；[HA ButtonEntity](https://developers.home-assistant.io/docs/core/entity/button/)。

## 5. b11 实现与验收

`NinecliClient.async_list_vehicles()` 使用原生 `--json vehicles` 生成 cache 并返回
原始车辆列表，替代 REST 发现操作，不在正常成功路径额外查询 whoami/list。
原生命令运行前停止 serve，与 REST 共用 `_operation()`（队列8、锁、closed检查、
取消回收），stdout 逐块读取上限1MiB、超时30秒、stderr/stdin均DEVNULL。
原生命令与 serve 使用同一私有配置、清除环境中的 NINEBOT_* override。
CLI 更新 token/cache 后停止，后续 REST 用新缓存/新token懒启动；不能并发写会话。

CLI 非零退出没有已核实的结构化错误输出；此时通过 REST whoami 获得明确
认证证据，typed AUTH 才触发 reauth，认证正常仍报 SERVICE。不上报 stderr、
exit字符串或原始错误内容，不自动重试列表，更不会重试控制。登录密码仍只经
loopback Bearer body；没有更换依赖、最低 HA、ConfigEntry 或实体身份。
raw schema diagnostics 的 vehicles source_endpoint 来源模板改为真实原生命令，避免
错误声称新列表来源于 REST `/vehicles`；其余 endpoint 保留原来源模板。

| 验证 | 结果与边界 |
|---|---|
| 纯离线回归 | 353 passed，分支覆盖97.77%，Ruff/format/mypy通过；包含管道上限/JSON/串行队列/取消/close/超时/错误隐私及CLI→REST认证判定 |
| 原生二进制路由 | 全部上游host显式loopback，合成token；无cache时上游请求为0，有cache时只到本机 `/v6/vehicle/battery-info`；stub故意返回服务错误，不假装真实BMS返回 |
| 新版client只读RC | 隔离生产会话副本；原生vehicles一次、两车各battery一次，均成功解析单包电压/温度，循环支持false不发布计数；cache 0600 |
| 生产边界 | 源会话文件哈希前后相同；production writes=0、real controls=0、images downloaded=0；未部署、重启或直接清理生产registry |
| 发布 | 必须以准确main SHA的三环境 Checks、Hassfest、HACS成功和 prerelease tag核实；本页不冒充远程检查结果 |

下阶段继续原生控制入口、权限策略和安全 debug 表达。缓存成功只证明路由
前置条件被满足，不等于控制权限允许或物理动作完成。

## 6. b12：REST 控制与缓存的区别

新增固定 binary 回归覆盖cache存在/缺失两种情况、battery加四种控制。
**已由原生模拟上游确认 S/F：** battery无cache返回no_cache且不上游；
四种REST控制在无cache时仍向ebike控制路径发送一次POST。因此第2节若
将CLI的缓存要求推及全部REST控制是不准确的：CLI缓存路由、REST BMS路由、
REST控制是不同实现路径。b11的client说明已修正；b11发布说明另补事实校正，
不重写tag或声称旧版已完成这些控制回归。

| HA命令 | REST路径 | 本机模拟器观察的native上游路径 |
|---|---|---|
| bell | POST /vehicles/{sn}/bell | POST /devices/control/bell |
| buck | POST /vehicles/{sn}/buck | POST /devices/control/open_buck |
| engine/start | POST /vehicles/{sn}/engine/start | POST /devices/control/engine_start |
| engine/stop | POST /vehicles/{sn}/engine/stop | POST /devices/control/engine_stop |

全部host显式改到测试专用loopback，上游stub主动返回503业务错误；请求
加密外层key观察为d/h/k/p/t。没有保存真实token/密文，没有真实云调用或车辆
动作。它确认native编码/路由路径，不证明内部RSA cmd解密内容、真实权限、
其他业务线/车型或物理动作完成。当前集成仍需scope/freshness/权限门禁。

b12补回正常命名的远程启动/关闭按钮，同时增加小型本地控制状态枚举实体。
启用按钮注册与允许动作执行明确分离，未知capability仍不允许。控制策略
变更尚待澄清，不能因为按钮已创建或模拟路由通过就宣称灰色问题全部解决。
