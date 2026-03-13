# Ninebot Integration for Home Assistant

[English](./README.md) | [简体中文](./README_zh.md)

![Ninebot Logo](https://oms-oss-public.ninebot.com/website/npm/resource/doc/logo.png)

Ninebot is a Home Assistant custom integration for Segway-Ninebot cloud vehicles.

Maintainer: Wuty-zju

## Requirements

- Home Assistant Core >= 2024.4.0

## Installation

### Method 1: HACS (recommended)

One-click install:

[![Open your Home Assistant instance and open this repository in HACS.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=Wuty-zju&repository=ha_ninebot&category=integration)

Or add manually in HACS:

1. HACS -> Integrations -> Custom repositories
2. Repository URL: https://github.com/Wuty-zju/ha_ninebot
3. Category: Integration
4. Search Ninebot and download
5. Restart Home Assistant
6. Settings -> Devices & Services -> Add Integration -> Ninebot

### Method 2: Manual copy

1. Copy custom_components/ninebot into your Home Assistant config/custom_components directory
2. Restart Home Assistant
3. Add integration from UI

## Quick Start

1. Add integration: Settings -> Devices & Services -> Add Integration -> Ninebot
2. Enter account and password
3. Choose language and polling options
4. Save and wait for first sync

This integration supports multiple accounts. Each account creates one config entry and one coordinator instance.

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

For consistency and migration safety, the recommended entity identifier format is:

`<domain>.ninebot_<vehicle_sn_lower>_<english_entity_name>`

Example:

- `sensor.ninebot_2pde42522j0242_battery`
- `binary_sensor.ninebot_2pde42522j0242_vehicle_lock`

Target canonical entity set (keep these, remove unrelated legacy entities):

| Raw field | HA domain | English entity id suffix | Default EN name | Default ZH name | Icon | Unit / semantics |
| --- | --- | --- | --- | --- | --- | --- |
| `battery` | `sensor` | `battery` | Main Battery Level | 主电池电量 | `mdi:battery` | `%` |
| `device_name` | `sensor` | `vehicle_name` | Vehicle Name | 车辆名称 | `mdi:card-text` | text |
| `sn` | `sensor` | `vehicle_sn` | SN Code | SN码 | `mdi:identifier` | text |
| `img` | `image` | `vehicle_image` | Vehicle Image | 车辆图片 | `mdi:image` | URL image |
| `status` (raw) | `sensor` | `vehicle_lock_raw` | Vehicle Lock Raw | 车辆锁原始值 | `mdi:lock-question` | raw `0/1` |
| `status` (converted) | `binary_sensor` | `vehicle_lock` | Vehicle Locked | 车辆锁 | `mdi:lock` | invert mapping for locked state |
| `status` (converted) | `lock` (read-only) | `vehicle_lock` | Vehicle Lock | 车辆锁 | `mdi:lock` | state mirror only, no control |
| `chargingState` | `binary_sensor` | `charging` | Charging Status | 充电状态 | `mdi:battery-charging` | on/off |
| `pwr` | `binary_sensor` | `main_power` | Main Power Status | 主电源状态 | `mdi:power-plug` | on/off |
| `gsm` (raw) | `sensor` | `gsm_csq` | GSM Signal Strength (CSQ) | GSM信号强度（CSQ） | `mdi:signal` | `0-31` |
| `gsm` (derived) | `sensor` | `gsm_rssi` | GSM Signal Strength (RSSI) | GSM信号强度（RSSI） | `mdi:signal-variant` | `dBm`, formula `-113 + 2*CSQ` |
| `estimateMileage` | `sensor` | `remaining_mileage` | Remaining Mileage | 剩余里程 | `mdi:map-marker-distance` | `km` |
| `remainChargeTime` | `sensor` | `remaining_charge_time` | Remaining Charge Time | 充电剩余时间 | `mdi:timer-outline` | text |
| `gsmTime` (raw) | `sensor` | `gsm_report_timestamp` | GSM Report Timestamp | GSM上报时间戳 | `mdi:clock-outline` | epoch seconds |
| `gsmTime` (derived) | `sensor` | `gsm_report_time` | GSM Report Time | GSM上报时间 | `mdi:clock-time-four-outline` | localized datetime |
| `locationInfo` | `sensor` | `vehicle_location` | Vehicle Location | 车辆位置 | `mdi:map-marker` | text |

Status semantic rules:

- Raw `status=0` means locked, `status=1` means unlocked.
- In binary locked sensor, use inverted boolean for locked semantics.
- Read-only `lock` entity mirrors status and must not send lock/unlock commands.

## Raw Data Hierarchy and Field Mapping

To reduce risk, this document intentionally does not expose concrete official API endpoint paths.

The integration consumes three categories of cloud data:

1. Account auth payload
	 - Purpose: token/session lifecycle management
	 - Typical keys: access_token, refresh_token, expires_at, token_checked_at

2. Account device collection payload
	 - Purpose: vehicle discovery and account-to-device mapping
	 - Typical hierarchy: data -> [device_item]
	 - Typical keys in each item: sn, deviceName, img, model/productName

3. Per-vehicle dynamic payload
	 - Purpose: runtime state polling per SN
	 - Typical hierarchy: data -> state fields

Main state fields parsed from raw payload:

- battery: data.dumpEnergy
- status: data.powerStatus
- chargingState: data.chargingState
- estimateMileage: data.estimateMileage
- pwr: data.pwr
- gsm: data.gsm
- location: data.locationInfo.locationDesc

`gsmTime` conversion rule:

- keep raw timestamp as `gsm_report_timestamp`
- convert with Python `time.gmtime()` semantics first (UTC baseline)
- then adapt/display in Home Assistant configured timezone as `gsm_report_time`

Recommended raw hierarchy (abstract):

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

## Architecture and Mechanisms

Implementation mapping (developer view):

- `custom_components/ninebot/api.py`: auth cache, device list cache, async multi-device fetch
- `custom_components/ninebot/coordinator.py`: per-vehicle scheduler, boost policy, failure fallback orchestration
- `custom_components/ninebot/storage.py`: persistent account/runtime data structure and migration-safe Store layer
- `custom_components/ninebot/config_flow.py`: user/options configuration for polling/cache/concurrency/tolerance
- `custom_components/ninebot/const.py`: option keys/defaults and polling constants

### 1) Token cache mechanism

- access_token, refresh_token, expires_at, token_checked_at are cached
- cache is persistent, not memory-only
- startup sequence first loads cached token, then validates refresh policy
- refresh triggers:
	- on integration startup
	- periodic check by token_refresh_interval_hours
	- token expired or near expiry
- if refresh_token flow is unavailable, integration falls back to re-login

### 2) Device list cache mechanism

- device list is cached persistently with devices_last_refresh
- startup loads cached device list first
- refresh triggers:
	- scheduled refresh by device_list_refresh_interval_hours
	- forced refresh on specific error patterns (for example SN mismatch or device set changed)
- avoids requesting device list in every polling cycle

### 3) Persistent storage mechanism

This integration uses Home Assistant Store for persistence and keeps config entry data clean.

- Account cache store
	- token/session fields
	- device list cache
- Runtime vehicle store
	- per-vehicle last successful raw/parsed state
	- failure_count, last_success_at, last_attempt_at
	- current_interval and energy-related accumulators

Important:

- Debug payloads and diagnostics are not persisted
- Debug data is runtime memory only and cleared after restart

### 4) Async multi-vehicle query mechanism

- multi-device polling is concurrent async, not serial for-await
- uses asyncio.gather (or equivalent task group pattern)
- semaphore limits concurrency by max_device_info_concurrency (default 3)
- each vehicle request is isolated, one slow/failing request does not block others

### 5) Per-vehicle scheduler and boost mechanism

The coordinator uses a short tick loop and a per-vehicle due-time scheduler.

- each vehicle has its own next_poll_at and current interval
- only due vehicles are queried
- interval is determined per vehicle by state priority:
	1. unlocked -> unlocked_scan_interval
	2. charging -> charging_scan_interval
	3. default -> default_scan_interval

So one unlocked/charging vehicle does not globally increase polling frequency for all vehicles.

Example:

- Vehicle A unlocked -> 3s
- Vehicle B locked and not charging -> 60s
- Vehicle C charging -> 30s

Boost trigger requirements:

- default polling interval starts at 60s (configurable)
- if `binary_sensor.*_vehicle_lock` switches from locked to unlocked, this vehicle switches to unlocked interval (default 3s)
- if vehicle returns to locked and not charging, it returns to default interval
- if `binary_sensor.*_charging` becomes on, this vehicle uses charging interval (default 30s)
- charging off and locked returns to default interval

### 6) Failure tolerance and cache fallback

If one device dynamic info request fails:

- keep last successful cached state for that vehicle
- increment failure_count
- while failure_count <= device_info_failure_tolerance, entities continue using cached values
- only after exceeding threshold, that vehicle may be marked unavailable
- successful fetch resets failure_count to 0

This prevents transient API errors from immediately turning all data into unknown.

### 7) Account cache + vehicle scheduler design

The design is account-level cache + vehicle-level scheduler:

- coordinator keeps account/session and device set management
- each vehicle keeps independent next_poll_at, interval, failure_count, last_success_state
- each tick refreshes only due vehicles
- non-due vehicles do not request dynamic cloud data

### 8) Battery energy accounting (in/out flow)

Energy statistics are derived from `sensor.*_battery` percentage trend.

- battery decrease contributes to day/month main-battery outflow energy
- battery increase contributes to day/month main-battery inflow energy
- instantaneous inflow/outflow power is computed from percent delta and elapsed time, aligned with Home Assistant energy statistic semantics
- counters are persisted per vehicle to survive restarts

Recommended exposed statistics:

- daily/monthly inflow kWh
- daily/monthly outflow kWh
- inflow instantaneous power (W)
- outflow instantaneous power (W)

## Data Source Semantics

Entity state may include runtime metadata such as:

- data_source: live or cached
- failure_count
- last_success_at
- last_attempt_at
- current_scan_interval

This helps diagnose whether values come from real-time cloud data or fallback cache.

## Debug Data Policy

- debug info/button payload and raw debug output are memory-only
- debug payload is never persisted in Store
- debug data does not pollute formal persisted state cache

## Security Notes

- This integration currently uses account/password login flow for Ninebot cloud APIs
- Home Assistant local files contain integration persistent data in plain form by platform design
- protect your Home Assistant config storage and backups

## Update and Release

HACS detects updates by tag/release.

Recommended release flow:

1. Update version in custom_components/ninebot/manifest.json
2. Commit and push to main
3. Create and push matching tag, for example:

```bash
git tag v0.9.1
git push origin v0.9.1
```

4. Create GitHub Release notes for the tag

## Release Evolution (from Git history)

- `v0.7`: dynamic polling, number entities, and battery energy features
- `v0.7.4` to `v0.7.7`: options-flow fixes, i18n fixes, debug diagnostics refinements
- `v0.8`: account cache + per-vehicle scheduler + async concurrency + cache fallback
- `v0.8.1`: storage migration fix for runtime Store compatibility
- `v0.8.2`: release packaging update
- `v0.8.3`: compatibility and stability hardening
- `v0.9`: codebase optimization pass (reduced redundant persistence writes, improved null-safety, and lower debug memory overhead)
- `v0.9.1`: lock status semantic fix and debug button payload cache retention

## Repository Layout

- Integration code: custom_components/ninebot
- HACS metadata: hacs.json

## Documents

- Chinese README: README_zh.md
- Issues: https://github.com/Wuty-zju/ha_ninebot/issues
