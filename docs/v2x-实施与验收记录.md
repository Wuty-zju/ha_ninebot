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

## Phase 5 进行中：事件 cursor（未发布）

分支 `feature/v2x-ride-events` 基于Phase4源提交，已有纯领域cursor模块，
尚未接入EventEntity或HA Store，不应当作ride事件功能已交付。

- 只有ID、过去的可靠起止、正时长与时间跨度一致才作为云端结束报告候选；
  时间/身份冲突、未来结束、未知起止不因“新ID”就发completed。
- 首次非空可靠窗口建立baseline，不发历史；空/未知保持uninitialized。
  restart/首次启用必须由pipeline强制baseline（尚待接入），旧backup不重放。
- 跨月按hashed opaque ride ID比较集合，重排和重复查询不制造新事件。
  seen最多128，超限建立保守retention_floor，防止已驱逐ID被重放；同结束时刻
  的过大批次可能被全部抑制，优先防重复而非承诺不遗漏。
- 30分钟late window与24小时gap rebaseline是本地保守策略，**不是实测上传延迟**。
  超窗backfill不补发，时钟回退/长期缺口重建baseline；实际上传时延仍待证据。
- cursor只包含时间、hash与有界集合，restore严格拒绝未知/不完整/非法结构。
  后续Store保存前确认schema版本与账户/车辆scope，不能用RestoreEvent当去重真相。

20项领域测试覆盖baseline、重排、跨月、迟到、恢复、容量上限和非法存储，
该模块分支覆盖100%，Ruff/format和mypy通过。没有新增真实查询、生产HA写入或
控制。待完成：版本化Store、durable-before-emit、enabled订阅与卸载取消、
EventEntity/中英翻译/icons、月边界窗口、HA集成测试及完整发布校验。

### Phase 5 EventEntity / Store 开发检查点（未发布）

已接入默认禁用的 `event.<vehicle>_ride` 与按entry的RideEventPipeline，
但版本尚未递增、未做阶段完整CI/发布验收，仍不当作Phase5正式交付。

enabled entity在added时订阅、remove时注销；无订阅不读写事件Store、不新增
详情或云端请求。每次启动/重新订阅重建baseline，RestoreEvent仅恢复可见旧状态，
不会回放历史。cursor比较现有成功travel snapshot及已取得的上月fallback Ride，
保留跨月seen集合；不宣称收到所有物理骑行。当前没有为事件另扫上一月，因此
上月late upload只有出现在已有fallback/已取得数据流时可被发现；云端分页及
上传延迟仍未知，事件是best-effort cloud end report，不是完整骑行账本。

Store以entry key隔离，vehicle/ride ID仅存hash，每车128个seen、最多128车、
文件读取上限3MiB。通过公共HA Store原子保存，但不能只依赖async_save返回：
Core实现对部分WriteError仅日志记录，不抛异常。pipeline在executor预读真实
envelope（拒绝未知版本/损坏，不触发Core自动rename/migration），保存后再次
核对磁盘data与候选cursor相同，才更新内存并发EventEntity事件。失败暂停事件，
保留旧文件与其他车辆状态/查询，产生需要用户处理的存储Repair。

写盘后/发事件前崩溃或取消仍可能漏一次事件；不是exactly-once。超窗或大量
同结束时刻batch会保守抑制；不承诺追补。属性只有稳定ID、月、起止、距离/时长、
服务端max/总平均、source和late，没有raw、GPS、samples。诊断只含健康与计数。
新event名称/状态属性/Repair/icon均中英翻译，runtime资源在integration目录。

20项cursor单元测试与8项pipeline测试通过；与既有setup测试合计48项通过。
测试在一次性HA config目录中使用真实原子writer及磁盘确认，验证静默write
failure、取消、首次enable的HA自动reload debounce、重启不重发、缺省不启用
与公开state不含大对象。cursor模块覆盖100%，event/store受影响覆盖约92%，
Ruff/format、mypy通过；阶段完整pytest/最低与stable CI、Hassfest/HACS待执行。
没有生产HA写入、部署、重启、真实控制或新增云查询。

## 2.0.0b5：Phase 5 骑行事件阶段验收

