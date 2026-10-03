# v2.x 骑行事件契约

2.0.0b5新增可选 `event.<vehicle>_ride`，默认禁用，实际entity_id由HA/用户
决定。unique_id为现有车辆身份加 `_ride`，不修改旧实体身份。名称、中英
状态属性/icon由HA翻译；不使用BUTTON/MOTION类，因为它是云端行程结束报告。

## 来源与判定

只消费已成功归一化的travel snapshot及已取得的previous-month fallback Ride，
不请求详情、GPS或控制，也不新增历史扫描。必须有已由真实样本/源码确认的
travel_id provenance、有效且过去的起止时间、正时长，以及时长与时间跨度相符
（允许1秒误差）。时间/身份冲突、不完整或未来报告不发事件，新ID本身不够。

这表示 `cloud_travel_end_report`，不是独立确认车辆正在/曾经移动，也不是引擎
启动/锁定事件。真实云端分页和上传延迟未确认，可能漏报；普通状态与query不
因缺乏可靠事件候选而不可用。资料标签：解析契约由源码/recorded fixtures确认，
事件状态机/保存先于触发由离线与隔离HA测试确认；无本阶段真实车辆查询。

## 基线、迟到和去重

首次启用、每次HA启动/entry reload、重新启用都会用首个非空可靠窗口建立
baseline，不发送启动前已有行程。尚无可靠行程时保持uninitialized，因此
第一份非空报告也用于基线，不能承诺启用后第一趟必定通知。RestoreEvent可以
恢复上次可见state，但不作为去重真相，也不重放恢复的事件。

正常运行只接受baseline之后、接收时刻前30分钟内、未见过的结束报告。
30分钟是本地保守策略，不是九号实测上传保证；可接受晚于更近期已见行程的
旧结束报告，并标记late。24小时成功观察缺口或时钟回退重建基线，不大量
追补历史。排序比较集合及可信时间，不假定list[0]或numeric ID递增。

跨月保留seen集合，同ride_id不因为月份变化再次通知。现阶段保留已取得的
fallback源；新月已有行程时不会额外扫描上一月，因此上月晚上传、分页外的
报告可能未被发现。历史查询Actions不直接推进baseline/cursor；周期poll正常
消费其较新的共享cache时才按同一规则观察。它不是完整行程同步数据库。

每车最多128个hashed ID。若溢出，建立保守时间floor，丢弃已驱逐ID对应时刻
及更早候选，避免重排后重复触发；大量相同结束时刻的batch可能全部被抑制。
当前最多128个cursor车辆，优先驱逐未订阅车辆；所有容量占满时暂停事件。
这些限制是安全边界，不伪称不会漏报。

## 持久化与故障

每entry使用私有 `.storage/ninebot.<entry_id>.rides_v1`，只存hashed vehicle/
ride标识、必要cursor时刻和有界集合，不含完整Ride、GPS、账号、token或密码。
读取最多3MiB，schema/envelope版本必须明确支持，未知/损坏文件保持原样并
创建存储Repair。默认禁用时不会读写事件文件。diagnostics只有健康和计数。

HA Store的原子保存可能对部分WriteError只记录日志而返回，不能把await返回
当成功证据。流程为：executor预读并确认envelope → 构造新cursor → 公共Store
原子写入 → executor读取实际磁盘并核对候选内容 → 更新内存 → 触发EventEntity。
不使用Store内存write cache冒充磁盘确认，也不修改HA生产数据库或registry。

异常/acknowledgement失败暂停事件，保留旧cursor与其它集成功能，Repair提示
用户检查存储权限/版本并保留备份。普通云端timeout/5xx只沿用coordinator重试，
不生成存储Repair。卸载/移除订阅取消任务，完成写盘后已卸载也不会触发事件。

cursor与HA state不是同一事务：崩溃/取消发生在“确认保存”与“发送”之间会
遗漏通知，不会自动重试已确认ID。采用at-most-once取向，**不是exactly-once**。
原子writer也不保证硬件掉电后的所有磁盘行为。旧备份恢复后重新基线防回放，
需要找回历史时用查询Actions；外部不可逆自动化仍建议自行按ride_id幂等。

## 属性与自动化

event_type=`completed`；属性含ride_id、query_month、UTC ISO start_time/end_time、
distance_m、duration_s、服务端max_speed_m_s、总平均average_speed_m_s、source
及late。没有raw、track、位置、samples或大对象；SI单位与query一致。Recorder
可保存这份小摘要，启用事件代表用户选择记录该行程观察，不能宣称无行程隐私。

```yaml
triggers:
  - trigger: state
    entity_id: event.REPLACE_WITH_YOUR_VEHICLE_ride
conditions:
  - condition: template
    value_template: >-
      {{ trigger.from_state is not none
         and trigger.to_state is not none
         and trigger.from_state.state != 'unavailable'
         and trigger.to_state.state not in ['unknown', 'unavailable']
         and trigger.from_state.state != trigger.to_state.state
         and trigger.to_state.attributes.get('event_type') == 'completed' }}
actions:
  - action: logbook.log
    data:
      name: Ninebot
      message: >-
        Ride {{ trigger.to_state.attributes.ride_id }} ended;
        {{ trigger.to_state.attributes.distance_m }} m
```

示例跳过实体初始化（from_state=None）/不可用恢复时的状态变化，允许已存在
unknown state转为第一条真实事件。实际自动化仍按需求额外区分state时间、
ride_id与自身持久化去重；
不要把 restored state 当新完成报告，也不要据此直接启动或控制车辆。

官方依据：[EventEntity](https://developers.home-assistant.io/docs/core/entity/event/)、
[协调器与订阅生命周期](https://developers.home-assistant.io/docs/integration_fetching_data/)。
