# v2.x 不完整车辆发现与缓存恢复契约

2026-10-04，b17；起始 main 为 b16 `e8a2b846f3fa909d2442f748be2df5ee9059ae2d`。
本阶段解决原生 CLI 的“成功退出不等于完整发现”，不修改控制权限或实体身份。

b18补充：合法JSON列表仍需通过已有profiles身份解析契约，才允许提交原生
路由cache；缺少身份等协议错误恢复旧cache。本补充只做离线针对性回归。

## 1. 新证据与生产现状

- **已由本机只读文件确认 R**：用户已安装 b15，启用 controls，两辆车均在
  allowlist；90 个登记实体、49 启用、41 integration-disabled，无旧 Lock。
  普通续航、最近行程、图片和四类命令均登记启用；隐藏项主要是诊断/估算/参数。
  登记不能证明运行可用。有限日志只有自定义集成 loader Warning，无对应错误。
- **已由只读 Recorder 查询确认 R**：多类当前状态、最近行程与命令最后记录为
  unavailable。查询只取本集成已有实体的最后一行，数据库 mode=ro/query_only，
  不读取位置、属性、历史轨迹或账号。该结果是最近持久化状态，不冒充 runtime。
- **已由本机只读文件确认 R**：native cache 的 vehicles 为 null，改写时间
  `2026-10-04T10:44:28.364071Z`；多类实体随后约60ms变为 unavailable。
  时间相关性支持发现缺陷解释，但没有保存当时 stderr，不宣布生产根因已确证。
- **已由原生离线模拟确认 S/F**：所有云 host 改到测试 loopback，两个车辆列表
  返回业务503；固定0.1.7原生 CLI 请求两次 `/vehicle/binding/my-vehicle`，
  退出码0、stdout为`[]`、stderr非空，并写入空native cache。此前client只看
  stdout/退出码，coordinator会将此前车辆标为 absent。这是确定的源代码缺陷。
- **已由隔离真实只读请求确认 R**：whoami返回502/upstream_error，属于SERVICE，
  不是坏密码证据。同轮一次原生发现返回两车且stderr非空。不能因为有stderr
  就拒绝所有正向结果，也不能按任意stderr字符串推断AUTH或业务线路。
- **已由新版client隔离RC确认 R**：一次发现返回两车、complete=false，随后两车
  status与BMS各一次成功，均能解析SOC存在、电压/温度存在和单包数量。生产会话
  哈希前后相同，临时副本删除、子进程回收；没有真实控制或生产写入。

本轮共7个显式只读逻辑操作：whoami1、原生发现基线1、RC发现1、status2、BMS2。
native可能有内部业务线fanout或认证刷新，7不是上游精确HTTP请求数。
只保存白名单元数据，不保存stderr、账号/SN、token、GPS或原始私有payload。

## 2. CLI 成功与发现完整性分开

| 原生结果 | 处理 | 身份与缓存 |
|---|---|---|
| 退出0、合法非空列表、stderr为空 | 完整成功 | 更新正向车辆；允许确认未返回车辆缺席；使用新native cache |
| 退出0、合法空列表、stderr为空 | 完整空账号 | 允许确认缺席；不把真实解绑当网络错误 |
| 退出0、非空列表、stderr非空 | 不完整正向结果 | 更新已返回车辆；未返回车辆保留但不延长身份时间；合并已知native路由 |
| 退出0、空列表、stderr非空 | SERVICE | 不推断解绑；恢复此前native cache；沿用退避 |
| 非零退出 | 原有whoami显式认证判定 | 不解析任意错误原文；失败恢复cache，不回滚刷新后的token |
| 非列表、超限、超时、取消 | typed错误/取消 | 回收child后恢复cache；不提交伪成功 |

stderr在两个并发管道中有界读取，每管道最多1MiB，总操作仍30秒；不进入日志、
异常或公开诊断。非空仅表示完整性未得到保证，不声称每条都是业务错误。
`BackendResult.vehicles_complete`是本地transport元数据，不是新增九号raw字段，
更不表示travel完整历史。现有136业务路径和12个选定命令代理路径仍分开计数。

## 3. 路由缓存的保护

原生命令仍负责生成business_line，不依据车型、businessType、昵称或SN猜测。
运行前在串行锁下有界读取此前cache；失败后回收child再原子恢复，原先没有cache
则移除本次失败生成的文件。成功后的token刷新不回滚，避免撤销有效认证轮换。

部分正向结果使用native新cache中的车辆行，并保留旧cache中未返回身份的行。
只接受已有native契约的非空wnumber及ebike/motor路由；不接受未知线路，合并仍有
1MiB上限。完整成功不合并旧行，因此真实解绑能正常移除路由。文件写入600，
临时文件原子替换，IO在工作线程完成；取消等待已开始的工作结束后才释放锁。
磁盘不可写等错误返回安全分类，不输出私有路径。进程崩溃/磁盘故障仍不是经过
证明的跨文件事务；本阶段不改写现有会话journal，不承诺所有故障都能恢复。

## 4. 每车身份 freshness

增加独立profile_freshness，与status/BMS/travel一样区分attempt、success与error。
不完整发现只能刷新实际返回车辆的身份；未返回车辆保留旧成功时间、记SERVICE，
不能通过其它车的成功无限延长其身份。TTL仍为3×3600秒，加入原有本地有效期通知。

已返回车辆可以继续正常查询和按既有条件发送命令；缺席于不完整结果的车辆，
profile_query_failed阻止控制，即使旧身份还在有限TTL内。完整发现后的缺席仍触发
原来的absent、raw清理、重新出现的模型baseline重建。AUTH仍是账户范围失败。

部分正向发现仍采用原有每小时列表周期，不因不确定的另一业务线频繁刷新。
完全失败使用现有指数退避/jitter。没有新增周期status/BMS/travel/detail请求。
diagnostics增加每车profile的时间/错误分类，不导出身份或stderr。

## 5. 验收与限制

离线回归覆盖原生两业务失败却退出0、部分正向缓存合并、正常空账号、旧缓存
恢复而token不回滚、stdout/stderr超限、取消/回收、线程取消等待和错误隐私、
未观察身份不能续期/控制、完整结果能确认缺席。具体测试结果见
[b17验收证据](evidence/v2x-b17-validation.json)，精确main发布CI另查release notes。

没有修改生产安装/config/registry/database或重启HA，没有车辆动作；RC是隔离
client查询，不证明生产HA已经升级/运行修复，也不证明真实控制物理效果。
无实验名称、新实体或翻译键；已有正常中英界面及身份连续性保持。
未确认的能量单位、CRS、电池稳定身份、历史完整性仍待验证。

来源：[ninecli PyPI](https://pypi.org/project/ninecli/)、固定0.1.7 native执行、当前源码、
本机只读证据与隔离副本；[HA fetching data](https://developers.home-assistant.io/docs/integration_fetching_data/)。
