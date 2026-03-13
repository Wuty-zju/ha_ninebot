# Home Assistant Ninebot 车辆集成

[English](./README.md) | [简体中文](./README_zh.md)

![Ninebot Logo](https://oms-oss-public.ninebot.com/website/npm/resource/doc/logo.png)

这是一个将 Segway-Ninebot 云端车辆接入 Home Assistant 的自定义集成。

维护者：Wuty-zju

## 环境要求

- Home Assistant Core >= 2024.4.0

## 安装方式

### 方式一：HACS（推荐）

一键安装：

[![在你的 Home Assistant 中通过 HACS 打开此仓库。](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=Wuty-zju&repository=ha_ninebot&category=integration)

或手动添加自定义仓库：

1. 打开 HACS -> Integrations -> Custom repositories
2. 仓库地址填写：https://github.com/Wuty-zju/ha_ninebot
3. Category 选择 Integration
4. 搜索 Ninebot 并点击 Download
5. 重启 Home Assistant
6. 在 设置 -> 设备与服务 -> 添加集成 中搜索 Ninebot

### 方式二：手动安装

1. 将 custom_components/ninebot 复制到 Home Assistant 的 config/custom_components 目录
2. 重启 Home Assistant
3. 在 UI 中添加 Ninebot 集成

## 快速开始

1. 添加集成：设置 -> 设备与服务 -> 添加集成 -> Ninebot
2. 输入账号与密码
3. 选择语言与轮询参数
4. 保存并等待首次同步

支持多账号接入。每个账号对应一个独立 config entry 与 coordinator。

## 配置项说明

以下参数可在首次添加和后续选项页中配置：

- default_scan_interval（秒，默认 60）：车辆默认轮询间隔
- unlocked_scan_interval（秒，默认 3）：车辆解锁时轮询间隔
- charging_scan_interval（秒，默认 30）：车辆充电时轮询间隔
- token_refresh_interval_hours（小时，默认 24）：Token 强制校验/刷新周期
- device_list_refresh_interval_hours（小时，默认 24）：设备列表刷新周期
- max_device_info_concurrency（默认 3）：设备动态信息并发上限
- device_info_failure_tolerance（默认 3）：单车连续失败容忍次数
- debug（默认关闭）：开启运行期调试信息（仅内存）

## 规范实体设计（命名/图标/标识符）

建议统一实体标识符格式：

`<domain>.ninebot_<vehicle_sn_lower>_<english_entity_name>`

示例：

- `sensor.ninebot_2pde42522j0242_battery`
- `binary_sensor.ninebot_2pde42522j0242_vehicle_lock`

建议只保留以下规范实体（其余历史遗留实体逐步迁移或移除）：

| 原始字段 | HA 实体域 | 英文后缀 | 默认英文名 | 默认中文名 | 图标 | 单位/语义 |
| --- | --- | --- | --- | --- | --- | --- |
| `battery` | `sensor` | `battery` | Main Battery Level | 主电池电量 | `mdi:battery` | `%` |
| `device_name` | `sensor` | `vehicle_name` | Vehicle Name | 车辆名称 | `mdi:card-text` | 文本 |
| `sn` | `sensor` | `vehicle_sn` | SN Code | SN码 | `mdi:identifier` | 文本 |
| `img` | `image` | `vehicle_image` | Vehicle Image | 车辆图片 | `mdi:image` | 图片 URL |
| `status`（原始） | `sensor` | `vehicle_lock_raw` | Vehicle Lock Raw | 车辆锁原始值 | `mdi:lock-question` | 原始 `0/1` |
| `status`（转换） | `binary_sensor` | `vehicle_lock` | Vehicle Locked | 车辆锁 | `mdi:lock` | 锁定语义反转 |
| `status`（转换） | `lock`（只读） | `vehicle_lock` | Vehicle Lock | 车辆锁 | `mdi:lock` | 状态镜像，不可控制 |
| `chargingState` | `binary_sensor` | `charging` | Charging Status | 充电状态 | `mdi:battery-charging` | 开/关 |
| `pwr` | `binary_sensor` | `main_power` | Main Power Status | 主电源状态 | `mdi:power-plug` | 开/关 |
| `gsm`（原始） | `sensor` | `gsm_csq` | GSM Signal Strength (CSQ) | GSM信号强度（CSQ） | `mdi:signal` | `0-31` |
| `gsm`（换算） | `sensor` | `gsm_rssi` | GSM Signal Strength (RSSI) | GSM信号强度（RSSI） | `mdi:signal-variant` | `dBm`，公式 `-113 + 2*CSQ` |
| `estimateMileage` | `sensor` | `remaining_mileage` | Remaining Mileage | 剩余里程 | `mdi:map-marker-distance` | `km` |
| `remainChargeTime` | `sensor` | `remaining_charge_time` | Remaining Charge Time | 充电剩余时间 | `mdi:timer-outline` | 文本 |
| `gsmTime`（原始） | `sensor` | `gsm_report_timestamp` | GSM Report Timestamp | GSM上报时间戳 | `mdi:clock-outline` | 时间戳 |
| `gsmTime`（转换） | `sensor` | `gsm_report_time` | GSM Report Time | GSM上报时间 | `mdi:clock-time-four-outline` | 本地时区时间 |
| `locationInfo` | `sensor` | `vehicle_location` | Vehicle Location | 车辆位置 | `mdi:map-marker` | 文本 |

锁状态语义说明：

- 原始 `status=0` 表示上锁，`status=1` 表示解锁。
- 在“车辆锁（二进制）”中按锁定语义取反映射。
- `lock` 实体仅做状态展示，不下发控制命令。

## 原始信息层级与字段映射

为降低风险，本文档不展示具体官方接口路径。

当前核心依赖三类云端数据：

1. 账号鉴权数据
	 - 作用：管理 token/session 生命周期
	 - 常见字段：access_token、refresh_token、expires_at、token_checked_at

2. 账号设备集合数据
	 - 作用：车辆发现与账号-车辆映射
	 - 常见层级：data -> [device_item]
	 - 常见字段：sn、deviceName、img、model/productName

3. 单车动态状态数据
	 - 作用：按 SN 轮询车辆运行状态
	 - 常见层级：data -> 状态字段

常用字段映射示例：

- 电量：data.dumpEnergy
- 锁状态：data.powerStatus
- 充电状态：data.chargingState
- 预计续航：data.estimateMileage
- 功率：data.pwr
- 信号：data.gsm
- 位置描述：data.locationInfo.locationDesc

`gsmTime` 转换规则：

- 保留原始时间戳实体 `gsm_report_timestamp`
- 先按 Python `time.gmtime()` 语义转换为 UTC 基准时间
- 再按 Home Assistant 系统时区进行展示适配，生成 `gsm_report_time`

建议的持久化层级（示例）：

```text
account:
	access_token
	refresh_token
	expires_at
	token_checked_at
	devices_last_refresh
	devices[]

vehicles:
	<sn>:
		raw_state
		parsed_state
		last_success_at
		last_attempt_at
		failure_count
		current_interval
```

## 核心机制说明

实现模块映射（开发视角）：

- `custom_components/ninebot/api.py`：账号鉴权缓存、设备列表缓存、单车并发查询
- `custom_components/ninebot/coordinator.py`：单车调度、动态提频、失败回退编排
- `custom_components/ninebot/storage.py`：持久化结构、运行态缓存、Store 迁移兼容
- `custom_components/ninebot/config_flow.py`：配置项/选项页（轮询、缓存周期、并发、容错）
- `custom_components/ninebot/const.py`：常量、默认值、配置键定义

### 1）Token 缓存机制

- 持久化缓存 access_token、refresh_token、expires_at、token_checked_at
- 集成启动时优先读取缓存，不立即强制登录
- 刷新触发条件：
	- 集成启动时检查
	- 每 token_refresh_interval_hours 周期检查
	- token 过期或接近过期
- 若当前无可用 refresh_token 流程，则自动回退重新登录

### 2）设备列表缓存机制

- 设备列表与 devices_last_refresh 持久化保存
- 启动优先加载缓存设备列表
- 刷新触发条件：
	- 达到 device_list_refresh_interval_hours
	- 错误模式命中（例如设备变化、SN 不匹配）触发强制刷新
- 避免每轮轮询都请求设备列表

### 3）持久化机制

使用 Home Assistant 官方 Store 进行持久化，不把大块动态状态塞入 config entry。

- 账号级缓存：
	- token 相关字段
	- 设备列表缓存
- 车辆级运行缓存：
	- 最近成功原始数据与解析状态
	- failure_count、last_success_at、last_attempt_at
	- current_interval 与能量累计相关参数

注意：调试按钮信息、原始调试输出不持久化，仅保存在运行内存。

### 4）异步并发查询机制

- 多设备轮询采用并发异步，不使用串行 for-await
- 使用 asyncio.gather（或等价任务组）并发查询
- 使用 semaphore 限流，并发上限由 max_device_info_concurrency 控制（默认 3）
- 每台车请求互相独立，一台慢车不会拖慢其余车辆

### 5）单车轮询与 boost 机制

coordinator 采用短 tick + 单车到期调度，不再全账户统一提频。

- 每台车独立维护 next_poll_at 与 current_interval
- 每轮只请求已到期车辆
- 轮询优先级规则（单车生效）：
	1. 解锁：使用 unlocked_scan_interval
	2. 否则若充电：使用 charging_scan_interval
	3. 其他：使用 default_scan_interval

因此一台车解锁/充电，不会拉高其他车辆轮询频率。

示例：

- 车辆 A 解锁 -> 3 秒
- 车辆 B 锁定且未充电 -> 60 秒
- 车辆 C 充电 -> 30 秒

触发规则要求：

- 默认轮询频率 60s（可配置）
- 当 `binary_sensor.*_vehicle_lock` 从锁定变为解锁时，仅该车辆切到解锁频率（默认 3s）
- 当该车回到锁定且未充电时，回到默认频率
- 当 `binary_sensor.*_charging` 变为激活时，仅该车辆切到充电频率（默认 30s）
- 充电结束且锁定时回到默认频率

### 6）失败容错与缓存回退机制

当某台车 device_info 请求失败时：

- 优先保留并回退到该车最近一次成功缓存
- failure_count 自增
- failure_count 未超过 device_info_failure_tolerance 时，实体继续展示缓存值
- 超过阈值后，该车才可能标记 unavailable
- 任意一次成功后 failure_count 归零

这可以避免短时云端抖动导致实体立刻 unknown。

### 7）账户级缓存 + 车辆级调度设计

整体采用“账户级缓存 + 车辆级调度”架构：

- coordinator 负责账户级 token、设备集合与调度管理
- 每台车独立维护 next_poll_at、current_interval、failure_count、last_success_state
- 每个 tick 仅刷新到期车辆
- 未到期车辆不请求动态状态接口

### 8）主电池能量统计（流入/流出）

能量统计基于 `sensor.*_battery` 百分比变化：

- 百分比下降计入日/月主电池流出电量
- 百分比上升计入日/月主电池流入电量
- 依据百分比变化量与时间间隔计算流入/流出瞬时功率，语义对齐 HA 能量统计体系
- 统计结果按车辆持久化，重启后可延续

建议暴露统计实体：

- 日流入电量（kWh）
- 日流出电量（kWh）
- 月流入电量（kWh）
- 月流出电量（kWh）
- 流入瞬时功率（W）
- 流出瞬时功率（W）

## 状态来源说明

实体状态可能包含运行期元信息：

- data_source：live 或 cached
- failure_count
- last_success_at
- last_attempt_at
- current_scan_interval

用于判断当前值来自实时云端还是缓存回退。

## 调试信息策略

- 调试原始 JSON、调试按钮输出仅保存在运行内存
- 不写入 Store 持久化
- 不污染正式状态缓存

## 安全说明

- 当前集成使用账号密码登录 Ninebot 云端接口
- 受 Home Assistant 平台机制影响，集成持久化数据存储在本地配置文件中
- 请妥善保护 Home Assistant 配置目录与备份文件

## HACS 更新与发布

HACS 通过 Git tag/Release 检测新版本。

推荐发布流程：

1. 更新 custom_components/ninebot/manifest.json 中 version
2. 提交并推送 main
3. 创建并推送同版本 tag，例如：

```bash
git tag v0.9.2
git push origin v0.9.2
```

4. 在 GitHub 创建 Release 并填写更新说明

## 版本演进摘要（基于 Git/Tag）

- `v0.7`：引入动态轮询、number 参数与电量统计能力
- `v0.7.4` 到 `v0.7.7`：修复 options flow、i18n 与调试信息能力
- `v0.8`：完成账户缓存 + 单车调度 + 并发查询 + 缓存回退架构
- `v0.8.1`：修复 Store 持久化迁移兼容问题
- `v0.8.2`：发布整理与版本更新
- `v0.8.3`：兼容性与稳定性补丁
- `v0.9`：代码层优化（减少冗余持久化写入、增强空值鲁棒性、降低调试路径内存开销）
- `v0.9.1`：修复车辆锁状态语义链路，并保留调试按钮最近一次请求缓存
- `v0.9.2`：修复二进制车辆锁显示语义，使其与 Home Assistant LOCK 二进制传感器状态定义一致

## 仓库结构

- 集成代码：custom_components/ninebot
- HACS 元数据：hacs.json

## 相关链接

- English README：README.md
- 问题反馈：https://github.com/Wuty-zju/ha_ninebot/issues
