# 车辆、实体身份、电池与刷新

现行基线：b24；所有创建实体默认可见/启用，位置、控制、调试、额定参数仍有功能opt-in。
用户主动禁用/隐藏、名称、实体ID、Recorder历史不被改写。本页维护本主题；全字段表另见[FIELD_INVENTORY](../reference/FIELD_INVENTORY.md)。

## 表达与精简

| 数据 | 当前HA表示/语义 |
|---|---|
| SOC/range/锁/充电/电源 | 当前状态；range一项，precise>estimated>AI，来源属性及调试三值 |
| Voltage/Temperature/Cycles | BMS测量；循环须明确support，不把unsupported返回100当真值 |
| Wh/W/score | 云端能耗/充电功率；评分非SOH，不标百分比健康度 |
| 月次数/时长/里程/能量 | 服务端聚合；月内修正/单调性不确定，不能强设TOTAL_INCREASING |
| LastRide时间/时长/距离/max/avg | timestamp/duration/distance/speed正确定义，见[TRAVEL](TRAVEL.md) |
| 存在/座桶锁/ACC/智能服务 | 有用有限观测，未知编码明确未解释，不猜连接/权限 |
| 协议类型/权限/计数/重复电量/偏好 | metadata、bounded runtime/安全调试；不为完整清单造重复entity |
| 额定V/Ah | 用户规格；唯一稳定battery_rated_energy=V×Ah/1000 kWh，非实测容量/电表 |

SOC累计充放电/质量/generation模型已退役；不恢复旧estimated_*实体、午夜采样、额外模型轮询或重复续航。
旧energy_v2只迁V/Ah，model_version=3；损坏/未来可选Store保留原样、Repair且禁止写参数，遥测继续。
model_vehicle用DeviceSelector提交设备注册表ID，再验本账户唯一归属、非child、存在/fresh；旧SN仅严格同账户兼容，不能名称猜测。
仍有意义的unique_id/translation_key稳定；has_entity_name、DeviceInfo与翻译组合命名。
21语种主要实体/设置/Action名称，中英完整，其它长帮助/异常部分English fallback；占位符与strings=en检查。

## 身份与迁移

清理只对本entry独占车辆、明确审阅过的obsolete IDs；generation完整匹配，未知/shared归属不删除。
不将旧估算历史复用为额定容量统计，不编辑Recorder/生产.storage。
迁移用原SN/legacy aliases，nickname不作identity；用户自动化引用已删实体需自行调整。
回滚须配置/参数Store与代码配套备份，旧版不识别v3；不要手改数据库/token JSON降级。

BMS单包车辆测量仅唯一上报pack时取值；多包按稳定身份匹配，不擅选第一行。
明确battery_sn/sn是既有/合成alias，真实样本中稳定多包身份尚未证明。
有身份的车辆内pack key沿用battery_<identity hash>_<key>；缺身份/冲突/多匿名不能把别的包历史接上。
当前电池sensor仍挂vehicle device，无child注册。compat集中feature detection，不散落版本字符串判断。
可移动物理电池未必适合不可reparent的child；新增pack device前须稳定身份、组件关系、迁移/降级fixture与备份评审。

## 图片与GPS

ImageEntity使用HA HTTP/缓存；审核HTTPS/443、固定允许域名/图片路径，移除唯一已审阅签名参数，未知来源拒绝。
禁止redirect和URL/异常正文日志；安全URL不变保留image cache，改变/消失才清理。没有自建任意URL下载器。
tracker需coordinates opt-in及双坐标有效；可参与Map/Zones，CRS未知不转换，loc.acc不当定位精度。
诊断无精确坐标/trail，常规实体不放路线；查询轨迹遵循TRAVEL的额外include_track授权。

## 刷新与请求依赖

默认status120s、battery/travel600s、profile3600s；允许options调周期，不是官方rate limit承诺。
多车按组独立freshness/backoff/jitter/partial failure；账号backend串行，过期/跨月本地通知不增加云请求。
需求来自typed coordinator contexts；禁用实体不注册消费者，不以无类型audit listener请求全部组。

| 消费者 | 周期需求 |
|---|---|
| SOC/range/锁电充/GPS、controls | status |
| BMS测量 | battery |
| 月汇总 | travel；无需额外上月fallback |
| LastRide/Ride Event | travel及已有规则的上月fallback；不轮询详情 |
| Image/额定参数/refresh | profile基础发现；额定模型无SOC/BMS强制需求 |
| 历史Action | 单次按需，无永久历史扫描需求 |

新车bootstrap和首次BMS失败按已有退避发现；空/多匿名无法表示的inventory保留每小时稀疏探测。
已有可表示BMS被用户全部禁用则停止常规请求，不以旧SOC estimator保持需求。需求激活不重置next_due强刷。
控制回读/manual refresh是有界显式请求，不能为一个字段反复云端poll。

## 维护

身份/迁移/参数/selector：registry/migration/entity_simplification/debug_and_model_options回归。
图片/位置/依赖：image_urls/current_observations/demand/coordinator回归；主要用isolated HA与录制fixture。
原决策见[实体精简](../archive/contracts/v2x-实体精简与额定参数迁移.md)、[包身份](../archive/contracts/v2x-电池身份与设备模型契约.md)、[图片依赖](../archive/contracts/v2x-图片位置与请求依赖契约.md)。旧SOC/default-off条款仅历史，不继续维护这些独立版本稿。
