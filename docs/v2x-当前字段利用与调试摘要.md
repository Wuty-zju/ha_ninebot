# v2.x 当前字段利用与调试摘要

> 2026-10-05组织说明：本文保留形成时版本；当前实现/默认/精简见[CURRENT_STATE](agent/CURRENT_STATE.md)与[b24契约](v2x-实体精简与额定参数迁移.md)。日表与day_total的后续证据见[NinePlus第11节](NinePlus生态源码审阅与ha_ninebot数据解析应用方案.md#11-每日里程历史查询与-cachestorage)。本文B类/“默认关闭”是历史分类，不是恢复禁用实体的要求。

> b17更正：车辆发现完整性和每车profile freshness为本地元数据，不是新raw字段；
> 当前136业务路径及12命令代理路径不变。部分列表不再作为解绑证据，见
> [发现契约](v2x-不完整车辆发现与缓存恢复契约.md)。

> b16后续更正：接口接受/错误与回读结果仅进入全局64条有界私有诊断缓存，
> 不成为物理完成标志或新云字段；派生字段单列于[回读契约](v2x-控制结果与状态回读契约.md)。
> 原始业务136路径、命令代理12路径及未知单位边界不变。本轮无新真实请求。

> b15后续更正：新的明确授权允许本地检查通过后由云端最终鉴权，UNKNOWN
> 不再阻止命令发送，也不变成ALLOWED。业务路径仍136条；另有四条命令的
> 12条选定代理路径，分开计数与分类。实际接受为空data，不是b14合成accepted
> 标记。见[控制实测契约](v2x-云端鉴权控制与实测契约.md)。

> b14后续更正：status明确返回的sn现已在raw/telemetry更新前检查请求归属；缺失/null不冒称已核验。原b13审计基线保留，当前机器用途列与[新契约](v2x-原生控制加密与状态归属契约.md)为准。

> 2026-10-04，当前实现b14；实现基线 b13 main `2135b62bfe267c306bf23d5982661a077dac9bca`。
> 本轮仅离线开发，不新增九号云查询，不执行真实控制，不修改生产 HA。

## 1. 当前清单与历史证据的关系

[主设计](ninecli原始数据完整利用与后续逐级开发方案.md)和
[原始97路径清单](evidence/v2x-field-inventory.json)保留最初固定基线，不反向改写。
[当前字段利用清单](evidence/v2x-current-field-usage.json)合并九份 recorded sanitized
payload 和选定的图片字段 shape：共 **136路径、122非容器字段**，含后续非空行程
和详情。按 endpoint 为 vehicles 41、status 25、battery 21、travel 23、trip_detail 26。
记录值中的身份、时间和位置已脱敏替换，不能据 fixture 数值宣称实车语义。

R=已有真实只读shape/关系；S=当前源码或固定ninecli显示契约；F=脱敏重放/合成
验证；V=待验证。原清单的81项别名/候选仍单列，未提升为真实已观察字段。
静态字段清单可记录已核实字段名，运行时diagnostics只允许审核过的名称，不导出
任意未知key；下表中的I不表示全部字段名称都会在当前运行时diagnostics中出现。

## 2. 调试入口：小摘要与大原始数据分开

新增每车可选诊断 sensor，稳定key `raw_data_summary`，中英名称“原始数据摘要”/
“Raw data summary”，b13形成时默认关闭；b24创建默认可见，仍须用户开启调试功能。状态为该车有界内存cache记录数，四个属性只有整数：

| 属性 | 定义 |
|---|---|
| schema_path_count | 此车已保留记录中，按endpoint去重的已审核schema路径数；不是所有原始字段数 |
| unknown_field_count | 已保留记录中尚未批准进入schema摘要的字段出现次数；含未知字段与已知但隐私受限字段，不是唯一字段数，也不表示错误 |
| redacted_field_count | 已保留记录中主动移除敏感值的字段出现次数 |
| retained_bytes | 此车raw内容及schema元数据保守预算字节数；不是进程总内存 |

摘要不包含字段名、值、账号、车辆/ride ID、图片URL、轨迹、token或错误原文。
账户级 vehicles 列表不计入某一车辆；历史month/detail可计入，但过期detail被移除。
记录数非零不表示云端最新或完整，freshness仍由既有coordinator/diagnostics判定。
实体仅观察现有cache，不增加status/battery/travel需求；不设置物理单位或统计类。
图标使用integration内官方icon translations，兼容旧HA；不要求icon出现在state属性。

完整raw仍仅在有界内存：8MiB全局预算、128记录、8详情/900秒及原有单响应/结构
限制。完整字段shape通过原有diagnostics下载；仅审核名称/类型，不含业务值。
不能用“debug实体”绕过隐私边界，也不把未知大对象、GPS或每ride塞入recorder。
该设计遵循[HA Sensor属性只读内存要求](https://developers.home-assistant.io/docs/core/entity/sensor/)。

## 3. 当前实现对历史描述的更正

- 形成时普通/AI续航分别显示；b24已合并为单一续航并精简估算/重复raw实体，诊断类别不等于默认禁用。功能opt-in与用户主动禁用仍保留。
- businessType/vehicle_type没有被用来推断业务线或控制权限；真实业务路由缓存由原生CLI产生。
- b14检查明确返回的status.sn与请求车辆是否匹配；缺失/null仍不能宣称“返回SN已证明”。两车已有记录关系已核对，全车型语义仍需证据。
- vehicle_name_zh目前不参与车型fallback；优先en再vehicle_name，不宣称按HA语言切换车型。
- 所有行程详情只用于显式历史查询，不静默改变当前最近行程sensor或重新发事件。
- 当前null权限不会自动允许控制；b12正常名称/图标/状态解释不是“实车控制已恢复”。

## 4. 原始字段到当前HA用途

A=正式默认Entity，B=默认关闭Entity，C=诊断Entity，D=Event，E=Action response，
F=设备元数据，G=子设备候选元数据，H=私有runtime，I=安全schema研究，J=暂不使用/移除值。
当前分类以本表为准；默认、单位、device/state class的详细机器列见同名JSON。
对于历史行程，A/D表示只选最新完成summary用于实体/事件，并不每条建实体。

以下统一表在2026-10-05组织时按b24机器用途及已完成NinePlus专项的增量更正同步，替代旧b14用途列；原表仍在Git历史中。字段形状不新增，机器evidence保持日期基线。
默认策略以b24契约为准；表中B是既有历史类别，并不要求自动禁用实体。单位/默认/device class的机器列仍须核对其版本。

| endpoint | raw path | 类型 | 含义/单位及限制 | 当前用途 | 分类/兼容 |
|---|---|---|---|---|---|
| vehicles | `$` | list | 结构容器；仅记录 shape，不创建 Entity / — | 有界 runtime raw（隐私字段主动移除）；静态字段清单。只有已审核名称进入运行时 schema，未知语义不创建实体。 | H/I / C0 |
| vehicles | `$[]` | dict | 结构容器；仅记录 shape，不创建 Entity / — | 有界 runtime raw（隐私字段主动移除）；静态字段清单。只有已审核名称进入运行时 schema，未知语义不创建实体。 | H/I / C0 |
| vehicles | `$[].active_date` | str | 激活时间/枚举；格式/语义 V，不创建时间实体 / — | 默认启用原值诊断实体：activation_time_raw；不猜单位/枚举/权限。 | C / C0 |
| vehicles | `$[].active_uid` | str | 用户身份；无需 HA 状态表达，值从公开输出删除 / — | 有界 runtime raw（隐私字段主动移除）；静态字段清单。只有已审核名称进入运行时 schema，未知语义不创建实体。 | J/I / C0 |
| vehicles | `$[].actived` | int | 激活时间/枚举；格式/语义 V，不创建时间实体 / — | 默认启用原值诊断实体：activation_status_raw；不猜单位/枚举/权限。 | C / C0 |
| vehicles | `$[].and_mac` | str | 平台相关 MAC；个人设备标识，不导出或作为已验证连接 / — | 有界 runtime raw（隐私字段主动移除）；静态字段清单。只有已审核名称进入运行时 schema，未知语义不创建实体。 | J/I / C0 |
| vehicles | `$[].auth_date` | str | 授权时间；格式/有效期关系未确认 / — | 有界 runtime raw（隐私字段主动移除）；静态字段清单。只有已审核名称进入运行时 schema，未知语义不创建实体。 | H/I / C0 |
| vehicles | `$[].auth_email` | str | 个人资料；不用于 Entity/diagnostics/debug export / — | 有界 runtime raw（隐私字段主动移除）；静态字段清单。只有已审核名称进入运行时 schema，未知语义不创建实体。 | J/I / C0 |
| vehicles | `$[].auth_nickname` | str | 个人资料；不用于 Entity/diagnostics/debug export / — | 有界 runtime raw（隐私字段主动移除）；静态字段清单。只有已审核名称进入运行时 schema，未知语义不创建实体。 | J/I / C0 |
| vehicles | `$[].auth_phone` | str | 个人资料；不用于 Entity/diagnostics/debug export / — | 有界 runtime raw（隐私字段主动移除）；静态字段清单。只有已审核名称进入运行时 schema，未知语义不创建实体。 | J/I / C0 |
| vehicles | `$[].auth_uid` | str | 用户身份；无需 HA 状态表达，值从公开输出删除 / — | 有界 runtime raw（隐私字段主动移除）；静态字段清单。只有已审核名称进入运行时 schema，未知语义不创建实体。 | J/I / C0 |
| vehicles | `$[].ble_name` | str | 蓝牙名称；非 connectivity 状态，不作为身份 / — | 仅车辆名缺失时作 profile.name fallback；不当连接状态；raw中移除值。 | F/J / C0 |
| vehicles | `$[].blue_secret` | str | 蓝牙凭据；禁止保存到新 raw cache/diagnostics，保留已遮蔽形状 / — | 有界 runtime raw（隐私字段主动移除）；静态字段清单。只有已审核名称进入运行时 schema，未知语义不创建实体。 | J/I / C0 |
| vehicles | `$[].businessType` | int | 车型/业务枚举；路由和支持矩阵，不能转权限 / — | 默认启用原值诊断实体：business_type_raw；不猜单位/枚举/权限。 | C / C0 |
| vehicles | `$[].color` | str | 颜色；稳定后可作 metadata，枚举含义待核 / — | 保留 raw；颜色枚举尚未映射到设备元数据。 | H/I / C0 |
| vehicles | `$[].common_user_permissions` | NoneType/null | 真实 null；权限仍为未知，不推断允许或位图。显式启用后的命令交由云端最终鉴权。 / — | 默认启用原值诊断实体：shared_permissions_raw；不猜单位/枚举/权限。 | C / C0 |
| vehicles | `$[].common_user_vehicle_index` | int | 共享列表索引；不能作稳定车辆 ID / — | 默认启用原值诊断实体：shared_vehicle_index_raw；不猜单位/枚举/权限。 | C / C0 |
| vehicles | `$[].common_user_version` | NoneType/null | 支持/版本候选；真实 null，禁止猜 bit mask / — | 默认启用原值诊断实体：shared_version_raw；不猜单位/枚举/权限。 | C / C0 |
| vehicles | `$[].device_name` | str | 用户车辆名；Device name，诊断名实体保持 / — | DeviceInfo.name；sensor.device_name（诊断默认关闭）；原值不进入raw/schema diagnostics。 | F/C / C0 |
| vehicles | `$[].img_url` | str | 车辆车型图片；light 优先、img fallback；签名查询移除 / — | image.vehicle + 安全 device/tracker picture；只接受审核过的公共 HTTPS URL | A/F / C0 |
| vehicles | `$[].ios_mac` | str | 平台相关 MAC；个人设备标识，不导出或作为已验证连接 / — | 有界 runtime raw（隐私字段主动移除）；静态字段清单。只有已审核名称进入运行时 schema，未知语义不创建实体。 | J/I / C0 |
| vehicles | `$[].is_common_user` | int | 共享用户标志；不是操作授权许可 / — | 默认启用原值诊断实体：shared_user_raw；不猜单位/枚举/权限。 | C / C0 |
| vehicles | `$[].is_img_special` | NoneType/null | 图片标志；null 未确认 / — | 默认启用原值诊断实体：special_image_raw；不猜单位/枚举/权限。 | C / C0 |
| vehicles | `$[].latest_support` | NoneType/null | 支持/版本候选；真实 null，禁止猜 bit mask / — | 默认启用原值诊断实体：latest_support_raw；不猜单位/枚举/权限。 | C / C0 |
| vehicles | `$[].loc_delay_time` | NoneType/null | 位置延迟候选；null，单位未知，不作 freshness / — | 默认启用原值诊断实体：location_delay_raw；不猜单位/枚举/权限。 | C / C0 |
| vehicles | `$[].owner_user_area_code` | str | 个人资料；不用于 Entity/diagnostics/debug export / — | 有界 runtime raw（隐私字段主动移除）；静态字段清单。只有已审核名称进入运行时 schema，未知语义不创建实体。 | J/I / C0 |
| vehicles | `$[].owner_user_avatar` | str | 个人资料；不用于 Entity/diagnostics/debug export / — | 有界 runtime raw（隐私字段主动移除）；静态字段清单。只有已审核名称进入运行时 schema，未知语义不创建实体。 | J/I / C0 |
| vehicles | `$[].owner_user_id` | str | 用户身份；无需 HA 状态表达，值从公开输出删除 / — | 有界 runtime raw（隐私字段主动移除）；静态字段清单。只有已审核名称进入运行时 schema，未知语义不创建实体。 | J/I / C0 |
| vehicles | `$[].owner_user_nickname` | str | 个人资料；不用于 Entity/diagnostics/debug export / — | 有界 runtime raw（隐私字段主动移除）；静态字段清单。只有已审核名称进入运行时 schema，未知语义不创建实体。 | J/I / C0 |
| vehicles | `$[].owner_user_phone` | str | 个人资料；不用于 Entity/diagnostics/debug export / — | 有界 runtime raw（隐私字段主动移除）；静态字段清单。只有已审核名称进入运行时 schema，未知语义不创建实体。 | J/I / C0 |
| vehicles | `$[].smart_service_surplus_days` | int | 智能服务剩余天数候选；确认后可禁用诊断，当前 runtime / 待验证（名称提示天） | 默认启用原值诊断实体：service_remaining_days_raw；不猜单位/枚举/权限。 | C / C0 |
| vehicles | `$[].support` | NoneType/null | 支持/版本候选；真实 null，禁止猜 bit mask / — | 默认启用原值诊断实体：support_raw；不猜单位/枚举/权限。 | C / C0 |
| vehicles | `$[].total_mileage` | NoneType/null | 总里程候选；真实 null，不冒充 odometer / 待验证 | 默认启用原值诊断实体：odometer_raw；不猜单位/枚举/权限。 | C / C0 |
| vehicles | `$[].v6_dark_img_url` | str | 暗色车型图片；未来主题表达，URL 安全审查 / — | 不创建重复Image；签名/URL值主动移除，仅静态shape研究。 | H/J / C0 |
| vehicles | `$[].v6_light_img_url` | str | 车辆车型图片；light 优先、img fallback；签名查询移除 / — | image.vehicle + 安全 device/tracker picture；只接受审核过的公共 HTTPS URL | A/F / C0 |
| vehicles | `$[].vehicle_name` | str | 车型；当前 model 来源 / — | DeviceInfo.model，优先vehicle_name_en，再vehicle_name；不是独立实体。 | F/H / C0 |
| vehicles | `$[].vehicle_name_en` | str | 车型语言版本；display fallback，非独立状态 / — | DeviceInfo.model，优先vehicle_name_en，再vehicle_name；不是独立实体。 | F/H / C0 |
| vehicles | `$[].vehicle_name_zh` | str | 车型语言版本；display fallback，非独立状态 / — | 当前 profiles parser 未使用该字段；保留私有raw，不能宣称自动按HA语言选车型。 | H/I / C0 |
| vehicles | `$[].vehicle_type` | int | 车型/业务枚举；路由和支持矩阵，不能转权限 / — | 默认启用原值诊断实体：vehicle_type_raw；不猜单位/枚举/权限。 | C / C0 |
| vehicles | `$[].vin` | str | 车辆 VIN；可评估敏感 metadata，当前未映射，不进入 diagnostics / — | 敏感 VIN 从 raw 保留值中移除；不作为 DeviceInfo 或公开 diagnostics。 | J/I / C0 |
| vehicles | `$[].wnumber` | str | 车辆云身份；用于现有 unique_id/device identifier / — | 既有车辆身份与默认启用序列号诊断；diagnostics不输出实际SN。 | F/C/H / C0 |
| status | `$` | dict | 结构容器；仅记录 shape，不创建 Entity / — | 有界 runtime raw（隐私字段主动移除）；静态字段清单。只有已审核名称进入运行时 schema，未知语义不创建实体。 | H/I / C0 |
| status | `$.ai_estimate_mileage` | float/int | 普通/AI 续航，分别表达 / km | 单一endurance续航实体按precise/estimated/AI优先并报告来源；三值仍保留调试视图 | A/H / C0 |
| status | `$.barrel_lock_status` | int | 座桶锁候选；无值枚举/反馈契约，暂不造 Lock / — | 默认启用原值诊断实体：seat_lock_raw；不猜单位/枚举/权限。 | C / C0 |
| status | `$.battery_exist` | int | 电池存在枚举；确认后可诊断，不与 pack_count 等价 / — | 默认启用原值诊断实体：battery_present_raw；不猜单位/枚举/权限。 | C / C0 |
| status | `$.ble_name` | str | 蓝牙名称；不能当蓝牙连接在线状态 / — | 有界 runtime raw（隐私字段主动移除）；静态字段清单。只有已审核名称进入运行时 schema，未知语义不创建实体。 | H/I / C0 |
| status | `$.charging` | int | 充电状态；当前 binary_sensor / — | binary_sensor.charging | A / C0 |
| status | `$.dump_energy` | str | 车辆 SOC；当前 battery Entity / % | sensor.battery | A / C0 |
| status | `$.estimate_mileage` | float | 普通/AI 续航，分别表达 / km | 单一endurance续航实体按precise/estimated/AI优先并报告来源；三值仍保留调试视图 | A/H / C0 |
| status | `$.is_common_user` | int | 共享账户标记；不能推出 owner 或控制权 / — | 有界raw/观测模型和安全调试视图；b24移除独立协议或重复实体，不扩大未知语义 | H/I / C0 |
| status | `$.is_smart_service_expired` | int | 服务到期标志候选；需验证对 endpoint 权限影响 / — | 默认启用原值诊断实体：service_expired_raw；不猜单位/枚举/权限。 | C / C0 |
| status | `$.left_mileage_user_choose` | int | App 续航显示偏好/阈值候选；未知语义 / — | 有界raw/观测模型和安全调试视图；b24移除独立协议或重复实体，不扩大未知语义 | H/I / C0 |
| status | `$.loc` | dict | 结构容器；仅记录 shape，不创建 Entity / — | 有界 runtime raw（隐私字段主动移除）；静态字段清单。只有已审核名称进入运行时 schema，未知语义不创建实体。 | H/I / C0 |
| status | `$.loc.acc` | int | acc；可能 ignition 或定位属性，不能直接判精度/电源 / 待验证 | 默认启用原值诊断实体：acc_raw；不猜单位/枚举/权限。 | C / C0 |
| status | `$.loc.lat` | str | 车辆定位；精确值不进入 diagnostics/schema摘要 / 角度；CRS待验证 | device_tracker（双坐标有效且coordinates opt-in）；不做坐标转换 | B / C3 |
| status | `$.loc.lock` | int | 锁当前状态，不能等同 engine 控制效果 / — | binary_sensor.unlocked + 反码诊断 sensor.vehicle_lock_raw（默认关闭） | A/C / C0 |
| status | `$.loc.lon` | str | 车辆定位；精确值不进入 diagnostics/schema摘要 / 角度；CRS待验证 | device_tracker（双坐标有效且coordinates opt-in）；不做坐标转换 | B / C3 |
| status | `$.permissions` | NoneType/null | 真实 null；权限仍为未知，不推断允许或位图。显式启用后的命令交由云端最终鉴权。 / — | 有界raw/观测模型和安全调试视图；b24移除独立协议或重复实体，不扩大未知语义 | H/I / C0 |
| status | `$.precise_estimate_mileage` | float | 精确续航；沿用 endurance/remaining_range aliases / km | 单一endurance续航实体按precise/estimated/AI优先并报告来源；三值仍保留调试视图 | A/H / C0 |
| status | `$.precise_mileage_user_choose` | int | App 续航显示偏好/阈值候选；未知语义 / — | 有界raw/观测模型和安全调试视图；b24移除独立协议或重复实体，不扩大未知语义 | H/I / C0 |
| status | `$.pwr` | int | 车辆电源状态；不是功率 / — | binary_sensor.main_power | A / C0 |
| status | `$.remain_charge_time` | str | 服务器剩余充电文字；保留原实体，不能当秒 / — | 默认启用现有文本实体，空文本unknown/not_reported；正在充电也可能缺报，不造0分钟。 | B / C2 |
| status | `$.remain_charge_timestamp` | int | 剩余充电时间候选；duration 或 deadline 不明确 / 待验证 | 有界raw/观测模型和安全调试视图；b24移除独立协议或重复实体，不扩大未知语义 | H/I / C0 |
| status | `$.sn` | str | 状态所属车辆校验；不新增重复序列号实体 / — | b14状态身份守卫：明确sn必须匹配请求车辆；缺失/null允许但不声称身份已确认；错车/非法值不更新raw或telemetry。 | H/J / C0 |
| status | `$.v6_dark_img_url` | str | 车型图片备用来源；沿用 profile 图片策略，非独立新实体 / — | status adapter未使用；图片来自profile；raw移除URL值，不增加图片下载。 | J/I / C0 |
| status | `$.v6_light_img_url` | str | 车型图片备用来源；沿用 profile 图片策略，非独立新实体 / — | status adapter未使用；图片来自profile；raw移除URL值，不增加图片下载。 | J/I / C0 |
| battery | `$` | dict | 结构容器；仅记录 shape，不创建 Entity / — | 有界 runtime raw（隐私字段主动移除）；静态字段清单。只有已审核名称进入运行时 schema，未知语义不创建实体。 | H/I / C0 |
| battery | `$.battery_count` | str | 服务端 count；历史为字符串，曾与数组长度不符；只作 schema / — | 有界raw/观测模型和安全调试视图；b24移除独立协议或重复实体，不扩大未知语义 | H/I / C0 |
| battery | `$.battery_find_my_support` | bool | 寻电池支持标志；不是 bell/buck/engine 权限 / — | 默认启用支持布尔诊断实体；不是控制权限。 | C / C0 |
| battery | `$.battery_list` | list | 结构容器；仅记录 shape，不创建 Entity / — | 有界 runtime raw（隐私字段主动移除）；静态字段清单。只有已审核名称进入运行时 schema，未知语义不创建实体。 | H/I / C0 |
| battery | `$.battery_list[]` | dict | 结构容器；仅记录 shape，不创建 Entity / — | 有界 runtime raw（隐私字段主动移除）；静态字段清单。只有已审核名称进入运行时 schema，未知语义不创建实体。 | H/I / C0 |
| battery | `$.battery_list[].bat_temp` | str | BMS 温度；保持身份 / °C | 电池 sensor.batt_temp；身份守卫 | A / C0/CG |
| battery | `$.battery_list[].bms_cycle` | str | 循环数；只有明确 support=true 才启用，false 的哨兵不使用 / 无HA单位（计数） | 循环次数支持门禁及reported_cycles属性，或分层电量调试观测；不创建重复raw实体 | C / C0/CG |
| battery | `$.battery_list[].bms_volt` | str | BMS 电压；显式合法数值，保持单/多包旧身份 / V | 电池 sensor.bms_voltage；身份守卫；当前真实匿名单包挂车辆 | A / C0/CG |
| battery | `$.battery_list[].electricity` | str | 包 SOC 候选；范围/主包/缓存语义待核，与 status SOC 区分 / 待验证（可能 %） | 循环次数支持门禁及reported_cycles属性，或分层电量调试观测；不创建重复raw实体 | C / C0 |
| battery | `$.battery_list[].score` | int | 评分原始量；不得直接命名 SOH / 未知 | 默认启用单包/有身份分包评分或原值；score不是SOH/%，循环原值不绕过support；旧正式循环计数保留支持门禁。 | A / C0 |
| battery | `$.battery_main` | dict | 结构容器；仅记录 shape，不创建 Entity / — | 有界 runtime raw（隐私字段主动移除）；静态字段清单。只有已审核名称进入运行时 schema，未知语义不创建实体。 | H/I / C0 |
| battery | `$.battery_main.electricity` | str | 主电池量；与外层及车辆 SOC 数值不同，不自动归并 / 待验证 | 有界raw/观测模型和安全调试视图；b24移除独立协议或重复实体，不扩大未知语义 | H/I / C0 |
| battery | `$.battery_type` | str | 电池类型编码；核实枚举后 metadata / — | 有界raw/观测模型和安全调试视图；b24移除独立协议或重复实体，不扩大未知语义 | H/I / CG |
| battery | `$.charging` | int | BMS 充电标志；与 status 比较，当前 primary 继续 status / — | 有界 runtime raw（隐私字段主动移除）；静态字段清单。只有已审核名称进入运行时 schema，未知语义不创建实体。 | H/I / C0 |
| battery | `$.charging_power` | int | 原始充电量；charging_power_raw 保持无单位 / W | 正式能量/功率实体，保留raw后缀unique ID和数值；单位维护者确认，历史数据库不重写。 | A / C2 |
| battery | `$.charging_protection` | dict | 结构容器；仅记录 shape，不创建 Entity / — | 有界 runtime raw（隐私字段主动移除）；静态字段清单。只有已审核名称进入运行时 schema，未知语义不创建实体。 | H/I / C0 |
| battery | `$.charging_protection.status` | int | 充电保护标志；枚举未知，不直接套 Problem class / — | 有界raw/观测模型和安全调试视图；b24移除独立协议或重复实体，不扩大未知语义 | H/I / C0 |
| battery | `$.charging_protection.url` | str | 充电保护相关 URL；功能/访问权限未知，禁止后台任意抓取 / — | 有界 runtime raw（隐私字段主动移除）；静态字段清单。只有已审核名称进入运行时 schema，未知语义不创建实体。 | J/I / C0 |
| battery | `$.electricity` | int | 电池外层量；与 status/main 分开，未知更新时间 / 待验证 | 有界raw/观测模型和安全调试视图；b24移除独立协议或重复实体，不扩大未知语义 | H/I / C0 |
| battery | `$.have_bms_cycle_support` | bool | 循环数 feature gate；必须 True，不从 cycle 数值猜能力 / — | 默认启用支持布尔诊断实体；不是控制权限。 | C / C0 |
| battery | `$.remain_charge_time` | str | BMS 充电文字；保留 raw，避免与 status 重复实体 / — | 有界 runtime raw（隐私字段主动移除）；静态字段清单。只有已审核名称进入运行时 schema，未知语义不创建实体。 | H/I / C0 |
| travel | `$` | dict | 结构容器；仅记录 shape，不创建 Entity / — | 有界 runtime raw（隐私字段主动移除）；静态字段清单。只有已审核名称进入运行时 schema，未知语义不创建实体。 | H/I / C0 |
| travel | `$.detail` | list | 结构容器；仅记录 shape，不创建 Entity / — | 有界 runtime raw（隐私字段主动移除）；静态字段清单。只有已审核名称进入运行时 schema，未知语义不创建实体。 | H/I / C0 |
| travel | `$.detail[]` | str | 按月日历的numeric字符串日里程；本次既有归档30/31项、和匹配月总且9月20/20日映射匹配 / km | MonthSummary严格校验后Action日序列；可新增valid+当月+fresh的today投影，不输出真实日程到diagnostics | E/H/I / C0 |
| travel | `$.duration` | int | 月时长候选；不能当 last ride duration / s | 默认启用服务端本月聚合实体，独立于部分ride列表。 | A / C0 |
| travel | `$.ec` | int | 月能量原始值；禁止直接 Energy Dashboard / Wh | 正式能量/功率实体，保留raw后缀unique ID和数值；单位维护者确认，历史数据库不重写。 | A / C2 |
| travel | `$.first_time` | int | 首行程/状态候选；样本零，不能推出 timestamp / 待验证 | 有界 runtime raw（隐私字段主动移除）；静态字段清单。只有已审核名称进入运行时 schema，未知语义不创建实体。 | H/I / C0 |
| travel | `$.list` | NoneType/list/null | 服务端返回的行程集合，真实list20且times128；分页语义待验证 / — | 已由非空recorded shape确认；Ride集合供get_trips；最多已返回集合，不声明完整月份。 | E/H / C0 |
| travel | `$.list[]` | dict | 结构容器 / — | 有界 runtime raw + 审核 schema；不创建实体。 | H/I / C0 |
| travel | `$.list[].day_total_mileage` | str | 对应结束日的日里程，本次9月20/20与detail[index]匹配；不能按每条ride相加 / km（该样本关系） | Raw/一致性证据；today仍取经过校验的daily序列，不重复创建entity | H/I / C0 |
| travel | `$.list[].duration` | int | 秒；20/20原始样本与end-start一致 / s | sensor.last_ride_duration、Ride、Action和event；总平均速度分母 | A/E/D/H / C0 |
| travel | `$.list[].ec` | str | 行程能量；维护者确认Wh / Wh | 正式能量/功率实体，保留raw后缀unique ID和数值；单位维护者确认，历史数据库不重写。 | A / C0 |
| travel | `$.list[].end_time` | int | Unix秒；明确最新已完成ride的排序依据 / Unix seconds | sensor.last_ride_end、Ride、查询response和completed事件 | A/E/D/H / C0 |
| travel | `$.list[].end_time_format` | str | 中国业务时区文本；fallback和冲突检查 / — | Ride ended_at显式Asia/Shanghai fallback；不另建重复实体 | E/H / C0 |
| travel | `$.list[].longest_distance` | NoneType | 真实null；意义/单位待验证 / 待验证 | 私有raw/审核schema；无可用state | H/I / C0 |
| travel | `$.list[].longest_time` | NoneType | 真实null；意义/单位待验证 / 待验证 | 私有raw/审核schema；无可用state | H/I / C0 |
| travel | `$.list[].mileages` | str | km显示契约；内部转m / km | sensor.last_mileage、Ride、Action和event；总平均速度分子 | A/E/D/H / C0 |
| travel | `$.list[].speed` | str | ninecli显示契约为服务端最高km/h；App未独立核验 / km/h | sensor.last_ride_max_speed、Ride、Action和event；不被sample覆盖 | A/E/D/H / C0 |
| travel | `$.list[].start_time` | int | Unix秒；时区转换为UTC / Unix seconds | sensor.last_ride_start、Ride、查询response和completed事件 | A/E/D/H / C0 |
| travel | `$.list[].travel_id` | str | 行程ID；实测可作为detail查询参数 / — | Ride.ride_id/detail_id；查询Action；不作为每ride entity ID | E/H / C0 |
| travel | `$.list[].used_electricity` | str | 使用电量raw；缩放/物理含义待验证 / 未知 | Ride.used_electricity_raw和Action；不自动当%或energy实体 | E/H / C0 |
| travel | `$.month` | str | 查询月份；必须与 requested month 一致，不能取当前日期覆盖 / YYYYMM | 有界 runtime raw（隐私字段主动移除）；静态字段清单。只有已审核名称进入运行时 schema，未知语义不创建实体。 | E/H / C0 |
| travel | `$.times` | int | 月次数候选；分页/撤销/重复语义待验 / — | 默认启用服务端本月聚合实体，独立于部分ride列表。 | A / C0 |
| travel | `$.total_mileages` | str | 月里程；现 month_mileage 无 state_class；单调性待验 / km | sensor.month_mileage + get_trips response | A/E / C0 |
| trip_detail | `$` | dict | 单次行程详情根对象 / — | 有界raw/审核schema；不猜Power/Speed/Torque、不强制按HA功能映射。 | H/I/J / C0 |
| trip_detail | `$.avg_engine_power` | NoneType | null节点/统计候选：没有非空schema、单位或语义证据 / 待验证 | 有界raw/审核schema；不猜Power/Speed/Torque、不强制按HA功能映射。 | H/I/J / C0 |
| trip_detail | `$.avg_shaft_speed` | NoneType | null节点/统计候选：没有非空schema、单位或语义证据 / 待验证 | 有界raw/审核schema；不猜Power/Speed/Torque、不强制按HA功能映射。 | H/I/J / C0 |
| trip_detail | `$.avg_speed` | int | 服务端平均raw，sample为0；语义未知 / 未知 | Ride.server_average_speed_raw与Action；总平均速度仍distance/duration，不能替代。 | E/H / C0 |
| trip_detail | `$.avg_throttle_opening` | NoneType | null节点/统计候选：没有非空schema、单位或语义证据 / 待验证 | 有界raw/审核schema；不猜Power/Speed/Torque、不强制按HA功能映射。 | H/I/J / C0 |
| trip_detail | `$.avg_torque` | NoneType | null节点/统计候选：没有非空schema、单位或语义证据 / 待验证 | 有界raw/审核schema；不猜Power/Speed/Torque、不强制按HA功能映射。 | H/I/J / C0 |
| trip_detail | `$.duration` | int | 详情时长字段；正式单位按固定显示/解析契约为秒；列表20/20关系不冒称全部detail实测 / s | 显式历史查询归一化/合并到Ride和Action response；不回写当前最近行程实体/事件。 | E/H / C0 |
| trip_detail | `$.ec` | str | 单次行程能量；维护者确认Wh / Wh | 显式历史查询归一化/合并到Ride和Action response；不回写当前最近行程实体/事件。 | E/H / C0 |
| trip_detail | `$.end_time` | int | Unix秒；明确最新已完成ride的排序依据 / Unix seconds | 显式历史查询归一化/合并到Ride和Action response；不回写当前最近行程实体/事件。 | E/H / C0 |
| trip_detail | `$.engine_power_nodes` | NoneType | null节点/统计候选：没有非空schema、单位或语义证据 / 待验证 | 有界raw/审核schema；不猜Power/Speed/Torque、不强制按HA功能映射。 | H/I/J / C0 |
| trip_detail | `$.img` | str | 行程截图URL，可能含路线隐私 / — | 从raw移除；不映射车型Image、不抓取图片。 | J/I / C0 |
| trip_detail | `$.is_show_simple_point` | bool | 上游显示策略提示；具体语义待验证 / 待验证 | 有界raw/审核schema；不猜Power/Speed/Torque、不强制按HA功能映射。 | H/I/J / C0 |
| trip_detail | `$.max_shaft_speed` | NoneType | null节点/统计候选：没有非空schema、单位或语义证据 / 待验证 | 有界raw/审核schema；不猜Power/Speed/Torque、不强制按HA功能映射。 | H/I/J / C0 |
| trip_detail | `$.max_torque` | NoneType | null节点/统计候选：没有非空schema、单位或语义证据 / 待验证 | 有界raw/审核schema；不猜Power/Speed/Torque、不强制按HA功能映射。 | H/I/J / C0 |
| trip_detail | `$.mileages` | str | km显示契约；内部转m / km | 显式历史查询归一化/合并到Ride和Action response；不回写当前最近行程实体/事件。 | E/H / C0 |
| trip_detail | `$.mileages_nodes` | NoneType | null节点/统计候选：没有非空schema、单位或语义证据 / 待验证 | 有界raw/审核schema；不猜Power/Speed/Torque、不强制按HA功能映射。 | H/I/J / C0 |
| trip_detail | `$.shaft_speed_nodes` | NoneType | null节点/统计候选：没有非空schema、单位或语义证据 / 待验证 | 有界raw/审核schema；不猜Power/Speed/Torque、不强制按HA功能映射。 | H/I/J / C0 |
| trip_detail | `$.show_simple_point_days` | int | 上游显示策略提示；具体语义待验证 / 待验证 | 有界raw/审核schema；不猜Power/Speed/Torque、不强制按HA功能映射。 | H/I/J / C0 |
| trip_detail | `$.speed` | str | ninecli显示契约为服务端最高km/h；App未独立核验 / km/h | 显式历史查询归一化/合并到Ride和Action response；不回写当前最近行程实体/事件。 | E/H / C0 |
| trip_detail | `$.speed_nodes` | NoneType | null节点/统计候选：没有非空schema、单位或语义证据 / 待验证 | 有界raw/审核schema；不猜Power/Speed/Torque、不强制按HA功能映射。 | H/I/J / C0 |
| trip_detail | `$.start_time` | int | Unix秒；时区转换为UTC / Unix seconds | 显式历史查询归一化/合并到Ride和Action response；不回写当前最近行程实体/事件。 | E/H / C0 |
| trip_detail | `$.tamp_speed_nodes` | NoneType | null节点/统计候选：没有非空schema、单位或语义证据 / 待验证 | 有界raw/审核schema；不猜Power/Speed/Torque、不强制按HA功能映射。 | H/I/J / C0 |
| trip_detail | `$.throttle_opening_nodes` | NoneType | null节点/统计候选：没有非空schema、单位或语义证据 / 待验证 | 有界raw/审核schema；不猜Power/Speed/Torque、不强制按HA功能映射。 | H/I/J / C0 |
| trip_detail | `$.torque_nodes` | NoneType | null节点/统计候选：没有非空schema、单位或语义证据 / 待验证 | 有界raw/审核schema；不猜Power/Speed/Torque、不强制按HA功能映射。 | H/I/J / C0 |
| trip_detail | `$.trail` | str | 分号分点/逗号四列；lon/lat/speed_raw/delta_raw；CRS和后两列单位未知 / 混合；未核实单位不能转换 | Ride轨迹/速度样本，显式include_track且位置opt-in后Action返回坐标；不进state/diagnostics/recorder。 | E/H / C0 |
| trip_detail | `$.used_electricity` | int | 使用电量raw；缩放/物理含义待验证 / 未知 | 显式历史查询归一化/合并到Ride和Action response；不回写当前最近行程实体/事件。 | E/H / C0 |

## 5. 非遥测能力与原生缓存

| ninecli能力 | 当前HA表达及边界 |
|---|---|
| password login、refresh、whoami | Config Flow/会话认证与reauth；不建账号/token实体，不将whoami资料公开 |
| SMS login | 已研究接口，尚未新增生产flow；发短信有外部副作用，不当只读调研 |
| vehicles | 原生CLI一次发现并准备private vehicles.json；设备/profile/Image；随后受鉴权REST |
| status / battery | 当前状态/身份安全的BMS实体；组freshness与partial failure保持 |
| travel month / detail | 当前月份和最近完成ride的小状态；历史ONLY-response Actions；新ride小Event |
| bell / buck / engine-start / engine-stop | 正常命名Button；显式启用/allowlist及现有安全门禁；不当Lock，不推断物理反馈 |
| --json / serve / MCP | 提供接口的方式，不是遥测量；当前生产CLI车辆发现+随机Bearer loopback REST |

private缓存的vehicles、wnumber、vehicle_name、device_name、business_line只作内部
transport路由/元数据，不额外创建Entity/导出值；完整契约见
[原生输入输出与缓存](v2x-ninecli输入输出与车辆缓存契约.md)。CLI缓存要求不能套用到
所有REST控制；离线POST路由验证不证明真实权限/执行结果。

## 6. 验收与剩余门槛

三项新增测试核实：所有recorded字段/类型有明确分类和来源；车辆摘要隔离其他车和
账户列表、详情TTL/删除；启用后真实HA state只含小型数字metadata，不暴露原始值，
不增加API调用或轮询需求。发布门槛仍为当前commit的必要静态检查、既有测试和
minimum/stable/beta、Hassfest、HACS，不无理由反复重跑。

仍未完成：未知权限策略的用户决定/真实能力证据、指定动作的未来实车验收、电池
稳定身份与child eligibility、能量/轨迹单位、CRS、cloud历史分页、更广车型/平台。
这些问题不因清单完整或新增诊断就变成已实现；主目标继续，不能据b13判定全部完成。

本阶段只读生产快照见[evidence](evidence/v2x-b13-production-readonly.json)：仍安装b8，
56登记实体/26启用/30集成禁用；读取两份日志各最多128KiB尾部，只见1/3条Ninebot
Warning。未记录日志原文/身份；范围内无Error不能证明所有请求/实体正常。未部署新版。
