# Ninebot for Home Assistant

[中文说明](README_zh.md) · [Releases](https://github.com/Wuty-zju/ha_ninebot/releases) · [Development entry](docs/README.md)

An independent, unofficial Ninebot vehicle integration using pinned `ninecli==0.1.7`.
Version 2 replaces the OpenClaw backend. The current b24 code is a prerelease;
vendor API availability can change. It targets the Chinese App service; other regions are unverified.

## Installation and account

Add `Wuty-zju/ha_ninebot` as a custom integration repository in HACS and select a beta release,
or copy `custom_components/ninebot` into your HA configuration. Restart your HA after installation,
then add Ninebot in Devices & services. Requires **HA 2026.1.0+** and a compatible 64-bit ninecli wheel;
ARMv7/32-bit is unsupported. Published Linux/macOS/Windows wheels do not certify every platform.

Password and two-step SMS login are supported. Candidate sessions are isolated and validated before
commit; passwords are not saved in ConfigEntry or passed as command-line arguments. SMS is covered
by offline tests, with live verification pending. Reauth/reconfigure retain the same account.
Per-entry token/config directories are private local storage; same-user/privileged processes remain
inside the trust boundary. A managed random-Bearer loopback serve child handles requests.
Vehicle discovery uses a controlled native command to initialize the business routing cache.
See [backend/auth contract](docs/README.md).

## Current data and options

- Vehicle SOC, one remaining-range sensor (precise → estimated → AI), charging, power and unlocked state.
- BMS voltage, temperature, supported cycles, charging power in W and a health score that is **not SOH**.
- Current-month distance, energy in Wh, ride count and total duration; last-ride distance/time/energy/max/overall average speed.
- Battery presence, seat/ACC/service observations where reported; unknown encodings remain explicitly unverified.
- Vehicle image, optional GPS, small ride-completed events and response actions for historical queries.
- Optional nominal V/Ah produce one stable rated-energy specification. **SOC cumulative estimation was removed in b24.**

Created entities are enabled/visible by default; user-disabled/hidden choices remain intact.
Coordinates, controls, debugging and nominal battery parameters require their feature options.
Entity names use translations; English/Simplified Chinese are complete, with 21 languages covering
main names/settings/actions and English fallback for some other long help/error messages.

Default status/battery-travel/profile intervals are 120/600/3600 seconds. Per-vehicle/group freshness,
backoff, partial failures and typed consumer contexts govern requests; each account backend is serialized.
Disabled BMS/travel consumers stop regular queries except bounded initial/sparse discovery and ride-event demand.
Incomplete vehicle discovery does not prove unbinding or refresh missing vehicles' identities.
See [vehicles/entities/polling](docs/README.md).

GPS requires two valid coordinates and opt-in; its coordinate system is unverified and never auto-converted.
Image URLs use reviewed anonymous origins and HA caching. Diagnostics contain safe schema/metadata,
not raw values, credentials, identity, precise locations or trails. Debug views are bounded and do not request extra data.
See [raw/diagnostics contract](docs/README.md).

## History, events and controls

`ninebot.get_trips`, `get_trip_detail` and `get_history` target the HA vehicle device and return response data.
Historical lists/tracks are not entities or large state attributes. Local pagination only covers received rows;
reported monthly totals can exceed the returned list. History cursors are bounded memory, not a permanent database.
Tracks require coordinates opt-in plus `include_track`; automation traces may retain response locations.
Server maximum speed and distance/duration average remain distinct; point-speed/delta units are unverified.
Ride events establish a startup baseline and suppress duplicates; cloud timing/pagination can cause missed events.
See [travel/actions/event contract and example](docs/README.md).

Controls require explicit enablement, an allowed vehicle, authentication and fresh ownership/status.
Known denials or ambiguity block dispatch; unknown permission stays unknown and the cloud decides authorization.
Buttons send bell/buck/engine-start/engine-stop once, with bounded status reconciliation, no automatic retry
and no optimistic physical-state update. Engine commands are not lock/unlock. Acceptance does not verify a physical effect.
See [control contract](docs/README.md).

## Upgrade and development

Back up matching HA configuration/storage before upgrading. Meaningful IDs, user names and Recorder history
are preserved; reviewed obsolete estimates/duplicates are removed only for confirmed exclusive ownership.
Automations using removed IDs need adjustment. Downgrading requires matching pre-upgrade storage,
including parameter Store backups; code-only rollback does not reverse migrations.
[Historical migration matrix](https://github.com/Wuty-zju/ha_ninebot/blob/9ba20bcadea1ea0500d9c080e60a48adc35e2387/docs/2.0-实体迁移矩阵.md) records the initial v2 transition.

Developers start with [product documentation](docs/README.md) and [product constraints](AGENTS.md), then the relevant code/tests/fixture provenance.
[CHANGELOG](CHANGELOG.md) is release history.
Maintain local developer handbooks outside the product repository instead of adding a report for every beta.
Small changes use targeted offline regression; major boundaries use the pinned compatibility suite.
Exact CI execution/skip scope and physical verification are separate facts; see [development constraints](AGENTS.md).
