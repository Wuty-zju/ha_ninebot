# Ninebot Integration for Home Assistant

![Ninebot Logo](https://oms-oss-public.ninebot.com/website/npm/resource/doc/logo.png)

[![version](https://img.shields.io/github/manifest-json/v/Wuty-zju/ha_ninebot?filename=custom_components%2Fninebot%2Fmanifest.json)](https://github.com/Wuty-zju/ha_ninebot/releases/latest)
[![releases](https://img.shields.io/github/downloads/Wuty-zju/ha_ninebot/total)](https://github.com/Wuty-zju/ha_ninebot/releases)
[![stars](https://img.shields.io/github/stars/Wuty-zju/ha_ninebot)](https://github.com/Wuty-zju/ha_ninebot/stargazers)
[![issues](https://img.shields.io/github/issues/Wuty-zju/ha_ninebot)](https://github.com/Wuty-zju/ha_ninebot/issues)
[![HACS](https://img.shields.io/badge/HACS-Custom-blue.svg)](https://hacs.xyz)

[English](./README.md) | [简体中文](./README_zh.md)

A custom integration to connect Ninebot cloud devices into Home Assistant.

Maintainer: Wuty-zju

## HACS Listing Status

This repository is available via HACS Custom Repository.

It is not listed in HACS Default yet.

## Integration Description

This integration provides vehicle-focused entities for Ninebot cloud devices, including:

- Main battery level and remaining range
- Vehicle lock state (read-only lock + binary lock state)
- Charging status and main power status
- GSM signal metrics (CSQ and converted RSSI)
- Vehicle location, report timestamp/time, SN, name, and vehicle image

## Requirements

- Home Assistant Core >= 2024.4.0

## Installation

### Method 1: HACS (recommended)

One-click install:

[![Open your Home Assistant instance and open this repository in HACS.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=Wuty-zju&repository=ha_ninebot&category=integration)

Or add manually in HACS:

1. Open Home Assistant -> HACS -> Integrations
2. Open menu (three dots) -> Custom repositories
3. Repository URL: https://github.com/Wuty-zju/ha_ninebot
4. Category: Integration
5. Search Ninebot and click Download
6. Restart Home Assistant
7. Go to Settings -> Devices & Services -> Add Integration -> Ninebot

### Method 2: Manual copy

1. Copy custom_components/ninebot into your Home Assistant config/custom_components directory
2. Restart Home Assistant
3. Add integration from UI

## Quick Start

1. Add integration: Settings -> Devices & Services -> Add Integration -> Ninebot
2. Enter account and password
3. Choose language and polling options
4. Save and wait for first sync

Multiple accounts are supported. Each account creates one config entry and one coordinator instance.

## Configuration Options

All options are available in initial setup and in options flow later.

- default_scan_interval (seconds, default 60): normal polling interval per vehicle
- unlocked_scan_interval (seconds, default 3): polling interval when one vehicle is unlocked
- charging_scan_interval (seconds, default 30): polling interval when one vehicle is charging
- token_refresh_interval_hours (hours, default 24): forced token check/refresh cycle
- device_list_refresh_interval_hours (hours, default 24): device list refresh cycle
- max_device_info_concurrency (default 3): max concurrent device dynamic info requests
- device_info_failure_tolerance (default 3): allowed consecutive failures before marking one vehicle unavailable
- debug (default off): enables extra diagnostics in memory only

## Canonical Entity Contract

Recommended entity identifier format:

`<domain>.ninebot_<vehicle_sn_lower>_<english_entity_name>`

Example:

- `sensor.ninebot_2pde42522j0242_battery`
- `binary_sensor.ninebot_2pde42522j0242_vehicle_lock`

Status semantic rules:

- Raw status=0 means locked, status=1 means unlocked
- In binary locked sensor, use inverted boolean for locked semantics
- Read-only lock entity mirrors status and must not send lock/unlock commands

## Architecture

Implementation mapping:

- custom_components/ninebot/api.py: auth cache, device list cache, async multi-device fetch
- custom_components/ninebot/coordinator.py: per-vehicle scheduler, boost policy, failure fallback orchestration
- custom_components/ninebot/storage.py: persistent account/runtime data and migration-safe Store layer
- custom_components/ninebot/config_flow.py: setup/options for polling, cache, concurrency, tolerance
- custom_components/ninebot/const.py: option keys/defaults and polling constants

Key mechanisms:

- Token cache with periodic refresh and re-login fallback
- Device list cache with periodic refresh and error-triggered force refresh
- Persistent Store for account/runtime data, while debug payload stays memory-only
- Async concurrent per-vehicle requests with semaphore limit
- Per-vehicle scheduler with unlocked/charging/default interval priority
- Failure tolerance with cache fallback before availability degradation

## Release and Update

Current release target: v1.0.1.

Recommended flow:

1. Bump version in custom_components/ninebot/manifest.json
2. Commit and push to main
3. Create and push matching tag, for example:

```bash
git tag v1.0.1
git push origin v1.0.1
```

4. Create GitHub Release notes for that tag

HACS detects updates from tags/releases.

## Repository Layout

- Integration code: custom_components/ninebot
- HACS metadata: hacs.json

## Links

- Chinese README: README_zh.md
- Issues: https://github.com/Wuty-zju/ha_ninebot/issues
