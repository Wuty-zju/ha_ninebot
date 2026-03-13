# Ninebot Integration for Home Assistant

[![version](https://img.shields.io/github/manifest-json/v/Wuty-zju/ha_ninebot?filename=custom_components%2Fninebot%2Fmanifest.json)](https://github.com/Wuty-zju/ha_ninebot/releases/latest)
[![releases](https://img.shields.io/github/downloads/Wuty-zju/ha_ninebot/total)](https://github.com/Wuty-zju/ha_ninebot/releases)
[![stars](https://img.shields.io/github/stars/Wuty-zju/ha_ninebot)](https://github.com/Wuty-zju/ha_ninebot/stargazers)
[![issues](https://img.shields.io/github/issues/Wuty-zju/ha_ninebot)](https://github.com/Wuty-zju/ha_ninebot/issues)
[![HACS](https://img.shields.io/badge/HACS-Custom-blue.svg)](https://hacs.xyz)

[English](./README.md) | [简体中文](./README_zh.md)

Ninebot is a Home Assistant custom integration for Segway-Ninebot cloud vehicles.

Maintainer: Wuty-zju

## HACS Listing Status

This repository is currently released for HACS Custom Repository installation.

It is not listed in HACS Default yet.

Before default inclusion, users must add this repository manually in HACS Custom repositories.

After all required checks pass, a PR will be submitted to `hacs/default`.
Only after that inclusion can users install by searching directly in HACS store without adding custom repository.

## Requirements

- Home Assistant Core >= 2024.4.0

## Install via HACS

### Chinese Steps

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

## Manual Installation

1. Copy `custom_components/ninebot` into your Home Assistant `config/custom_components` directory.
2. Restart Home Assistant.
3. Go to Settings -> Devices & Services -> Add Integration.
4. Search and add Ninebot.

## Configuration Notes

- Multiple accounts are supported.
- Per-device scheduler and cache fallback are enabled.
- Vehicle lock semantics are unified:
  - raw `status=0` means locked
  - raw `status=1` means unlocked

## Release / Version

Current release target: `v0.9.5`.

Please install the latest version from:

- GitHub Releases: https://github.com/Wuty-zju/ha_ninebot/releases
- HACS (custom repository mode before default inclusion)

Tag and release should match the same version number.

## Repository Metadata Suggestions

For better discoverability and HACS readiness:

- Set GitHub repository description
- Set GitHub repository topics (for example: `home-assistant`, `hacs`, `ninebot`)
- Keep formal GitHub Releases updated for each version

## HACS Default Preparation (Next Phase)

The following must be ready before submitting to `hacs/default`:

- HACS Action passes
- Hassfest passes
- Repository is public
- Formal GitHub Release `v0.9.5` exists
- PR submitted by repository owner or major contributor
- Integration entry added in `hacs/default` list
- Brand icon exists at `custom_components/ninebot/brand/icon.png`

## Repository Layout

- Integration code: `custom_components/ninebot`
- HACS metadata: `hacs.json`

## Links

- Chinese README: `README_zh.md`
- Issue tracker: https://github.com/Wuty-zju/ha_ninebot/issues
