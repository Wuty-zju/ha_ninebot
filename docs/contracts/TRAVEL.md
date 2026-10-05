# 行程、历史查询与骑行事件

现行基线：b24；当前Ride/严格trail/MonthSummary/历史Actions/Event均已实现。
只在本页更新行程契约；决策进度写[PROGRESS](../development/PROGRESS.md)，全路径用途查[字段表](../reference/FIELD_INVENTORY.md)。

## domain与语义

| 属性/路径 | 已实现解释 | 证据限制 |
|---|---|---|
| travel_id / detail_id | 有provenance的opaque关联，用于本车/月份详情索引 | 不以nickname、数组索引、任意id猜关联 |
| start/end | Unix秒→UTC；中国业务文本fallback明确Asia/Shanghai | 合成fixture时间不证明用户真实日程 |
| mileages / duration | km→distance_m；duration_s | 列表既有20/20时差一致；冲突留issue，不补0 |
| speed | 服务端最高km/h→max_speed_m_s | ninecli显示契约；App未独立核验 |
| average_speed | distance/duration总行程平均 | 时长<=0/冲突为unknown；不以avg_speed或sample mean替代 |
| ec | Wh，维护者确认；entity原ID保留 | 月/单次不设累计state_class，不是墙端电表 |
| used_electricity / avg_speed | 独立raw | 缩放/物理意义待验证，不当%或正式平均 |
| month times/duration/total/ec | 服务器聚合 | 不从不完整list求全月总量 |
| detail[] / day_total_mileage | 已有日序列关系；严格校验后Action日图表 | 不能逐ride重复累加；today entity尚未实现 |

Ride保留query_month/source/身份/起止/距离/时长/能量/速度raw、track/samples、issues/provenance。
当前详情合并仍复用Ride；独立RideDetail及更严merge/provenance为NR增量，不是假称新模型已完成。
稳定身份/排序/去重按已验证ID与时间，不假定list[0]最新或numeric ID递增。

## 轨迹

正式parser只处理已确认trail字符串：分号分点、逗号四列lon/lat/speed_raw/distance_delta_raw。
范围检查、非法点保留sequence/issue，限长截断有计数；不递归JSON静默选任意points数组。
CRS与逐点speed/delta单位未知，不自动缩放/WGS↔GCJ转换、合计delta修正总里程或推断点时间/heading/altitude。
起終点来自有效轨迹端点，非独立精确地址；非空nodes/新alias需fixture后再解析。

## Action response：唯一历史大对象出口

均使用SupportsResponse.ONLY；HA2026.1已支持，无需提高最低版本。
选择本integration唯一归属的车辆device_id；child、过期profile、卸载/reauth/移除车辆拒绝；不接受SN/account/entry/任意host。

| Action | 参数/限制 | 返回重点 |
|---|---|---|
| get_trips | month YYYYMM；page1..1000，limit1..100默认20；include_detail默认false且true时limit<=5 | 月聚合、MonthSummary日图表/覆盖率、rides、分页/warnings |
| get_trip_detail | ride_id<=256来自本车指定月；query_month默认业务当前月；max_points默认500<=2000 | normalized ride、samples；可选track/截断计数 |
| get_history | start/end_month；max_months_per_call默认3<=6；limit默认100<=100；cursor；include_daily_chart默认true | 有界跨月索引/统计、扫描与rides完整性、续查cursor |

月份拒绝未来/非ASCII/非法输入；详情只信本车/月唯一关联，冷查询最多一次指定月，不扫描全历史猜ID。
include_track默认false，须位置选项开启；get_trips还需include_detail。返回前复查选项/归属。
默认无GPS、完整raw、signedURL、账号/token/SN。轨迹响应仍可能进入HA automation trace/脚本变量或用户日志。
参数完整schema与字段名称以[services.yaml](../../custom_components/ninebot/services.yaml)及相关tests为准。

网络最多4个排队，账户串行、同key调用coalesce；include_detail最多5个串行子查询。
month TTL600秒、detail900秒；同月poll可复用真实received_at，旧月查询不覆盖当前月state。
HistoryStore最多8个cursor/20MiB/TTL900秒、身份上限20000，重启/卸载清空，不是永久行程数据库。
range扫描完成≠rides全部取得；20条与服务器times128的缺页证据必须保留。没有证明上游分页可补全。

```yaml
sequence:
  - action: ninebot.get_trips
    data:
      device_id: REPLACE_WITH_HA_VEHICLE_DEVICE_ID
      month: "202609"
      limit: 5
    response_variable: trips
```

不创建几百个历史sensor、不将轨迹/完整日表放entity attributes；HA现有Action响应足够，不需要自建WebSocket传输。

## Ride Event

当前创建默认可见/启用，用户可禁用；event_type=completed，仅代表cloud travel end report。
可靠ID、过去且一致起止/正时长才是候选；启动/reload/re-enable首个可靠窗口建立baseline，不重放历史。
首次非空也可能只建立baseline；30分钟本地迟到窗口、24小时观察缺口/时钟回退重建baseline。
跨月集合去重，每车128 hashed IDs、全局128 cursor车辆；溢出保守floor可能漏报，不宣称完整同步。
持久化只含有界hashed身份/cursor时刻；实际磁盘ack确认先于Event触发，异常暂停事件并Repair。
确认后崩溃可能丢事件，偏向at-most-once，非exactly-once。RestoreEvent不是去重真相。
属性仅ID/月份/UTC起止/距离/时长/max/总avg/source/late小摘要，无track/raw/samples。

## 维护与下一步

优先fixture replay；相关travel/month_summary/history_actions/ride_entities/ride_events/event_pipeline tests。
NR增量：RideDetail/严格merge→有界Wh/km统计与条件性today；非空remaining/CRS/逐点单位仍需证据。
独立研究alias/clean-room限制读[NinePlus第4/10/11/19节](../research/NinePlus生态源码审阅与ha_ninebot数据解析应用方案.md)。
历史细节在[Action基线](../archive/contracts/v2x-历史查询Actions契约.md)及[Event基线](../archive/contracts/v2x-骑行事件契约.md)；其中默认禁用/unknown Wh等已被后续覆盖。
