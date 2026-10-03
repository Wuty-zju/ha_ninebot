# v2.x 实施与验收记录

## 2.0.0b1：Phase 0 / Phase 1 / Phase 7 安全门禁

基于 main `1bba9eb`，开发分支 `feature/v2x-raw-safety`。设计文档与机器字段
清单作为独立 v2.x 依据提交，保留历史报告基线。

- Phase 0：已留存七条历史 payload 转换为 `tests/fixtures/ninecli/0.1.7/`
  可重放样本；metadata 区分记录来源和合成坐标/身份/URL替换。97路径/86叶
  inventory 不等于 raw fixtures。非空 travel/detail 缺项明确，未新增云查询。
- Phase 1：RawStore 与 normalized snapshots 分离，未知业务字段可保留供后续
  解析；token/password/蓝牙secret/个人资料和URL先替换。private GPS 内存值
  不向 diagnostics 导出。缓存默认不落盘、不进入 entity attrs/state/recorder。
  保留数据预算8MiB、记录128、detail8/15分钟，HTTP仍1MiB；深度12、节点25000、
  schema路径256有界。预算含保守metadata估算，不声称Python进程总内存限制。
  CPU准备在executor，取消后不允许后台工作重新写入卸载缓存。raw超限拒绝计数，
  不使既有可归一化数据失效；仍显示旧raw成功时间，不伪装为当前raw。
- Phase 7 第一部分：三态support/permission+semantics/evidence。选项、allowlist、
  present、profile/status新鲜且无当前错误、动作证据全部通过才允许。排队后
  再检查门禁。当前生产parser不解释未知权限，因此所有硬件动作默认拒绝。
  不把 endpoint 存在当作车辆权限。旧Lock身份/状态保留，actuator返回翻译错误。
  权限真实解析、逐动作实车语义验证仍待证据；不能将mock当成真实授权。

实体 unique_id、ConfigEntry/schema、会话事务、分组轮询周期和backoff均保持。
minimum HA2026.1.0，依赖ninecli==0.1.7，HACS运行资源仅integration目录。
本阶段没有生产HA写入、重启、部署或真实控制。

验证记录在 `evidence/v2x-b1-validation.json`；发布前完整pytest、Ruff/format、
mypy及对应提交的Hassfest/HACS CI必须通过。下一阶段是travel domain/detail
查询与证据补齐，不提前将未确认单位变成正式物理实体。

## 2.0.0b2：Phase 2 Travel domain / backend

基于main `a99af471`，分支 `feature/v2x-travel-domain`。保留已有client/session
生命周期，通过薄NinecliBackend接入统一结果及endpoint metadata；backend
endpoint支持与VehicleCapabilities权限完全分开。coordinator轮询/互斥/
取消/reauth逻辑沿用，travel归一化在executor执行。

### 新只读证据与采样边界

只读复制生产会话到0700隔离目录，在只读root filesystem、限制内存/PID、
无特权的独立容器内启动Bearer鉴权loopback serve；没有挂载生产config。
一次vehicles、两辆车各一次202609月查询，后续为确认详情关联再读一次
vehicles，并读取一个travel_id详情，共5个业务REST操作。认证内部云调用
数未抓包计数，不冒称只有5个底层网络请求。没有控制请求、自动轮询或全历史
扫描。临时凭据与SN selector已删除，私有原始证据仅保留在仓库外的受限审阅
目录；公开样本全面替换身份、时间安排、图片及GPS。

- 一辆车list为null，另一辆20条；times=128仅保留raw计数，分页完整性未知。
- list只有travel_id，没有detail_id。recon源码/协议报告说明该字段原样作为
  详情参数；实读同ride起止/时长一致，距离/最高速度数字一致，确认关联。
  legacy id不自动等价detail ID；多ID冲突拒绝关联。
- 20/20行duration=end_time-start_time；20/20 end_time_format对应Unix秒
  Asia/Shanghai。内部统一UTC，不按HA用户时区/字符串长度猜秒或毫秒。
- mileages为km、speed为服务端max km/h来自ninecli0.1.7显示契约与recon
  静态取证，真实list/detail数字一致；App UI未独立核验。契约来源在Ride
  provenance保留，未扩大为任意候选字段通用解释。
- trail是真实字符串，semicolon分点，每点lon,lat,speed,distFromPrev四列；
  lon-first经数值范围及源码标签核对。坐标系/后两列单位仍unknown，不转坐标，
  不补点时刻、不用samples改写servermax/overallaverage。
- detail avg_speed返回0，语义未确认，仅server_average_speed_raw；总平均是
  distance/duration。energy/used_electricity继续raw；不进入Energy Dashboard。

旧实体ID不变。时间明确时选真实时间排序的最近ride；旧无时间结构保留last
returned兼容值，但新时间依赖实体不能借此推断排序。month polling不存完整
轨迹、不拉detail；只有显式详情parser生成有界TrackPoint模型供下一阶段Action。
归一化rides保存在runtime，不作为entity attributes。

证据：[travel schema](evidence/v2x-travel-schema.json)、fixtures metadata及
[逐字段补充](v2x-行程字段与解析契约.md)。模型/parser受影响114项测试、Ruff、
mypy已通过；发布前完整校验结果见 `evidence/v2x-b2-validation.json` 与发布CI。
Phase3/4/5实体、actions、event尚未实施，不能据本阶段模型声称用户功能已齐备。

