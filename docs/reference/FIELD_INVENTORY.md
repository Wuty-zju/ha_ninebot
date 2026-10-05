# 已观察字段统一清单

基线：ninecli 0.1.7 / b24，136业务路径、122非容器字段；12控制代理路径另在机器清单。
来源：[机器清单](../evidence/v2x-current-field-usage.json)及[NinePlus专项](../research/NinePlus生态源码审阅与ha_ninebot数据解析应用方案.md)的既有日表核验。
本页只维护全路径的用途，不维护版本叙事、实现进度或第二份调试契约。
当前行为读[Raw契约](../contracts/RAW_DATA.md)、[实体契约](../contracts/VEHICLES_ENTITIES.md)和[当前状态](../agent/CURRENT_STATE.md)。
原包装章节可在fbac9ec的Git历史查原文件；本次未新增云端采集或改写机器证据日期。
表中单位/alias各受证据范围限制；B为历史分类，不用于恢复默认禁用策略。

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
