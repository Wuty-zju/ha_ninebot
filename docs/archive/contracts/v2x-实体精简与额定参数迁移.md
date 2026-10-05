# b24：实体精简、额定参数与配置修复

> 历史归档：保持原版本/证据范围，不继续追加。现行行为先读[CURRENT_STATE](../../agent/CURRENT_STATE.md)和对应主题contract，不能直接执行本文旧goal或“未来”清单。

日期：2026-10-05。开发基线b23；只使用现有源码、fixtures和隔离HA测试，不采集车辆数据。

## 设计决定

SOC是量化的云端状态，额定V×Ah不等于实际可用容量。用其变化累计充放电量容易受
缺报、换电、SOC校正影响；日/月/总计和逐样本量重复暴露，参数generation又累积
不可用实体。删除这套模型、采样/跨日桶/累计Store逻辑与质量实体；不再提供推算电表。

保留可选额定电压、额定容量配置及**一个稳定ID的额定能量**（V×Ah/1000 kWh）。
这表示用户输入的额定参数，不是SOH、充电电表或实测容量。旧enable_estimation选项
键兼容保留但界面改为“电池额定参数”；旧energy_v2文件只迁移V/Ah，不迁移旧累计值。
参数改变更新同一实体，不产生generation。额定参数不需要额外SOC/BMS请求或午夜写盘。

| 原实体/数据 | 新表示与处置 |
|---|---|
| estimated_*_v2_gN、estimation_quality；v1估算能量/功率 | 移除，额定能量改用稳定battery_rated_energy；不继续创建过时generation |
| endurance、range_estimated、range_ai | 一项剩余续航，优先明确返回的precise，再estimated，再AI；属性说明来源，三值仍保留在调试视图 |
| vehicle_lock_raw | 移除重复编码实体，保留锁状态binary和调试状态 |
| device_name、sn | 移除重复sensor，放Vehicle DeviceInfo的name/serial_number |
| cycle_raw与bms_cycles | 一项循环次数；支持未确认时unknown，报告计数及支持标志放属性，不伪称已支持 |
| 电量分层原值、上游battery_count | 主SOC与实际返回pack count实体；层级差异、上游计数放属性/调试视图，不重复建SOC实体 |
| 月返回明细数 | 放月列表覆盖率属性和Action，不再单独建重复实体 |
| shared/version/type/support/permissions、续航偏好、充电时间戳等协议字段 | 不再直接建低价值实体；保留有界内存/raw schema/安全调试与控制能力模型 |
| 电池存在、座桶锁、ACC、智能服务信息 | 保留有用观测；名称无“原值/raw”，未知编码仍明确未解释，不伪造枚举/单位 |
| 云端Wh行程能耗、W充电功率、健康评分、行程时间/速度、历史Actions/event | 保留原功能、ID与语义；健康评分不改成SOH |

这是有意的实体收缩。使用已移除实体的自动化需要用户调整；不改写Recorder历史、
统计数据库或用户自定义名称。清理只限本config entry独占车辆、精确的已审阅ID；
generation采用完整匹配，不按任意estimated前缀或名称模糊删实体。未知/共享归属不删。

## model_vehicle修复

旧SelectSelector只接受实时发现列表里的SN，提交显示名称或列表变化时会在进入
flow前报“not a valid option”。不能把允许任意文本作为修复。改用HA DeviceSelector，
提交设备注册表ID，再验证设备唯一账户归属、非child、存在、fresh和当前entry。
选择错误/车辆过期显示本地化字段错误，不崩溃、不把配置应用给另一辆车。
对旧flow直接传SN仅作严格的已发现同账户兼容，不支持按名称猜车辆。

参数提交采用批量校验后一次提交；损坏或未来格式的可选参数Store不覆盖、不阻断
云端遥测，提供Repair并禁止参数写入。两个Number和Options共用一份参数存储。

## 命名与语言

保留仍有意义实体的unique_id/translation_key，只修改翻译展示名；未知字段的证据
限制放属性和文档，名称不带“原值/raw/experimental”。英文strings与en一致，简中
完整；增加繁中及主流语言的实体、设置和常用动作翻译，缺少的长帮助/异常说明使用
英文兜底并明确记录范围，不以复制英文宣称全部语句已人工翻译。保留占位符结构。

## 稳定性与效率

删除SOC模型依赖的强制轮询、每次sample的Store写入和午夜计时；保留per-group
freshness/backoff、互斥、partial failure、manual刷新/reauth/控制回读与账户边界。
额定能量使用profile上下文，不额外拉云数据。调试视图不重复读取大raw，不增加请求。

## 验收

- 用真实HA selector验证设备ID、旧SN、显示名称、另一账户和过期车辆的提交路径。
- 参数Store v2→v3仅V/Ah、损坏/未来格式不覆盖；额定实体改参数后ID稳定。
- 升级精确清理各generation及冗余实体；失败首刷、共享设备和未知ID不误删。
- 保留遥测、SMS/历史Action、安全门禁、用户命名和Recorder边界。
- 翻译key/占位符、实体名称、Ruff/format/mypy；相关离线回归及一次最终综合测试。
- 分支合入main后核对实际HACS/Hassfest/Checks，通过后发布v2.0.0b24 prerelease。

实际实施/检查结果在完成后补入本页；本方案不授权执行任何车辆控制。

## 当前实体数量与回滚

无旧身份别名的新安装、单匿名电池且cycle支持false的标准fixture，默认实体从77减至47；
启用额定参数时从原90减至50。实际数量随多电池/历史身份而变，这不是实车在线清单。
用户自定义名称优先，更新翻译不会强行改用户的entity_id。

参数payload升级到model_version=3，只保存V/Ah。回滚到b23或更早时须恢复升级前的
参数Store备份；旧版本不识别v3。Recorder数据库没有改写，旧估算历史不作为新额定容量
统计续用。

当前21种语言：en、zh-Hans、zh-Hant、de、fr、es、it、pt、pt-BR、ru、ja、ko、nl、pl、
tr、ar、hi、id、vi、th、uk。所有实体展示名和主要设置/查询动作名称已本地化；简中/英文
完整，其他语言部分长说明与异常采用英文兜底。词汇源在scripts/localization_labels.json，
生成工具仅用于开发；安装后运行只依赖集成目录中的translations。

参考：[HA自定义集成本地化](https://developers.home-assistant.io/docs/internationalization/custom_integration/)、
[实体注册表与用户命名](https://developers.home-assistant.io/docs/entity_registry_index/)。

## 实施与本地验证结果

已完成上述精简、固定额定身份、DeviceSelector、读旧V/Ah、不阻断遥测的可选Store
及拓扑缓存。最终HA2026.1.0/Python3.13.16综合离线回归464通过，覆盖率96.66%，
10.71秒；Ruff/format、43个源文件mypy通过。第一次综合回归有两项旧实体数量/
旧返回计数实体的断言未更新，修正到新的实体/属性契约后最终全部通过。
真实云查询/SMS/控制/生产写入为0；未重跑多HA版本矩阵。CI发布检查另以实际结果为准，
不把本地测试冒充HACS/Hassfest。见[验证记录](../../evidence/v2x-b24-validation.json)。

PR源码提交9bb62f3的Checks lint、Hassfest、HACS均实际通过，完整链接记录在验证JSON；
单独翻译模块5项通过，已消除测试收集顺序依赖。合并后main发布检查仍以对应提交为准。
