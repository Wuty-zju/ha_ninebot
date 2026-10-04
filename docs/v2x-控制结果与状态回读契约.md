# v2.x 控制结果与状态回读契约

2026-10-04，b16，基线main `afcb037b021ec000790b3f6c1a8da0ae899ac998`。
延续[b15云端鉴权策略](v2x-云端鉴权控制与实测契约.md)，不增加控制权限、
实体或真实车辆动作。此阶段只处理命令后对状态/错误的理解和有界诊断。

## 已复现的缺陷

已由源码和离线复现确认 S/F：b15命令返回后，如果车辆已标记为移除，
`async_refresh_vehicle` 会跳过查询；旧代码仍读取之前的 freshness.error，
没有错误就正常返回。回归测试首先在原代码上失败（DID NOT RAISE），证明
问题不是假设。旧路径还存在两个边界：

- CONNECTION/SERVICE/PROTOCOL命令错误直接返回“结果未知”，没有状态协调。
- 已完成的manual refresh任务可能尚未被其外层清理，后来的控制回读复用了
  命令前的已完成任务；这不能作为命令后的读取证据。

上述缺陷不证明真实车辆动作失败。修复不重发POST、不猜物理状态，也不把
GET成功或缓存中的锁/电源值当成命令完成。

## 发送、协调与错误语义

| 命令阶段 | 回读行为 | UI返回 | 诊断事实 |
|---|---|---|---|
| 本地门禁/队列拒绝，尚未调用backend | 无额外I/O | 原有拒绝/繁忙翻译 | 不覆盖上次实际backend调用 |
| 接口接受且状态查询成功 | 一次status协调，可与有效正在运行的同车请求合并 | 正常返回 | accepted/refreshed；物理效果仍未验证 |
| 接口接受但状态失败、跳过或车辆消失 | 不重发命令 | control_readback_failed | accepted + failed/skipped；不是命令执行失败证明 |
| 非认证错误、命令结果不确定 | 在runtime/车辆/认证仍有效时尝试一次status协调 | 始终control_uncertain，即使GET成功 | uncertain与原始ErrorKind；回读结果单列，不覆盖原错误 |
| 明确认证失败 | 启动reauth，不继续GET/POST | control_uncertain | authentication_required/auth，readback=skipped |
| 等待队列时取消 | 无命令调用 | 传播CancelledError | 不生成已调用记录 |
| backend调用中取消 | 无回读/重发 | 传播CancelledError | cancelled/skipped，不声称动作被物理取消 |
| 接受后回读中取消 | 停止本调用，不额外I/O | 传播CancelledError | accepted/cancelled，保留已有接受事实 |

普通查询仍沿用per-vehicle/per-group freshness、partial failure、backoff与reauth。
命令后的status协调为用户动作触发的有界强制读取；增加的是发生非认证控制
错误时的一次GET，不增加周期polling、详情/BMS请求或后台控制重试。
如果同时有活跃manual读取，可合并；已经完成的任务必须另起新读取。

`async_refresh_vehicle`返回bool只证明本次/合并请求**尝试查询**，不是成功或
物理验证。车辆移除/runtime停止返回False；回读还须检查snapshot仍有效与
status_freshness.error。`_forced`清理比较任务身份，旧任务的finally不得移除
替代它的新任务；取消/卸载仍完整回收。

## 控制结果诊断

`control_results.py`仅内存保留每车每动作最近一次backend调用，共享全局64条
上限，旧记录淘汰；卸载清空，不写Store、recorder、entity attributes或日志。
动作固定为四个已知命令，未注册动作不能加入。每次调用创建独立记录对象；
旧调用晚结束只能更新自己的旧对象，不能覆盖更新调用的槽位或清空后的缓存。

每车diagnostics中`control_results`按固定动作名导出以下白名单：

| 字段 | 类型/含义 | HA表达与隐私 |
|---|---|---|
| attempted_at | UTC ISO timestamp；backend调用开始，不保证上游已收到 | I：按需diagnostics，无Entity |
| finished_at | UTC ISO timestamp/null；本调用结束 | I；不是车辆动作时间 |
| outcome | pending/accepted/uncertain/authentication_required/cancelled | I；本地调用分类，不是权限或物理状态 |
| error | 固定ErrorKind/null | I；没有异常原文、账号、token或上游返回 |
| readback | not_requested/pending/refreshed/failed/skipped/cancelled | I；只描述GET协调，不表示动作完成 |
| readback_error | 固定ErrorKind/null | I；独立于命令错误 |
| physical_outcome_verified | 固定false | I；当前没有可靠物理确认协议 |

vehicle/SN仅作为私有内存索引，导出不含索引；不存在任意payload/未知key/精确
坐标/图片签名/行程轨迹。`allowed`、权限三态、readiness仍在独立control_policy
中，不能由上次accepted永久授权今后的命令。此表为新增本地派生字段，不混入
ninecli真实返回字段清单，不把诊断枚举当成新云端API字段。

无需新增状态实体、图标或启用选项。诊断JSON使用机器字段；用户可见的回读
失败说明同步中英，明确接口已接受但当前状态未读到。

## 验收和限制

离线验证车辆消失的原失败用例、命令前manual任务竞态、超时后GET成功/失败
仍保留原命令不确定性、认证不再请求、取消发生在两个不同阶段、已知门禁/
队列/卸载、缓存淘汰/旧调用晚结束/隐私。原HASS流程与原生加密、记录fixture
继续回归；阶段只跑必要针对性测试及一次完整验收。

本轮真实控制=0、新云查询=0、生产修改=0。没有为验证元数据再鸣笛/开桶/
启动/关闭，也没有为了增加功能数量建立实体。b15真实接口接受仍不等于物理
动作确认；生产安装/观察及未知单位/CRS/pack身份/上游历史完整性仍待证据。
精确main CI和prerelease以实际发布记录为准。
