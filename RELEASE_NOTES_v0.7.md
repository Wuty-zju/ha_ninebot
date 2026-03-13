# Ninebot Integration Release Notes v0.7

发布日期：2026-03-14

## 版本摘要

v0.7 聚焦三个方面：

1. 新增每车本地可编辑电池参数（电压、容量）并持久化
2. 新增基于电量百分比变化的主电池流入/流出能量与功率统计
3. 新增按车辆状态自动切换轮询频率的动态轮询机制

## 主要新增功能

### 1) 设备级 Number 实体（本地参数）

每台车辆新增两个 Number 实体：

- number.ninebot_[sn]_main_battery_voltage
- number.ninebot_[sn]_battery_capacity

特性：

- 在设备页可直接编辑
- 参数为本地配置，不写回 Ninebot 云端
- 每台车独立存储
- Home Assistant 重启后保留

### 2) 主电池能量统计实体

新增以下传感器：

- sensor.ninebot_[sn]_battery_nominal_energy
- sensor.ninebot_[sn]_battery_energy_delta
- sensor.ninebot_[sn]_battery_outflow_energy_step
- sensor.ninebot_[sn]_battery_inflow_energy_step
- sensor.ninebot_[sn]_battery_outflow_power
- sensor.ninebot_[sn]_battery_inflow_power
- sensor.ninebot_[sn]_battery_outflow_energy_daily
- sensor.ninebot_[sn]_battery_outflow_energy_monthly
- sensor.ninebot_[sn]_battery_inflow_energy_daily
- sensor.ninebot_[sn]_battery_inflow_energy_monthly

计算逻辑：

- 标称能量：voltage * capacity / 1000（kWh）
- 百分比变化对应能量变化：nominal_kwh * delta_percent / 100
- 上升记为 inflow，下降记为 outflow（分开累计，不相互抵消）
- 日/月统计由集成内部自动管理和重置

### 3) 动态轮询策略

新增配置项（Config Flow 和 Options Flow 均可设置）：

- default_scan_interval（默认 60 秒）
- unlocked_scan_interval（默认 3 秒）
- charging_scan_interval（默认 30 秒）

调度优先级：

1. 任意车辆已解锁 -> unlocked_scan_interval
2. 否则任意车辆充电中 -> charging_scan_interval
3. 否则 -> default_scan_interval

说明：该策略直接修改 coordinator 的 update_interval，非仅展示。

## 兼容与修复

- 锁状态统一链路修复：status 缺失时回退 powerStatus
- 三类锁实体语义统一：0=上锁，1=解锁
- binary_sensor.vehicle_lock 语义修正为：on=上锁，off=已解锁

## 持久化机制

采用 Home Assistant 官方 Store 存储：

- 每台车电压与容量参数
- 上次电量样本（百分比与采样时间）
- inflow/outflow 的总计、日计、月计

## 升级说明

1. 升级到 v0.7 后，系统会为每车自动创建新实体。
2. 建议先在设备页设置正确电压与容量，以获得更准确的能量统计。
3. 若历史实体命名非标准，集成会根据 unique_id 自动规范化。

## 已知限制

- 功率为离散采样近似值，采样间隔越短，观测越平滑。
- 极端异常上报（短时间大幅跳变）会被基础防护忽略。
