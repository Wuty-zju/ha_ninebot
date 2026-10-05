# v2.x 历史查询 Actions 契约

> 历史归档：保持原版本/证据范围，不继续追加。现行行为先读[CURRENT_STATE](../../agent/CURRENT_STATE.md)和对应主题contract，不能直接执行本文旧goal或“未来”清单。

2.0.0b4 在 integration async_setup 注册两项 `SupportsResponse.ONLY` Actions。
HA 2026.1.0 已支持此 API，无版本字符串分支或最低版本提升。服务即使账户
卸载仍可被编辑器识别，调用时会给出可翻译错误。选择车辆 Device，不要求某个
sensor 启用；不接受 account、SN、entry ID、host 或 arbitrary endpoint。

## 参数

| Action | 参数 | 默认与上限 |
|---|---|---|
| get_trips | device_id、month | 必填；YYYYMM，200001至Asia/Shanghai本月 |
| get_trips | page、limit | 1、20；page≤1000，limit≤100，本地分页 |
| get_trips | include_detail | false；true时必须limit≤5 |
| get_trips | include_track | false；需要include_detail及集成coordinates选项 |
| get_trip_detail | device_id、ride_id | 必填；ID来自该车辆该月get_trips返回，≤256字符 |
| get_trip_detail | query_month | 可选，默认Asia/Shanghai本月；历史行程必须指定月份 |
| get_trip_detail | include_track、max_points | false、500；points≤2000，同时约束samples |

整数不接受bool或小数。月份拒绝非ASCII和未来月份。电池child或无法唯一确定
Ninebot账户的设备不能查询，需选择明确归属的车辆设备。已移除、profile过期、
reauth/卸载中或被禁用的车辆不能使用缓存绕过检查。

详情需当前车辆指定月份index中的唯一ride→detail映射，不假定legacy id就是
detail ID。ID不存在/重复/关联未知时拒绝请求；冷查询最多一次指定月，不扫描
历史月份。详情ID矛盾、已知起止时间不一致、空响应均作为协议错误，不串行程。
整个获取/解析失败抛HA异常，不返回成功形状的error对象或未经验证的旧值。

## 返回数据

两者返回 JSON dict，包含 schema_version=1、query_month、received_at（UTC
ISO8601）、source=ninecli、backend_version。get_trips另有month_mileage_km、
month_energy_raw、month_energy_unit=unknown、rides、warnings、pagination：

| 分页字段 | 含义 |
|---|---|
| page / limit / returned | 本地选定页/条数限制/实际返回条数 |
| available_in_response | 此次ninecli集合中可解析的行程数量 |
| has_more | 该集合是否还有下一本地页；false不代表云端已无更多行程 |
| total_known | null：真实云端全月行程计数未确认 |
| upstream_complete | unknown：没有上游分页参数或完整性证据 |

已观测20条和raw times=128不能据此宣称返回全月历史。排序按已验证起止时间，
没有时间的行保留彼此返回次序，不为其推测先后。空页返回rides=[]；总里程与
raw能耗仍来自请求月份，不受旧月最近行程fallback影响。

get_trip_detail返回ride；get_trips每个rides元素也使用同一normalized契约：
ride_id/detail_id/query_month/source/start_time/end_time/distance_m/duration_s/
max_speed_m_s/average_speed_m_s、energy_raw/used_electricity_raw/
server_average_speed_raw/raw_units=unknown、parser_contract/field_provenance/
warnings/speed_samples。单位明确的数值以SI表达，客户端按需要显示km/h或km。
总平均为距离/时长；时长与时间跨度冲突时average_speed_m_s=null。
服务端max与speed samples不互相覆盖。字段含义及证据见
[行程解析契约](v2x-行程字段与解析契约.md)，单位未知保持raw，不进入Energy Dashboard。

默认不含track、坐标或起终位置。speed_samples只有sequence、speed_raw、
unit=unknown，不含GPS；samples与详情点解析共用上限，不保证全部样本。
显式include_track增加coordinate_system=unknown、track和track_pagination。
每点只有latitude/longitude/sequence/speed_raw/distance_delta_raw/raw_units。
不转换坐标系，不推导未返回的点时间、高度、航向或delta米数。track_pagination
返回有效点数量、源字符串点数、truncated；invalid points可见于parser warnings。

**位置 response可能保存在automation trace、脚本变量或用户日志中**。双重
opt-in不等于不会持久化。没有GPS需求的自动化请保留默认include_track=false。
完整raw、signed image URL、token/password/account/UID/SN不进入响应。

## 自动化示例

在开发者工具 Actions UI选择本集成车辆设备，再把生成的device_id用于脚本。
以下ID是占位符，不是云端车辆SN。

```yaml
sequence:
  - action: ninebot.get_trips
    data:
      device_id: "REPLACE_WITH_HA_VEHICLE_DEVICE_ID"
      month: "202609"
      page: 1
      limit: 5
    response_variable: trips
  - if: "{{ trips.rides | count > 0 }}"
    then:
      - action: ninebot.get_trip_detail
        data:
          device_id: "REPLACE_WITH_HA_VEHICLE_DEVICE_ID"
          ride_id: "{{ trips.rides[0].ride_id }}"
          query_month: "{{ trips.query_month }}"
          include_track: false
          max_points: 500
        response_variable: trip
```

这是按需查询，不创建几百个实体或把数组塞入attributes。无需自定义WebSocket
API；HA现有Action/脚本响应即可处理结构化历史。不要用高频自动化循环请求。

## 缓存、生命周期与兼容

month缓存600秒，detail900秒；detail按账户runtime/vehicle/month/detail隔离，
RawStore最多128条/8MiB预算，detail最多8条，内存卸载即清理。网络查询最多4个
排队，串行共享账号backend；同缓存键的同时调用复用首次结果。include_detail
最大5个串行子查询，没有隐藏的后台扇出。若预算拒绝本次raw记录则报协议错误，
不把旧cache冒充本次成功。

Action不直接改变当前月state/event cursor；正常周期poll可重用较新的当月查询
结果，仍使用真实received_at维护freshness，不延长成功时间。旧月查询永不覆盖
本月汇总。查询前和输出前检查设备/会话/加载状态，位置选项输出前再次检查；
卸载取消进行中的网络查询，归属移除清除缓存，认证错误触发既有reauth。

compat.py通过公开属性检测旧多owner/new单owner注册表；child输入明确拒绝，
无owner composite不以primary owner猜路由。minHA/stable CI检查实际Action调用，
旧版不需要把历史数据塞state作为fallback。实际生产HASS仍为只读参考，未部署。

资料：[官方Actions与response data](https://developers.home-assistant.io/docs/dev_101_services/)、
[官方设备注册表](https://developers.home-assistant.io/docs/device_registry_index/)。
