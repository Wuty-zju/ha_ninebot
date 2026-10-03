# v2.x 行程字段与解析契约

补充新2026-10-04真实只读month/detail，不修改2026-10-03固定字段清单的
证据时间。公开[shape清单](evidence/v2x-travel-schema.json)不含值；fixtures
内时间/身份/GPS为合成替换。S=源码显示契约，R=真实只读形状/关系；App未独立核验。

| endpoint/path | 意义/单位/证据 | A–J处置与当前实现 | 默认/ID与统计 |
|---|---|---|---|
| month.detail[] | 人类汇总文本，R | H/I：runtime raw与shape，不建实体/不解析自然语言 | 不进state |
| month.duration | 月总时长，具体汇总语义V | H/I；runtime保留，不当累计duration实体 | 无SC |
| month.ec | 能量raw，单位V | B：已有month_energy_raw，单位/SC均无 | ID不变 |
| month.first_time | 时间字段具体用途V | H/I，不推断车辆报告时间/首ride | 无实体 |
| month.list[] | 服务端取得行程集合，R | E/H：Ride列表供后续Actions，当前只runtime | 不建每ride实体 |
| month.month | YYYYMM查询月份，R | E/H，adapter检查匹配 | 非实体 |
| month.times | 返回128而list20；count语义/分页V | E/H/I：报告raw，upstream_complete unknown | 不声明完整历史 |
| month.total_mileages | 月里程，km现有契约S/R | A：month_mileage | 原ID；无TOTAL_INCREASING |
| list[].travel_id | 详情入参，S+R关联确认 | E/H；Ride ID/detail ID关联，不用作文件名 | opaque，非实体ID |
| list[].start_time | Unix秒，R与duration关系 | B/E：UTC started_at，后续Timestamp sensor | 新实体阶段再注册 |
| list[].end_time | Unix秒，R与CST文本关系 | B/E：UTC ended_at/排序，后续Timestamp sensor | 新实体阶段再注册 |
| list[].end_time_format | CST中国业务时间，20/20 R | E/H：显式Asia/Shanghai fallback；冲突标记 | 不按系统时区猜 |
| list[].mileages | km，S显示契约，R与detail数值一致 | B/E：distance_m，复用last_mileage | 原ID，distance无累计SC |
| list[].duration | 秒，S+R=end-start | B/E：duration_s；总平均distance/duration | 待实体阶段 |
| list[].speed | 服务端最高km/h，S；R与detail一致 | B/E：server_max_speed_m_s，samples不可覆盖 | 非samplemax |
| list[].ec | raw单位V | B/E：energy_raw，复用last_energy_raw | 原ID，无unit/SC |
| list[].used_electricity | 数值raw，%显示提示但缩放/物理语义V | E/H：used_electricity_raw | 不建SOC/energy实体 |
| list[].day_total_mileage | 数值字符串，日汇总定义/单位V | H/I/J：raw/shape，暂不建daily实体 | 不猜总累计 |
| list[].longest_distance | null，语义V | H/I/J：raw/shape，无法提供有效state | 无实体 |
| list[].longest_time | null，语义V | H/I/J：raw/shape | 无实体 |
| detail.start_time / end_time | 同ride起止秒，R | B/E/H：补充list，保留冲突issue | 原querymonth/归属保留 |
| detail.duration / mileages / speed | 同list数值；秒/km/max km/h契约S+R | B/E/H：显式归一化，detail修正有issue/provenance | 不偷偷换量 |
| detail.ec / used_electricity | ec字符串、used整数，R；单位V | E/H：独立raw，不互作fallback | 不接Energy Dashboard |
| detail.avg_speed | int=0，R；语义V | E/H/J：server_average_speed_raw，不替代计算平均 | 不建正式average实体 |
| detail.trail | semicolon点，comma四列，R+S | E/H：授权response未来出口；当前仅详情runtime | 不进state/recorder/diagnostics值 |
| trail col1 / col2 | longitude/latitude，R范围+S标签 | E/H：TrackPoint，坐标系unknown | 不自动转换 |
| trail col3 | speed raw，单位V | E/H：SpeedSample raw，m/s保持None | 均值/最大与overall/server不同 |
| trail col4 | distFromPrev raw，单位/增量用途V | E/H/J：distance_delta_raw | 不靠sum修正里程 |
| detail.img | URL（可能路线截图/隐私内容），R | J：Raw缓存先移除，不当车辆ImageEntity | diagnostics禁止值 |
| detail.is_show_simple_point | bool；上游显示提示具体语义V | H/I/J：raw/shape，不当权限/能力 | 无实体 |
| detail.show_simple_point_days | int；上游显示/保留策略V | H/I/J：raw/shape，不设HA retention | 无实体 |
| detail.avg_engine_power | null，单位/语义V | H/I/J：raw/shape | 不建Power |
| detail.avg_shaft_speed / max_shaft_speed | null，单位/语义V | H/I/J：raw/shape | 不建Speed |
| detail.avg_throttle_opening | null，单位/语义V | H/I/J：raw/shape | 无实体 |
| detail.avg_torque / max_torque | null，单位/语义V | H/I/J：raw/shape | 无实体 |
| detail.engine_power_nodes | null，数组schema/单位V | H/I/J：raw/shape | 不机械解析候选 |
| detail.mileages_nodes | null，schema/单位V | H/I/J：raw/shape | 不自动作轨迹 |
| detail.shaft_speed_nodes | null，schema/单位V | H/I/J：raw/shape | 无实体 |
| detail.speed_nodes / tamp_speed_nodes | null，schema/单位V | H/I/J：raw/shape | 不伪造samples |
| detail.throttle_opening_nodes / torque_nodes | null，schema/单位V | H/I/J：raw/shape | 无实体 |

没有真实出现的recon别名不能自动启用。已兼容旧fixture的id/detail_id需显式
映射；id alone不推断详情等价。detail parser只认当前确认的trail字符串，不
遍历任意points/route/gps数组。超限返回truncation计数；非法点保留原sequence
并标issue，绝不把缺失/未知填零。坐标0,0可合法但不能声称一定真实。

query actions未来必须账户/设备/车辆路由校验，默认不返回GPS；automation trace
仍可能持久化response位置，不能宣称Action即无隐私风险。历史时间字段已替换
为synthetic日期，fixture不证明用户真实行程时段。
