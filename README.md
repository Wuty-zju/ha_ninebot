# Ninebot Integration for Home Assistant

[English](./README.md) | [简体中文](./README_zh.md)

![Ninebot Logo](https://oms-oss-public.ninebot.com/website/npm/resource/doc/logo.png)

A custom integration to connect Ninebot cloud devices into Home Assistant.

Maintainer: Wuty-zju

## Requirements

- Home Assistant Core >= 2024.4.0

## Install via HACS

One-click from HACS:

[![Open your Home Assistant instance and open this repository in HACS.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=Wuty-zju&repository=ha_ninebot&category=integration)

Or manually in HACS:

1. Open HACS -> Integrations -> menu (three dots) -> Custom repositories
2. Add repository URL: https://github.com/Wuty-zju/ha_ninebot
3. Category: Integration
4. Search Ninebot and click Download
5. Restart Home Assistant
6. Go to Settings -> Devices & Services -> Add Integration -> Ninebot

## Update via HACS

HACS detects new versions from repository tags/releases.

Recommended release flow:

1. Bump version in custom_components/ninebot/manifest.json
2. Commit and push to main
3. Create and push a tag matching the manifest version, for example:

```bash
git tag 0.1.1
git push origin 0.1.1
```

4. Optional but recommended: create a GitHub Release for the tag

After that, HACS will detect the update.

## Manual installation

1. Copy custom_components/ninebot to your Home Assistant config directory
2. Restart Home Assistant
3. Add integration from UI

## Repository layout

- Integration code: custom_components/ninebot
- HACS metadata: hacs.json
