# Ninebot for Home Assistant

[中文说明](README_zh.md)

Version 2 uses the pinned **ninecli 0.1.7** App protocol backend for vehicle
list, status, battery and trip queries. It replaces the old OpenClaw backend.
This is an independent, unofficial integration; vendor API availability can change.

**2.0.0b15 is a beta release.** Review the upgrade instructions and limitations
before installing it. [Release notes](https://github.com/Wuty-zju/ha_ninebot/releases/tag/v2.0.0b15)
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
2026.1.0/Python 3.13 and HA 2026.9.4 / 2026.10.0b0/Python 3.14.

The account/password form validates an isolated candidate session. Account
identity and token files are committed only after validation and duplicate
checks. Passwords are not saved in ConfigEntry. Expired sessions have a UI
reauthentication flow; reauth/reconfigure cannot switch to a different account.

Vehicle discovery runs the pinned native JSON command to prepare ninecli's
business-line cache; remaining I/O uses its authenticated loopback child. Passwords
are sent in local HTTP bodies, not command-line arguments. Tokens live in a
private per-entry directory under `.storage/ninebot_v2`; this is a same-user
trust boundary, not protection against privileged or same-user inspection.
No service is exposed to the LAN and no binary is downloaded at runtime.

## Data and options

Default status polling is 120 seconds, battery/trips 600 seconds and vehicle
list 3600 seconds. After discovery, periodic groups follow enabled entities and
internal dependencies: disabling BMS/travel entities stops their regular requests
unless the SOC model or Ride event needs them. Empty/anonymous multi-pack inventory
keeps an hourly discovery probe; a failed initial BMS discovery uses group backoff. Polling is serialized per account. A failed car/group does
not discard other successful results; a local timer notifies entities when data
expires or the business month changes, without extra cloud requests.

Main entities include vehicle SOC, cloud precise range, charging, main power,
unlocked state, battery voltage/temperature and current-month distance.
Cloud estimated/AI ranges, latest ride measurements and the public vehicle image
are enabled by default. Raw energy/power diagnostics remain disabled by default. Missing is unknown; valid zero remains zero. Unsupported
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
timestamps retain the last-returned snapshot; ordering is unknown.
Pagination/completeness remains unverified: an observed month returned 20 rows
while its raw times field reported 128. Previous-month fallback updates only
last-ride information, never current-month totals.

Cloud coordinates are opt-in because the coordinate reference is unverified.
No automatic GCJ/WGS conversion is claimed. Standard GPS trackers participate in
HA Zones/Map; disabling coordinates removes their location attributes. Vehicle
images are enabled by default, use a reviewed public HTTPS origin without opaque signatures
or redirects, and retain the HA image cache while the URL stays the same.
Unknown origins remain unavailable; not every model image has been verified.

**SOC energy estimation is opt-in.** Set nominal voltage and capacity explicitly;
there is no assumed 72 V/20 Ah pack for new users. The model estimates
`V × Ah × ΔSOC / 100 / 1000` kWh, not charger input or precision battery energy.
Gaps, implausible jumps and source/configuration changes re-baseline. A new model
generation has new entity identities; old model history is never rewritten.
Daily/monthly samples are assigned to the receiving business day/month; sampling
across a boundary is approximate. No instantaneous power is inferred from SOC.

**Controls are opt-in.** Enable controls and allow each vehicle explicitly.
Authenticated, present vehicles with fresh profile/status data may send commands;
known denials, query errors and ambiguous capabilities still block them. Unknown
permission stays unknown: Ninebot decides final authorization and vehicle support.
“Ready to send” does not mean the physical action completed. Commands are sent
once, never retried automatically, then followed by a status readback.
Lock state is exposed by the binary sensor. The nonfunctional Lock control
entity is removed; engine commands are not represented as lock/unlock. Read-only refresh
is unaffected. See the [current policy and authorized live test](docs/v2x-云端鉴权控制与实测契约.md).

Raw business responses are retained only in bounded private memory, with secrets
and personal profile fields removed. They are never entity attributes or persistent
trip history. Diagnostics export approved schema names/types/counts, not raw values
or arbitrary unknown keys. Nine sanitized recorded payloads plus a separately labeled selected image-shape fixture are replayable; identity, location and schedule
replacements are explicitly synthetic. Nonempty travel/detail structure and
Unix-second/China-time relationships were confirmed in a bounded read-only study.
Ride distance and server maximum speed use ninecli's km/max-km/h display contract;
App UI was not independently tested. Trail speed/delta units, coordinate system
and energy meanings remain unverified. Five default-enabled last-ride sensors expose duration, UTC start/end, server
maximum and total-trip average speed alongside last-ride distance. Disable them individually if unnecessary.
They have no cumulative statistics class, large attributes or extra detail calls;
ambiguous/future time or missing data stays unknown. Average speed is unknown if
duration conflicts with the observed time span. Historical queries use the
response actions documented below.

## Upgrade and rollback

Back up HA configuration and `.storage` before installing a major upgrade.
Both v1 layouts are recognized. Fork token/config/cache files are copied into
private v2 storage; original files remain intact. OpenClaw users reauthenticate
through the App login form. Migration does not perform background password login.

Existing entity identities, user names and disabled settings are preserved where
the physical meaning is equivalent. Missing sources and changed estimate models
are removed from the registry when explicitly classified as obsolete, rather than
being reused for a different quantity. Valid current and model identities stay intact.
Cloud trip energy never replaces local estimated totals. Serial identity is never
matched by vehicle nickname. No recorder SQL is edited.

Legacy SOC-by-range, GSM/address/report-time and estimated-energy identities are
removed when covered by the reviewed obsolete identity list. New SOC model entities have separate IDs.
The unused legacy full-range parameter is removed during obsolete identity cleanup.
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

Control diagnostics separates local readiness from upstream support/permission
evidence and physical completion, and lists each blocking condition. Explicit
opt-in and an allowed vehicle permit cloud authorization without inventing a
permission parser. See the [current policy](docs/v2x-云端鉴权控制与实测契约.md);
older phase descriptions below preserve their historical behavior.

Image/GPS privacy and per-vehicle request dependencies are documented in the
[Phase 8 contract](docs/v2x-图片位置与请求依赖契约.md). No new entity identities or
storage schema are introduced in this phase.

## Obsolete entities in 2.0.0b9

After a successful setup, the integration removes reviewed obsolete registry IDs
owned exclusively by this entry's known vehicle device: legacy GSM/address/range
SOC estimates and obsolete energy counters, the nonfunctional Lock control, and
the unused full-range model input. These placeholders are no longer recreated.
The lock binary sensor remains the observed state; it is not a vehicle control.

This changes the earlier placeholder-retention policy at the user's request.
Valid current sensors, custom names, opt-in model generations and unrelated
entities stay intact; temporary missing values do not imply obsolescence. HA's
registry API is used, without editing recorder SQL or production files during
development. Old automations referencing removed entities need updating.

Vehicle controls have ordinary translated names and dedicated icons. This
cleanup release does not yet change the control-dispatch policy or activate
hardware actions; the next control stage addresses that independently.

## Standard entity visibility in 2.0.0b10

Confirmed estimated/AI range, latest ride distance/duration/timestamps/speeds and
the vehicle image are normal features. Existing integration-disabled defaults
are enabled through the public registry API after a successful first refresh,
only for exact identities on exclusively owned, present vehicle devices. User
disables, custom names and entity IDs are preserved. GPS, ride events, controls,
estimation and raw diagnostics keep their explicit enable policies. Enabled ride
entities share the existing travel schedule; they do not poll trip details.

## Native vehicle cache in 2.0.0b11

The native `vehicles --json` operation prepares `vehicles.json`, required by
ninecli 0.1.7 battery/control routing. REST `/vehicles` alone does not prepare
this cache. Discovery is a single operation, not a second periodic list request.
REST and native discovery share one bounded queue and lock; discovery stops the
old serve process before native cache/token updates, then REST restarts lazily.
Passwords still travel only in authenticated local HTTP bodies. CLI stderr is
discarded; unknown exit errors are not classified by matching text.

The new client successfully read and normalized both vehicles' BMS data from an
isolated session copy. This does not claim production HA was upgraded or any
hardware action was executed. Controls remain subject to the existing permission
policy. See the [native I/O and cache contract](docs/v2x-ninecli输入输出与车辆缓存契约.md).

## Control representation in 2.0.0b12

Remote start/stop are direct command buttons, never Lock entities. Enabling
controls and allowing a vehicle enables integration-disabled button defaults
for that vehicle; user-disabled buttons remain disabled. Registration does not
bypass the execution gate: unknown permissions still refuse real commands.

The normal diagnostic **Vehicle control status** sensor explains configuration,
authentication, freshness, unverified capabilities and denial. Its four action
attributes contain only fixed enum states, not raw payloads or identities; it
adds no status/battery/trip polling demand. Large raw data remains in the bounded
memory layer and schema diagnostics, outside entity state/recorder.

Native loopback tests verify four REST command paths and one attempt per action.
They also establish that REST controls dispatch without a vehicle cache, unlike
BMS and CLI cache-based routing. The tests deliberately return service errors;
no real cloud command or physical outcome is claimed.

## Raw data review in 2.0.0b13

The optional diagnostic **Raw data summary** reports only a vehicle's bounded
cache record count and four integer metadata counters. It excludes account-wide
discovery, raw values, arbitrary field names, identities and locations, and adds
no polling demand. Cached records do not imply fresh or complete cloud data.
Detailed approved schema information remains in downloadable diagnostics.

The [current field review](docs/v2x-当前字段利用与调试摘要.md) consolidates 136 observed
paths across five business endpoints, including nonempty trips and details.
It distinguishes implemented representations, private runtime data and unknown
semantics; source aliases and candidates are not presented as observed fields.

## Status identity and native conformance in 2.0.0b14

An explicit returned status `sn` must match the requested vehicle before raw
cache or telemetry updates. Missing/null identity remains supported; a mismatch
is a partial group error and does not clear authentication or replace successful
timestamps. Existing valid cached values remain bounded by their original TTL.

Test-only native copies with temporary public keys verify encrypted control
acceptance and rejection against loopback upstreams. Original binaries and
production HA remain unchanged. This does not verify physical controls or
relax unknown-permission gating. See the [contract](docs/v2x-原生控制加密与状态归属契约.md).

## Cloud-authorized command dispatch in 2.0.0b15

This supersedes the b7–b14 unknown-permission gate. Configured vehicles can send
bell, seat-trunk, start and stop commands when local authentication/freshness
checks pass. Reviewed denials still block; unknown permissions are not promoted
to allowed. The translated control status now says “Ready to send”.

After explicit user authorization, each command was sent once on one vehicle
using an ephemeral copy of the production session. All four returned HTTP200,
ok=true and empty data. Immediate readbacks succeeded but the selected power/lock
state did not change, so no physical effect is claimed. Production HA files were
not changed; the temporary child and session were removed. The empty-response
fixture supports offline replay without repeating vehicle commands. Full scope
and limits are in the [b15 contract](docs/v2x-云端鉴权控制与实测契约.md).