前述两份“进行中”记录是开发检查点，正式行为以
[v2x-骑行事件契约.md](v2x-骑行事件契约.md)为准。候选版本2.0.0b5，
分支feature/v2x-ride-events，基于Phase4已合并main `bd2ffcbdf367`。

正式身份仅接受已确认travel_id provenance，不将legacy id或detail ID未知
语义用于completed判断。缺乏可靠时间/时长的车型保留state/query功能，不发
推测事件。HA最小/稳定版实际Entity restore/生命周期、cursor离线场景、
原子writer及磁盘确认是验收依据。完整校验见evidence/v2x-b5-validation.json；
只有相同main提交的CI通过后才发布，release notes记录确切SHA与CI链接。

不提高最低HA、不更新ninecli、不更改旧unique_id或生产HA。Phase6电池身份/
compat、Phase7权限诊断/证据门禁和Phase8Image/GPS/按依赖轮询仍需继续；
原始能量/点速度/delta/坐标系/permissions仍有待验证，不以事件阶段替代这些要求。

## 2.0.0b6：Phase 6 电池观察身份与兼容

基于已发布main `b13cfe96286c`，独立分支 feature/v2x-battery-identity。
[电池契约](v2x-电池身份与设备模型契约.md)与
[evidence/v2x-battery-field-review.json](evidence/v2x-battery-field-review.json)
复核21个已观察BMS路径；源码/合成身份别名与真实样本区分。

修复旧单包实体在多包时继续抓旧包的歧义，以及分包实体误认匿名slot placeholder。
集中选择策略保证single→multi→replacement/reorder/identity-loss保留原ID/用户名称/
vehicle assignment，支持否定不出循环数；明确别名冲突拒绝，不偷偷合并历史。
SOC observation signature使用有序类型化哈希，旧编码匹配时rebaseline而不换generation
或清累计；真正source change仍按现有模型处理。无身份同数量换装无法识别，明确保留限制。

compat集中feature detection仅输出registry API能力，诊断新增无身份/测量值的grouping
摘要。当前真实两份battery样本无packSN，不能证明固定child组成或可移动硬件身份，
因此没有新电池device/child、registry迁移，也没有child downgrade演练可以宣称通过。
硬件模型/真实identity取得证据后另立实施项，不能用合成tests代替；不阻碍Phase7/8。

完整离线275tests通过，combined branch coverage97.62%；battery/sensor/compat100%。
Ruff、format、mypy34源文件通过；精确main提交的最低/稳定CI、Hassfest/HACS通过后
发布b6，证据见evidence/v2x-b6-validation.json及release notes。
未修改生产HA、未执行控制、未增加云查询、未改变最低HA/依赖pin/旧unique_id。
本阶段不增加实体名称或配置项，因此沿用既有中英translation keys与icons。

## 2.0.0b7：Phase 7 能力证据与统一控制决策

基于已发布main `c61dd2afed526`，独立分支feature/v2x-control-diagnostics。
[权限契约](v2x-权限门禁与能力证据契约.md)区分backend软件endpoint、车型support、
账号permission和已验证动作语义。现有真实权限null/未知，生产parser没有verified
capability，未知继续拒绝，不用ownership/acc/锁状态等候选字段推导授权。

按钮availability、排队前/后执行检查、diagnostics共用ControlDecision。诊断输出
固定blockers、support/permission三态、证据是否存在和条目数，不导出free-form label、
权限原始对象或个人资料。拒绝重复capability、unknown action、空白label、backend
没有实现的动作及非明确true的用户启用；reauth/readback/cancel/不重试沿用现有。
保留旧controls boolean，新增backend_support说明软件endpoint不代表车辆权限。
中英Options明确“当前仅启用选项不会开放硬件”，entity ID与旧Lock观察/拒绝engine
行为不变，不增加误导性的永远不可用engine实体。

完整离线282tests通过，combined branch coverage97.66%，capabilities100%；Ruff/
format/mypy34源文件通过。精确main提交最低/稳定CI、Hassfest/HACS通过后发布b7，
证据见evidence/v2x-b7-validation.json与release notes。新增云查询0、实车动作0、
生产HA写入0；无dependency/最低HA/registry/storage schema变更。
未交付权限位图解析与真实控制语义，待协议证据及具体动作授权；下一阶段继续
Image缓存、安全URL/GPS及启用实体/内部模型依赖决定请求，不缩小主要目标。

