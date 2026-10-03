# Ninebot for Home Assistant

[中文说明](README_zh.md)

Version 2 uses the pinned **ninecli 0.1.7** App protocol backend for vehicle
list, status, battery and trip queries. It replaces the old OpenClaw backend.
This is an independent, unofficial integration; vendor API availability can change.

**2.0.0b6 is a beta release.** Review the upgrade instructions and limitations
before installing it. [Release notes](https://github.com/Wuty-zju/ha_ninebot/releases/tag/v2.0.0b6)
and the [entity migration matrix](docs/2.0-实体迁移矩阵.md) describe the changes.

## Install and configure

Add `Wuty-zju/ha_ninebot` as a custom
integration repository in HACS and select the beta version. Alternatively,
copy `custom_components/ninebot` into your HA configuration. Restart your
own HA after installation, then add **Ninebot** through Devices & services.


The backend targets the Chinese App service; other regions are unverified.
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
not discard other successful results; a local timer notifies entities when data
expires or the business month changes, without extra cloud requests.

Main entities include vehicle SOC, cloud precise range, charging, main power,
unlocked state, battery voltage/temperature and current-month distance.
Cloud estimated/AI ranges, last returned trip and raw energy/power diagnostics
are disabled by default. Missing is unknown; valid zero remains zero. Unsupported
BMS cycles are not published as real counts. HA lock binary sensors are on
when unlocked; the App lock encoding is normalized before entity mapping.

Battery measurements keep their existing IDs. Vehicle-level voltage/temperature
are known only with one reported pack; multiple rows do not imply a primary pack.
Identified pack measurements follow their reported identity across reordering and
become unknown when that identity disappears. All remain on the vehicle device;
physical pack identity/composition is not yet verified for Child Devices.

Trip `ec` and `charging_power` units are not independently established, so raw
diagnostics have no physical unit or statistics class. They are not Energy
Dashboard meters. Timestamped rides are selected by valid end/start time, so
server reordering does not select an older ride. Legacy payloads without valid
timestamps retain the optional last-returned snapshot; ordering is unknown.
Pagination/completeness remains unverified: an observed month returned 20 rows
while its raw times field reported 128. Previous-month fallback updates only
last-ride information, never current-month totals.

Cloud coordinates are opt-in because the coordinate reference is unverified.
No automatic GCJ/WGS conversion is claimed. Vehicle images are optional.

**SOC energy estimation is opt-in.** Set nominal voltage and capacity explicitly;
there is no assumed 72 V/20 Ah pack for new users. The model estimates
`V × Ah × ΔSOC / 100 / 1000` kWh, not charger input or precision battery energy.
Gaps, implausible jumps and source/configuration changes re-baseline. A new model
generation has new entity identities; old model history is never rewritten.
Daily/monthly samples are assigned to the receiving business day/month; sampling
across a boundary is approximate. No instantaneous power is inferred from SOC.

**Controls fail closed.** Options and a vehicle allowlist are necessary but do
not prove upstream permission. The integration additionally requires fresh,
verified support, permission and action semantics. Current opaque/null capability
data does not meet this requirement, so hardware controls remain unavailable.
The existing Lock entity retains observed state and identity; lock/unlock actions
are rejected because engine start/stop equivalence is unverified. Read-only refresh
is unaffected. All development control tests use a fake verified backend contract.

Raw business responses are retained only in bounded private memory, with secrets
and personal profile fields removed. They are never entity attributes or persistent
trip history. Diagnostics export approved schema names/types/counts, not raw values
or arbitrary unknown keys. Nine sanitized recorded fixtures are replayable; identity, location and schedule
replacements are explicitly synthetic. Nonempty travel/detail structure and
Unix-second/China-time relationships were confirmed in a bounded read-only study.
Ride distance and server maximum speed use ninecli's km/max-km/h display contract;
App UI was not independently tested. Trail speed/delta units, coordinate system
and energy meanings remain unverified. Five optional last-ride sensors now expose duration, UTC start/end, server
maximum and total-trip average speed. Enable them individually in the entity UI.
They have no cumulative statistics class, large attributes or extra detail calls;
ambiguous/future time or missing data stays unknown. Average speed is unknown if
duration conflicts with the observed time span. No historical query action is
exposed yet.

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
Legacy lock-code diagnostics retain 0=locked and 1=unlocked. The reversed App
codes are normalized internally; lock/unlocked entities are preferred for automations.

Rolling back code alone does not reverse a ConfigEntry schema upgrade. Restore
both the pre-upgrade integration version and the matching HA configuration/storage
backup. Never downgrade storage by editing recorder or token JSON manually.

## Development and evidence

```sh
python -m pip install pytest-homeassistant-custom-component==0.13.305 ninecli==0.1.7 ruff==0.16.10 mypy==2.4.0
ruff check custom_components tests scripts
ruff format --check custom_components tests scripts
mypy custom_components/ninebot --follow-imports=silent
LITELLM_LOCAL_MODEL_COST_MAP=True python -m pytest --cov=custom_components.ninebot --cov-branch --cov-fail-under=95
```

Tests use synthetic credentials, identities and coordinates. Controls are mocked;
the running HA is never a test target. See [development reports](docs/README.md)
for endpoint/entity mappings and source/binary audit boundaries. Diagnostics use
an explicit whitelist and contain no account, serial, token or position.

The v2 serve client completed one bounded read-only query pass for two vehicles
using a copied session in an isolated Linux musl/arm64 container. Parser, HA flows,
migration and lifecycle boundaries are covered by synthetic tests; see the
[prerelease audit](docs/2.0-预发布验收.md) for platform and evidence details. New real password login,
refresh recovery, all hardware controls and additional platforms require separate
verification. Complete Go source/reproducible builds are not available from the
reviewed wheels; the dependency audit states that limitation explicitly.

Restarting or re-enabling estimation preserves totals while rebuilding the sample
baseline; disabled intervals are not counted. Unknown estimation storage versions
stop setup with a Repair issue rather than overwriting saved data. A failed session
rollback that cannot unload the runtime retains its journal/backup and requests a
user-managed restart through Repairs.

## Historical query actions

`ninebot.get_trips` and `ninebot.get_trip_detail` require a vehicle `device_id`
and response data. They do not create historical entities. Tracks require both
coordinates opt-in and `include_track`; automation traces or response variables
may retain locations. Detail fanout is limited to five; pages cover only the
rows returned by ninecli, with cloud completeness explicitly unknown.
See the [action contract and examples](docs/v2x-历史查询Actions契约.md).

## Optional ride events

Enable the disabled-by-default Ride event entity to observe `completed` cloud
travel end reports. Startup/re-enable establish a baseline without replaying
history. Reports require a verified travel ID and consistent past start/end
and duration; a new ID alone does not trigger an event. Attributes contain
small ride summaries, without coordinates, tracks, speed samples or raw JSON.

Observation is best effort: cloud pagination/upload timing is unverified. A
local 30-minute late window and 24-hour gap rebaseline suppress history floods.
Acknowledged disk storage precedes emission, so crash/cancellation may lose a
notification. No exactly-once guarantee or historical catch-up is claimed.
See [ride event behavior](docs/v2x-骑行事件契约.md).
