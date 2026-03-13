# Home Assistant Ninebot 车辆集成

![Ninebot Logo](https://oms-oss-public.ninebot.com/website/npm/resource/doc/logo.png)

[![version](https://img.shields.io/github/manifest-json/v/Wuty-zju/ha_ninebot?filename=custom_components%2Fninebot%2Fmanifest.json)](https://github.com/Wuty-zju/ha_ninebot/releases/latest)
[![releases](https://img.shields.io/github/downloads/Wuty-zju/ha_ninebot/total)](https://github.com/Wuty-zju/ha_ninebot/releases)
[![stars](https://img.shields.io/github/stars/Wuty-zju/ha_ninebot)](https://github.com/Wuty-zju/ha_ninebot/stargazers)
[![issues](https://img.shields.io/github/issues/Wuty-zju/ha_ninebot)](https://github.com/Wuty-zju/ha_ninebot/issues)
[![HACS](https://img.shields.io/badge/HACS-Custom-blue.svg)](https://hacs.xyz)

[English](./README.md) | [简体中文](./README_zh.md)

这是一个将 Segway-Ninebot 云端车辆接入 Home Assistant 的自定义集成。

维护者：Wuty-zju

## HACS 收录状态

当前仓库通过 HACS Custom Repository 方式安装，暂未进入 HACS Default。

## 集成能力说明

本集成提供面向车辆场景的实体，包括：

- 主电池电量与剩余里程
- 车辆锁状态（只读 lock + binary lock）
- 充电状态与主电源状态
- GSM 信号指标（CSQ 与换算 RSSI）
- 车辆位置、上报时间戳/时间、SN、名称、车辆图片

## 环境要求

- Home Assistant Core >= 2024.4.0

## 安装方式

### 方法 1：HACS（推荐）

一键安装：

[![Open your Home Assistant instance and open this repository in HACS.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=Wuty-zju&repository=ha_ninebot&category=integration)

或在 HACS 中手动添加：

1. 打开 Home Assistant -> HACS -> Integrations
2. 打开右上角菜单（三点）-> Custom repositories
3. 仓库地址填入：https://github.com/Wuty-zju/ha_ninebot
4. 类型选择 Integration
5. 搜索 Ninebot 并点击 Download
6. 重启 Home Assistant
7. 打开 设置 -> 设备与服务 -> 添加集成 -> Ninebot

### 方法 2：手动复制

1. 将 custom_components/ninebot 复制到 Home Assistant 的 config/custom_components 目录
2. 重启 Home Assistant
3. 在 UI 中添加集成

## 快速开始

1. 设置 -> 设备与服务 -> 添加集成 -> Ninebot
2. 输入账号密码
3. 选择语言和轮询参数
4. 保存并等待首轮同步

支持多账号，每个账号会创建一个 config entry 与一个 coordinator 实例。

## 配置项

- default_scan_interval（秒，默认 60）：车辆常规轮询周期
- unlocked_scan_interval（秒，默认 3）：车辆解锁时轮询周期
- charging_scan_interval（秒，默认 30）：车辆充电时轮询周期
- token_refresh_interval_hours（小时，默认 24）：token 刷新检查周期
- device_list_refresh_interval_hours（小时，默认 24）：设备列表刷新周期
- max_device_info_concurrency（默认 3）：并发查询设备动态信息上限
- device_info_failure_tolerance（默认 3）：设备连续失败容忍阈值
- debug（默认关闭）：只在内存中启用额外诊断数据

## 语义约定

推荐实体标识格式：

`<domain>.ninebot_<vehicle_sn_lower>_<english_entity_name>`

状态规则：

- 原始 status=0 表示上锁，status=1 表示解锁
- binary_sensor 的锁语义使用反转后的布尔状态
- lock 实体为只读镜像，不发送 lock/unlock 控制命令

## 架构说明

代码映射：

- custom_components/ninebot/api.py：登录、token、设备列表与并发查询
- custom_components/ninebot/coordinator.py：按车辆独立调度与失败回退
- custom_components/ninebot/storage.py：持久化缓存与迁移
- custom_components/ninebot/config_flow.py：安装/选项配置流程
- custom_components/ninebot/const.py：默认值与配置键定义

机制要点：

- token 与设备列表采用持久化缓存
- 车辆动态信息采用异步并发抓取 + 并发限制
- 每辆车独立 next_poll_at 与动态轮询间隔
- 失败后优先回退到最近有效缓存，超过阈值才标记 unavailable
- 调试数据仅保存在内存中，不写入持久化存储

## 发布与更新

当前发布目标版本：v1.0.0。

推荐发布流程：

1. 更新 custom_components/ninebot/manifest.json 版本
2. 提交并推送到 main
3. 创建并推送同版本 tag，例如：

```bash
git tag v1.0.0
git push origin v1.0.0
```

4. 在 GitHub Release 页面发布对应版本说明

HACS 通过 tag/release 检测更新。

## 仓库结构

- 集成代码：custom_components/ninebot
- HACS 元数据：hacs.json

## 相关链接

- English README：README.md
- 问题反馈：https://github.com/Wuty-zju/ha_ninebot/issues
