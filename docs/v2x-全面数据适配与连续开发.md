# v2.x 全面数据适配与连续开发

> 接手先读[CURRENT_STATE](agent/CURRENT_STATE.md)：原Phase/A–F主体已完成；本文是时间基线和设计依据，不是全部待开发任务。当前增量方向另见[NinePlus专项](NinePlus生态源码审阅与ha_ninebot数据解析应用方案.md)。

2026-10-05，始于b19，当前整理至b23。私有原始数据及设计资料不进入仓库；
公开fixture仅保存去身份、去位置、去签名后的业务结构。原先开发文档保留其
日期基线；本页为后续A–F阶段的公共实施入口。

## 持续开发路线

| 阶段 | 内容 | 当前状态 |
|---|---|---|
| A | 全部创建实体默认可见、Wh/W、评分与原值、月次数和总时长 | b19实现；实际验证见版本证据 |
| B | 经校验的每日里程图表、月聚合/行程覆盖率、Action兼容契约 | b20实现 |
| C | 初始/Options估算参数与调试开关、每车有界格式化解析视图 | b21实现 |
| D | 两步SMS登录、同账户reauth/reconfigure、候选会话取消与提交 | b22实现；真实短信未测试 |
| E | 有界跨月查询、续查游标、历史覆盖统计和展示示例 | b23实现；索引仅内存，非上游完整历史 |
| F | 功能/迁移/隐私收尾、缺报与未知语义说明、阶段综合回归 | b23完成本轮综合离线验证 |

完成情况见[实施记录](v2x-实施与验收记录.md#b19b23af-连续开发完成记录)。
此表不是未来再次执行A–F的任务清单；未确认语义、全历史缺口及真实测试边界仍保留。

ninecli保持生产backend，精确pin和最低HA保持原值。生产HA只读，实机研究
只选一辆明确授权车辆；主要使用脱敏录制fixture、合成边界数据和fake server。
每个完成阶段/小版本合入main并发布下一个prerelease，针对性验证小改动，
重要功能边界集中综合验证，不重复为版本号重跑完整套件。

## A：状态、单位与正常启用

`observations.py`集中声明可进状态的已审阅标量路径，不允许任意path或无限
自动创建实体。字段类型和null/absent在有限模型中保留；对象、超长/控制字符
字符串、非有限数等不进入普通sensor state。手机号、账号、邮箱、MAC、
蓝牙秘钥、个人资料、签名URL、精确坐标没有进入这个观测白名单。

- `month_energy_raw`、`last_energy_raw`保留原unique ID和数值，metadata为Wh/
  ENERGY；`charging_power_raw`保留原ID，为W/POWER/MEASUREMENT。Wh/W依据
  维护者确认；月/单次量未设累加state_class，不冒充墙端充电电表。
- 本月骑行次数和总时长来自服务端times/duration，不用已取得的部分明细相加。
  月次数无累加class，月时长DURATION/s，历史查询不覆盖当前月。
- 存在、座桶锁、ACC、服务标志/剩余天数、续航选项、支持/权限、类型/计数等
  编码先用原值diagnostic；未知不变成“允许”“已过期”或猜测的物理状态。
- 电池健康评分无%/SOH；循环计数原值和cycle-support分别显示，support=false
  不被raw值覆盖。主字段电量不覆盖车辆SOC；接口计数和真实返回行数分开。
- 单匿名pack挂车辆；多包只为明确身份创建分包测量，保留既有稳定ID及顺序
  独立取值。未知多包不擅选第一行、不伪造child identity。
- 充电时可能仍返回空remaining文本和时间戳0。文本sensor报告unknown，附
  not_reported/原文本；timestamp原值保留0，不建立1970或虚构倒计时。

全部**创建**实体默认启用、不默认隐藏。registry仅针对明确属于本账户、
现存车辆的有效工厂keys/legacy aliases解除INTEGRATION默认禁用/隐藏，
保留用户名称、unique_id、entity_id及USER禁用/隐藏；不处理其它账户/shared
设备或不认识的key。估算实体仍仅在估算功能开启时创建。

控制和位置的显式opt-in仍是执行/输出门禁；显示控制按钮不自动允许发送，
coordinates=false的可见tracker不输出经纬度。Ride Event现在默认启用，但
重启仍建立baseline，不把历史最近一次当作新事件；用户主动禁用仍停订阅。

Energy/Power metadata更新不会缩放或重写历史数据库。真实数据重放验证非
充电0W/充电非零W、零分、支持false+原计数、空文本以及不同电量层级。
HA中已有用户单位override或旧statistics异常应按其配置处理，不能偷偷改数据库。

## 后续强约束

月图表返回每日序列走Action，不造每天一个entity。上游报告的总次数与返回
列表数量分开；月列表缺页不能冒充全历史。跨月扫描完不等于取得全量rides。
剩余时间正式Duration只在获得已证实非空格式后加入，不阻塞当前缺报适配。
调试模式从已有snapshot派生，不新增云请求，不把完整raw/历史/轨迹塞attributes。
SMS等待输入时不持有全局session锁，不把验证码写入ConfigEntry、argv或日志。

每阶段保持独立可review/revert的提交，配套中英翻译、icons、针对性离线测试。
发布记录说明真实运行的检查范围；历史完整矩阵不冒充当前版本的新结果。

参考：[Sensor官方API](https://developers.home-assistant.io/docs/core/entity/sensor/)、
[Registry禁用语义](https://developers.home-assistant.io/docs/entity_registry_disabled_by/)、
[分级验证](v2x-分级测试与预发布策略.md)、
[历史Actions契约](v2x-历史查询Actions契约.md)。