## 2.0.0b3：Phase 3 Last Ride entities

基于Phase2已合并main `f8c786da`，分支 `feature/v2x-last-ride`。新增5个
默认禁用sensor，stable key见下表；没有修改legacy unique_id、模型/ConfigEntry
存储或分组轮询。所有名称采用has_entity_name+中英translation_key，icons.json
放在integration运行目录。

| key | 数据与HA表示 | 验收/限制 |
|---|---|---|
| last_ride_duration | Ride.duration_s；DURATION/s，无SC | 实读20/20 duration=end-start秒；不当累计时间 |
| last_ride_start | Ride.started_at UTC；TIMESTAMP，无unit/SC | 原生datetime，不是普通文本；仅有可靠ended_at排序的ride |
| last_ride_end | Ride.ended_at UTC；TIMESTAMP，无unit/SC | future/conflicting_time/reversed不可用值，不假定已完成 |
| last_ride_max_speed | server_max_speed_m_s×3.6；SPEED/km/h，无SC | 明确ninecli显示契约，samples不能覆盖；App核对仍待用户参考 |
| last_ride_average_speed | distance_m/duration_s×3.6；SPEED/km/h，无SC | duration>0、零距离合法0；时长与时间跨度冲突不发布平均值 |

旧无时间payload仍给legacy last_mileage/last_energy_raw兼容值，但这5个新
量返回None；group过期则HA标准unavailable。已有上月fallback只更新last ride，
不会把上月合计覆盖本月。实体属性不含rides/raw/track/samples/GPS；没有自动
详情请求或额外轮询。energy/used_electricity仍raw，没有创建猜测单位的新实体。

测试在隔离HA中检查默认禁用、启用后的真正Timestamp state编码、device class/
unit/state class、未来/含糊时刻、duration冲突、旧无时间兼容，以及无详情/控制
请求。原有身份/reauth/隐私/门禁/生命周期测试继续通过。完整校验与CI以
`evidence/v2x-b3-validation.json`和GitHub prerelease notes为准；生产HA未部署。

下一阶段Phase4注册get_trips/get_trip_detail ONLY-response查询action，严格
设备/账户/车辆路由和GPS opt-in，仍不将大历史对象放入state machine。

## 2.0.0b4：Phase 4 Historical query actions

基于已发布main `8fa540b9`的Phase3内容，分支 `feature/v2x-query-actions`。
`async_setup`常驻注册两项SupportsResponse.ONLY Actions；最低2026.1已支持，
无新增WebSocket、minimum bump或attrs/bus fallback。新services/compat资源全部
位于integration directory，可被HACS打包。中英字段描述、icons与参数契约见
[v2x-历史查询Actions契约.md](v2x-历史查询Actions契约.md)。

- 必填device_id；按Device Registry所属entry查找本次账户已知/新鲜/存在车辆，
  拒绝child、battery identifier、foreign/disabled、多个Ninebot owner及无明确
  owner的composite，不用primary entry猜账户。compat集中检测新单owner属性与
  旧config_entries集合。没有启用实体也可按车辆设备查询。
- month限ASCII YYYYMM、200001至当前业务月份；page<=1000、limit<=100，
  不截断float/接受bool。详情ride_id须在该车辆指定month index唯一，并有已确认
  detail关联；冷查询最多一次该月，不扫历史/猜legacy id。详情时刻与已知summary
  不符/返回空对象/响应ID矛盾时拒绝合并。
- include_detail需要limit<=5；串行最多5详情。轨迹需要include_detail（列表）
  或detail action、coordinates选项与include_track双重opt-in。最终返回前再次
  检查设备归属/状态与位置选项，取消/卸载/移除不能返回旧scope数据。
- 共用RawStore：month600秒/detail900秒，detail ID再按month隔离；最多4个网络
  排队查询，账号互斥、相同查询复用。卸载取消网络请求、清空缓存，auth进入原有
  reauth。ownership消失/恢复清除私有缓存。超限raw不返回旧记录冒充新成功。
- Action不直接改coordinator.data/current month freshness/event baseline。周期
  poll可重用action更近的当月记录，成功时刻仍是真实received_at，下一次到期也
  基于原始时刻；previous-month近期cache复用，避免重复空月fallback。
- 月完整性未知：本地available_in_response和has_more仅指已取得集合，
  total_known=null、upstream_complete=unknown，不把20条或times=128当全月计数。
- 输出是normalized whitelist，不含account/SN/password/token/原始JSON/URL；
  默认没有coordinates/track/start-end location。能耗和samples单位仍unknown，
  单次详情max_points默认500/最大2000，同时约束samples。UTC时间编码ISO8601。
  response变量/automation trace可能保留GPS，文档明确此出口边界。

验证使用recorded sanitized fixtures与隔离HA/mock，无本阶段真实车辆/云查询、
生产HA写入/部署/重启或控制。验证结果见 `evidence/v2x-b4-validation.json`；
release前对应提交必须通过完整pytest、Ruff/format、mypy、Hassfest/HACS CI。
尚待Phase5事件、Phase6电池身份/兼容及Phase8Image/GPS优化；不能将本阶段
query能力当作完整历史数据库或云端分页实现。
