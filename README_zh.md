# Home Assistant Ninebot 车辆集成

[English](./README.md) | [简体中文](./README_zh.md)

![Ninebot Logo](https://oms-oss-public.ninebot.com/website/npm/resource/doc/logo.png)

这是一个将 Ninebot 云端设备接入 Home Assistant 的自定义集成。

维护者：Wuty-zju

## 集成描述

该集成提供面向车辆场景的 Ninebot 云端实体，包括：

- 主电池电量与剩余里程
- 车辆锁状态（只读 lock + 二进制锁状态）
- 充电状态与主电源状态
- GSM 信号指标（CSQ 与换算后的 RSSI）
- 车辆位置、上报时间戳/上报时间、SN、车辆名称与车辆图片

## 环境要求

- Home Assistant Core >= 2024.4.0

## 通过 HACS 安装

一键安装：

[![在你的 Home Assistant 中通过 HACS 打开此仓库。](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=Wuty-zju&repository=ha_ninebot&category=integration)

或手动添加自定义仓库：

1. 打开 HACS -> Integrations -> 菜单（三点）-> Custom repositories
2. 仓库地址填写：https://github.com/Wuty-zju/ha_ninebot
3. Category 选择：Integration
4. 搜索 Ninebot 并点击 Download
5. 重启 Home Assistant
6. 在 设置 -> 设备与服务 -> 添加集成 中搜索 Ninebot

## 通过 HACS 更新版本

HACS 通过仓库的 Git tag / Release 检测新版本。

推荐发布流程：

1. 修改 custom_components/ninebot/manifest.json 的 version
2. 提交并推送到 main
3. 创建并推送与 version 一致的 tag，例如：

```bash
git tag 0.1.1
git push origin 0.1.1
```

4. 可选但推荐：在 GitHub 上为该 tag 创建 Release

完成后，HACS 会检测到可更新版本。

## 手动安装

1. 将 custom_components/ninebot 复制到 Home Assistant 配置目录下
2. 重启 Home Assistant
3. 在 UI 中添加 Ninebot 集成

## 仓库结构

- 集成代码：custom_components/ninebot
- HACS 元数据：hacs.json

## 发布说明

### v0.5.1

- 全面修复实体命名，强制统一为基于 SN 的确定性 ID。
- 全面修复车辆锁状态映射（status=0 为上锁，status=1 为解锁）。
- 清理历史设备名 slug 和 `_2`/`_3` 等残留命名。
- 仅保留规范实体，并统一中英文默认名称。
- 修复锁状态前端显示异常与语义不一致问题。
