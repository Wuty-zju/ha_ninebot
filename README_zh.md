# Home Assistant Ninebot 车辆集成

[![version](https://img.shields.io/github/manifest-json/v/Wuty-zju/ha_ninebot?filename=custom_components%2Fninebot%2Fmanifest.json)](https://github.com/Wuty-zju/ha_ninebot/releases/latest)
[![releases](https://img.shields.io/github/downloads/Wuty-zju/ha_ninebot/total)](https://github.com/Wuty-zju/ha_ninebot/releases)
[![stars](https://img.shields.io/github/stars/Wuty-zju/ha_ninebot)](https://github.com/Wuty-zju/ha_ninebot/stargazers)
[![issues](https://img.shields.io/github/issues/Wuty-zju/ha_ninebot)](https://github.com/Wuty-zju/ha_ninebot/issues)
[![HACS](https://img.shields.io/badge/HACS-Custom-blue.svg)](https://hacs.xyz)

[English](./README.md) | [简体中文](./README_zh.md)

这是一个将 Segway-Ninebot 云端车辆接入 Home Assistant 的自定义集成。

维护者：Wuty-zju

## HACS 默认收录状态

当前仓库按 HACS Custom Repository 方式安装。

目前尚未进入 HACS Default。

因此现阶段用户仍需先手动添加自定义仓库，之后才能在 HACS 中搜索和安装。

后续在满足条件后，会向 `hacs/default` 提交 PR。
只有被默认收录后，用户才可以在 HACS 商店中直接搜索安装（无需手动添加仓库）。

## 环境要求

- Home Assistant Core >= 2024.4.0

## 通过 HACS 安装

### 中文步骤

1. 打开 Home Assistant
2. 打开 HACS -> Integrations
3. 打开右上角三点菜单 -> Custom repositories
4. 添加仓库地址: https://github.com/Wuty-zju/ha_ninebot
5. 类型选择 Integration
6. 搜索 Ninebot
7. 点击 Download
8. 重启 Home Assistant
9. 打开 设置 -> 设备与服务 -> 添加集成
10. 搜索 Ninebot
11. 完成配置

### English Steps

1. Open Home Assistant
2. Open HACS -> Integrations
3. Open the three-dot menu -> Custom repositories
4. Add repository URL: https://github.com/Wuty-zju/ha_ninebot
5. Select category: Integration
6. Search for Ninebot
7. Click Download
8. Restart Home Assistant
9. Go to Settings -> Devices & Services -> Add Integration
10. Search for Ninebot
11. Complete setup

## 手动安装

1. 将 `custom_components/ninebot` 复制到 Home Assistant 的 `config/custom_components` 目录
2. 重启 Home Assistant
3. 在 设置 -> 设备与服务 -> 添加集成 中搜索 Ninebot

## 版本与发布说明

当前发布目标版本：`v0.9.5`。

建议优先通过以下方式安装最新版本：

- GitHub Releases: https://github.com/Wuty-zju/ha_ninebot/releases
- HACS（当前为 custom repository 模式）

## 仓库元数据建议

为了后续默认收录准备，建议在 GitHub 仓库中保持：

- Repository Description
- Repository Topics（例如 `home-assistant`, `hacs`, `ninebot`）
- 每个版本对应正式 GitHub Release

## 提交到 HACS 默认商店前的准备

提交到 `hacs/default` 前建议确认：

- HACS Action 通过
- Hassfest 通过
- 仓库为公开仓库
- 已发布正式 GitHub Release `v0.9.5`
- 由仓库 owner 或 major contributor 提交 PR
- 在 `hacs/default` 的 integration 列表中新增该仓库
- 品牌图标存在：`custom_components/ninebot/brand/icon.png`

## 仓库结构

- 集成代码：`custom_components/ninebot`
- HACS 元数据：`hacs.json`

## 相关链接

- English README: `README.md`
- 问题反馈：https://github.com/Wuty-zju/ha_ninebot/issues
