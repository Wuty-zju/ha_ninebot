# Ninebot for Home Assistant

[中文说明](README_zh.md)

Version 2 uses the pinned **ninecli 0.1.7** App protocol backend for vehicle
list, status, battery and trip queries. It replaces the old OpenClaw backend.
This is an independent, unofficial integration; vendor API availability can change.

The 2.0 refactor is under development on `refactor/ninecli-v2`. A release is
not ready until its checks, migration tests and final release audit pass.
Target prerelease: **v2.0.0b0** (manifest `2.0.0b0`).

## Install and configure

When the prerelease is published, add `Wuty-zju/ha_ninebot` as a custom
integration repository in HACS and select the beta version. Alternatively,
copy `custom_components/ninebot` into your HA configuration. Restart your
own HA after installation, then add **Ninebot** through Devices & services.
The development/release task does not install anything into the owner's HA.

Requires HA **2026.1.0+**, Python supplied by that HA release, and a compatible
ninecli wheel. Linux x86_64/arm64 musl and glibc, macOS x86_64/arm64 and Windows
amd64/arm64 wheels are published; published availability is not proof that every
platform has been runtime-tested. ARMv7/32-bit is unsupported. CI targets HA
2026.1.0/Python 3.13 and HA 2026.9.4/Python 3.14.

The account/password form validates an isolated candidate session. Account
identity and token files are committed only after validation and duplicate
checks. Passwords are not saved in ConfigEntry. Expired sessions have a UI
reauthentication flow; reauth/reconfigure cannot switch to a different account.

The integration starts its own authenticated loopback ninecli child. Passwords
are sent in local HTTP bodies, not command-line arguments. Tokens live in a
private per-entry directory under `.storage/ninebot_v2`; this is a same-user
trust boundary, not protection against privileged or same-user inspection.
No service is exposed to the LAN and no binary is downloaded at runtime.

## Data and options

Default status polling is 120 seconds, battery/trips 600 seconds and vehicle
list 3600 seconds. Polling is serialized per account. A failed car/group does
not discard other successful results; expired data becomes unavailable.

Main entities include vehicle SOC, cloud precise range, charging, main power,
unlocked state, battery voltage/temperature and current-month distance.
Cloud estimated/AI ranges, last returned trip and raw energy/power diagnostics
are disabled by default. Missing is unknown; valid zero remains zero. Unsupported
BMS cycles are not published as real counts. HA lock binary sensors are on
when unlocked; the App lock encoding is normalized before entity mapping.

Trip `ec` and `charging_power` units are not independently established, so raw
diagnostics have no physical unit or statistics class. They are not Energy
Dashboard meters. Last-row ordering/pagination is unverified; `last_*` remains
an optional snapshot of the first returned ride. Previous-month fallback updates
only last-ride information, never current-month totals.

Cloud coordinates are opt-in because the coordinate reference is unverified.
No automatic GCJ/WGS conversion is claimed. Vehicle images are optional.

**SOC energy estimation is opt-in.** Set nominal voltage and capacity explicitly;
there is no assumed 72 V/20 Ah pack for new users. The model estimates
`V × Ah × ΔSOC / 100 / 1000` kWh, not charger input or precision battery energy.
Gaps, implausible jumps and source/configuration changes re-baseline. A new model
generation has new entity identities; old model history is never rewritten.
Daily/monthly samples are assigned to the receiving business day/month; sampling
across a boundary is approximate. No instantaneous power is inferred from SOC.

**Experimental controls are off by default.** Enable them only after confirming
support and permission, and explicitly select individual vehicles. These use
engine start/stop, bell and seat-trunk commands; hardware effects and permissions
have not been verified in this development task. Timeouts are not retried.
A successful command with failed status readback is reported separately.

## Upgrade and rollback

Back up HA configuration and `.storage` before installing a major upgrade.
Both v1 layouts are recognized. Fork token/config/cache files are copied into
private v2 storage; original files remain intact. OpenClaw users reauthenticate
through the App login form. Migration does not perform background password login.

Existing entity identities, user names and disabled settings are preserved where
the physical meaning is equivalent. Missing sources and changed estimate models
keep their old registry/history instead of being reused for a different quantity.
Cloud trip energy never replaces local estimated totals. Serial identity is never
matched by vehicle nickname. No recorder SQL is edited.

Legacy SOC-by-range, GSM/address/report-time and estimated-energy identities do
not receive fabricated replacements. New SOC model entities have separate IDs.
The legacy full-range parameter remains local and is not used as an energy source.
App raw lock codes differ from the old OpenClaw encoding: update automations
that depended on the raw code to use normalized lock/unlocked entities.

Rolling back code alone does not reverse a ConfigEntry schema upgrade. Restore
both the pre-upgrade integration version and the matching HA configuration/storage
backup. Never downgrade storage by editing recorder or token JSON manually.

## Development and evidence

```sh
python -m pip install pytest-homeassistant-custom-component==0.13.305 ninecli==0.1.7 ruff==0.16.10 mypy==2.4.0
ruff check custom_components tests
ruff format --check custom_components tests
mypy custom_components/ninebot --follow-imports=silent
LITELLM_LOCAL_MODEL_COST_MAP=True pytest --cov=custom_components.ninebot --cov-branch --cov-fail-under=95
```

Tests use synthetic credentials, identities and coordinates. Controls are mocked;
the running HA is never a test target. See [development reports](docs/README.md)
for endpoint/entity mappings and source/binary audit boundaries. Diagnostics use
an explicit whitelist and contain no account, serial, token or position.

Current evidence verifies query behavior using earlier isolated session copies,
and v2 parser/lifecycle/HA flows using synthetic tests. New real password login,
refresh recovery, all hardware controls and additional platforms require separate
verification. Complete Go source/reproducible builds are not available from the
reviewed wheels; the dependency audit states that limitation explicitly.