### Phase 8 Image 缓存开发检查点（未发布）

基于已发布b7 main `7d05b6c21ff5`，独立分支feature/v2x-native-ux，尚未完成
Phase8验收或递增版本，不当作完整阶段交付。

核对HA2026.1实际ImageEntity实现：它缓存图片bytes，并不会因上游profile/URL
自行清除。旧实现每次profile success都改变image_last_updated，却可能在URL切换
后继续返回旧cached image。当前检查点仅在URL变化时更新时间和清除官方缓存，
无URL则unavailable/无图片时间，并启用官方HTTP client TLS证书校验。
保持image_url/官方下载及缓存，不自建下载器。Tracker改用最低版已导出的公开
根模块TrackerEntity，与新版Core原生Zone API对接，不自行实现zone判定。

现有21项setup tests通过，新增一项通过实际HA image component/async_get_image
入口验证同URL仍使用cached bytes、换URL取新图、移除URL不可用；图片下载完全
mock，测试目录一次性。初次新test把success time置于未来造成freshness拒绝，已用
受控clock纠正（运行实现不需要为此放宽时间检查）。Ruff/format/mypy通过。
没有新增云请求、真实下载/控制或生产HA写入。

仍需完成URL origin/敏感query/redirect安全契约、实际GPS/Zone/选项撤销测试、
typed entity contexts + estimator/event/bootstrap dependency scheduling及阶段完整CI。
公开fixture均为替换URL，现有保存travel资料不含vehicles payload；只读production
registry/restore参考亦无可用图片origin，因此不能声称已核实CDN allowlist。
不得猜域名，必要时另立一次孤立只读vehicles查询计划。阶段整体设计/验收报告
完成前不发b8，不以此检查点替代主要目标。

## 2.0.0b8：Phase 8 图片 / GPS / 功能请求图

独立分支 feature/v2x-native-ux，基于 b7 main `7d05b6c21ff5`。
增量实现保留旧 ID、entity translation keys、ConfigEntry/Store schema、最低 HA 和
ninecli pin。具体安全与发现例外见[Phase 8契约](v2x-图片位置与请求依赖契约.md)。

- 图片 URL 只接受审核的匿名 HTTPS 资源；移除真实返回的 opaque nbchecksignv1，
  TLS验证、redirect禁用、错误不打印 URL/异常正文，复用 Core 解码/bytes cache。
  同URL保持时间戳/缓存，变化/移除才失效。首选light可安全回退img，dark不新增实体。
- GPS 使用公共 TrackerEntity 导出；实际HA测试 home/not_home、无自动坐标转换、
  非法坐标不可用、撤销选项后无坐标且ID保持。测试位置是合成值，不证明实车CRS。
- 有类型 per-vehicle context + 内部 estimator/event 依赖决定组参与轮询。已发现
  可表示电池且全部禁用时停止常规BMS；空/匿名多包每小时稀疏probe，首次失败
  按原组退避发现。保留新车bootstrap与每小时车辆发现；month-only不额外查上月。
- 单次隔离 vehicles 业务请求确认两车六URL来源；选取一资源去掉签名匿名HEAD
  200/image/png，不下载正文/不跟随跳转。公开 evidence 和 selected shape fixture
  只留域名/参数名/形状，真实资产路径、签名、身份不入Git。源会话前后hash相同，
  子进程回收、临时会话副本删除；无生产写入/重启/部署/车辆控制。
- 本地完整336 tests通过，含分支覆盖97.85%；新增demand/image/image_urls和GPS
  模块100%，Ruff/format通过，mypy36源文件通过。HTTP hook另经真实Core解码/
  缓存路径的单项验证。最低2026.1、stable2026.9.4、beta2026.10.0b0均精确pin
  独立CI；Hassfest/HACS和准确发布提交的结果以release notes为准，不以本地结果
  冒充远程成功。未变化的安全/迁移测试沿用阶段完整suite，无额外实车轮询。

尚未确认物理pack身份与composition，所以不强建child；opaque/null permissions
保持拒绝，engine语义不映射lock。能量、trail speed/delta、CRS、服务端全量分页
继续待验证。Phase 9成本收益不成立时不创建任意raw entities；Native backend为
长期独立项目，不纳入本轮默认替换。

