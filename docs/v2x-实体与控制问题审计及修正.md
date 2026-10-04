# v2.x 实体与控制问题审计及修正

2026-10-04，用户安装 b8 后反馈的下一轮重构，不把此前保守设计当成不可改变的框架。
用户明确要求删除旧无效实体、取消实验界面、以 ninecli I/O 重新设计功能；该要求
覆盖此前“无效实体保留占位”的策略。有效量的身份/单位不随意改写。

## 本机只读现状与根因

源码基线 main aa68b4134d8f，生产HA为2026.10.0b0，安装插件2.0.0b8；一个
配置条目、两辆车。只读取manifest/版本、Ninebot条目的结构和实体登记、有限日志。
不读取/公开账号、会话值、真实unique_id或位置，不修改生产配置/registry/database。

56个登记实体：sensor36、binary_sensor6、tracker2、image2、lock2、button6、
event2；26个启用，30个integration-disabled。options为空。当前/轮转日志中
Ninebot相关行仅4条Warning（含1条deprecated），未出现Ninebot相关Error。
登记状态不等于当前在线state；没有据此宣称完整实时云查询通过。

| 用户问题 | 当前代码/登记证据 | 判断与后续处理 |
|---|---|---|
| 鸣笛/座桶灰色 | 两类button均已登记启用；options没有enable_controls/allowlist；control_decision还要求实际parser永远未提供的support/permission/evidence | 双重本地阻止，不是已发现云端动作失败；重新审查本地dispatch条件与上游执行权限的区别，不能把null臆作允许或明确拒绝 |
| 远程开机无法点击 | 当前仅有Lock实体；async_lock/unlock固定抛engine_lock_unverified | 对用户暴露无法履行的交互模型；移除Lock，用ninecli原命令对应的启动/关闭按钮独立设计，不混作开锁 |
| 隐藏实体很多 | 新最近行程、普通/AI续航、图片、raw diagnostics默认禁用，用户禁用标志与集成默认不同 | 不能把所有hidden解释为故障；重新分类默认启用的可靠当前量、opt-in位置、诊断raw和功能依赖，升级只调整integration-disabled，不覆盖USER禁用 |
| 实验名称/设置说明 | 中英strings明确写experimental；旧Lock额外属性含实验控制标志 | 普通功能名称与能力/限制说明分开，不把实现限制塞进每个实体名称 |
| 不规范图标 | 原icons只覆盖最近行程/Event/Actions，多数按钮没有语义图标 | 保持正式device class默认图标，缺class的功能补规范MDI图标与翻译键 |
| 旧无效实体 | LegacySensor专门返回None、旧满电续航参数不参与模型；旧Lock无法执行 | 删除已审核名单的无效登记和创建代码，不把无数据的有效实体一概删除 |

## 第一步 b9：废弃实体与界面模型清理

registry.py集中列出20个废弃sensor key、2个Lock key和1个无用number key。
仅成功首次刷新之后，用HA公共registry API删除当前entry独占车辆设备上的
精确唯一ID匹配；不按实体显示名、suffix模糊匹配、nickname或账号猜测。
平台必须为本集成，设备必须具有单一Ninebot标识且无共享entry。未知/无设备/
其它平台/其它账号/复合身份不删。重复执行幂等；失败setup不删。

删除 LegacySensor 与整个不可操作Lock平台，不再创建deprecated占位；未使用
battery_max_range参数不再生成。二元锁状态仍有效，真实status/BMS/行程/估算
模型的当前实体保持身份、用户命名与统计含义。诊断只公开移除数量。无直接SQL
删除，原始开发期间仍只读生产HA；新代码安装后会执行已授权的registry清理，
原自动化引用会失效，需要升级备份/改用有效实体，不能宣传完全无破坏变更。

普通鸣笛/座桶、控制开关/允许车辆使用正常中英名称，range/trip/raw/count/button/
模型输入补语义icons。该步骤不声称控制已修复、不执行实车动作；门禁改造与
engine按钮为下一独立阶段，避免删除误导Lock与放开真实控制混在一次修改中。

## 后续仍属于本目标的工作

1. 完整核对ninecli固定版本与最新版help/REST/JSON、已有脱敏样本和必要最小
   新只读样本。区分CLI软件功能、车辆能力、账户权限与物理动作完成；研究
   missing permission字段应由何种证据/云响应判定，避免不可满足的本地条件。
2. bell/buck/engine-start/stop直对应Button或device-targeted Action；普通名称、
   明确用户授权/车辆路由、认证与失败翻译、超时不重发、执行后回读，不把
   engine-start当unlock。不自动试运行真实动作。
3. 可靠当前/最近行程值改善默认可见性；保留USER禁用，诊断原始量仍不猜单位。
   评估真实status/BMS/profile元数据，动态实体必须有明确schema语义和来源。
4. 原始未知数据以有界RawStore/安全诊断或按需debug响应提供；若采用debug实体，
   只允许小体积审核后的scalar/schema摘要。完整raw JSON、账号/token、GPS轨迹、
   私人地址和未知敏感字符串不放state/recorder，不能把安全隐私当作“未适配”
   借口无限丢弃业务信息，也不能为了所有字段可见而dump全部payload。
5. 每阶段必要受影响测试与一次发布门槛，最低/稳定/beta准确提交CI、Hassfest/HACS，
   合入main递增prerelease。此文是持续修正入口，b9清理不等于整个目标完成。

以前的设计/字段清单/契约保留其证据日期；本轮真实结论独立追加，不把合成控制
测试或此前匿名图片HEAD冒充实车开关机授权/完整云端支持。

## b10：正常实体可见性

普通/AI续航、最近行程六类实体（距离、时长、起止时间、最高/总平均速度）
和车型图片默认启用。旧 registry 中只有 disabled_by=INTEGRATION 的明确匹配项
会升级为启用；USER 禁用始终保留，独占设备/唯一标识/当前车辆存在检查复用
清理边界。不改 unique_id、entity_id、自定义名称、时间单位或统计语义。

GPS、事件、控制、估算和未确认单位的 raw 诊断仍需按用途启用。未返回值仍
为 unknown/unavailable，默认显示不伪造数据。最近行程共用 travel 定时刷新及
上月补充，不增加 detail 查询；全部 travel consumers 被用户禁用且无内部需求时
仍停止周期查询。图片继续使用审核的公开来源和 HA 按需缓存，不在轮询时下载。

该阶段不宣称控制问题已解决。当前真实只读 status 再次返回 permissions=null；
它不是允许证据，控制策略需要明确处理本地预验证与云端最终鉴权的边界。