Phase8首次beta CI发现3处Probatio/voluptuous静态Schema类型不匹配（run
37162821409），未跳过或移除beta门槛。按官方公共alias规则在compat集中动态
解析validation namespace，配置/Actions使用同一引擎；没有新runtime依赖。
本地42项受影响测试通过，最终完整验收以修复后的精确提交三版本CI为准。

## 2.0.0b9：无效实体清理与界面语义

根据安装b8后的最新反馈，用户明确改变“无效旧实体永久占位”的要求。只读审计
生产HA已安装b8/2026.10.0b0，一entry/two vehicles/56登记，其中30由integration
默认禁用；options为空。有限日志无相关ERROR，不能把登记状态当作实时云查询。
详见[问题审计](v2x-实体与控制问题审计及修正.md)。

- registry.py精确废弃名单在成功首刷后、平台加载前执行，独占entry+单车辆
  device identity+本集成platform限定，不删陌生/共享/模糊/有效model身份。
- 删除无值LegacySensor、不可操作Lock平台、无用max_range number的创建路径。
  保留二元锁状态、真实读数、有效旧alias和有意义模型历史，不直接编辑recorder。
- 普通鸣笛/座桶与控制选项中英名称去实验前缀，补按钮/里程/diagnostic/参数icons；
  diagnostics仅增加移除数量。当前门禁未改，后续直接engine buttons和控制策略
  独立实施，不能把此阶段说成恢复了实车动作。
- 本地338 tests，含分支覆盖97.83%，registry100%；Ruff/format、mypy36文件通过。
  失败首刷不清理、幂等重载不重建Lock、跨entry/无device/template/未知ID守卫验证。
  最低/稳定/beta+Hassfest/HACS准确提交通过后发布，真实结果见release notes。
- 生产只读、真实云业务查询0、真实车辆控制0；升级时清理旧实体是明确授权的
  行为变化，原自动化需改引用。开发过程中未将其应用到生产HA。

## b10：默认可见性修正

- 提升已确认的两种续航、六类最近行程及 Image 默认；保留用户禁用和身份。
- registry API 升级只对当前条目独占、仍存在的车辆，首刷失败不执行。
- GPS/控制/事件/raw 诊断/估算不在提升名单；按实际 consumers 继续调度。
- 339 passed / 97.84% 分支覆盖；registry 100%；Ruff/format/mypy 通过。
- 本地 HA 仅只读。真实查询只用于原始契约研究，未执行控制；当前权限仍 null，
  b10 未修正控制策略。准确 main CI 与 prerelease 以 GitHub release notes 为准。

## b11：native 车辆发现与业务线路缓存

- 使用原生JSON vehicles操作准备native cache，解决REST发现不写缓存导致BMS的no_cache。
- REST/CLI共享有界串行队列、输出/时间限额、取消回收；native更新前停止旧serve，不猜业务line，不将密码放argv。
- CLI非零退出通过REST whoami明确认证，未知stderr不泄漏、不猜auth；成功路径无额外whoami或重复列表。
- 353 passed / 97.77% 分支覆盖及静态检查通过；真实native stub全host loopback，只验证缓存路由，不执行控制。
- 新client在隔离生产会话副本上做一次vehicles和每车一次battery，两车BMS可解析；源文件哈希不变、cache 0600、production writes/control/image downloads均0。
- 准确main CI/发布以GitHub release notes为准。控制策略与native engine入口尚需继续。

## b12：原生控制Button与小型诊断

- 增加engine_start/engine_stop正常Button和control_availability本地ENUM Diagnostic，
  不映射Lock；根据明确controls/allowlist提升INTEGRATION禁用，USER禁用保留。
- 状态与四个动作属性完整中英翻译、标准图标，无raw大对象或新云轮询需求。
- 固定native loopback测试四种控制，发现REST控制无cache仍发送，修正CLI/BMS
  缓存契约不能泛化到REST控制的说明；不因此放行未知权限。
- 366 passed / 97.80%分支覆盖，静态检查通过。仅fake已验证capability可调用
  HA Button.press并回读状态，未知真实权限仍拒绝；实车控制和生产写入均0。
- 准确main CI/发布以GitHubrelease notes为准，真实控制策略未完成。
