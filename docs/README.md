# Ninebot integration behavior

This page describes the current user-facing contract. Developer investigations and local raw samples belong in a separate private workspace; they are not required to install or run this integration.

## Authentication

Pinned ninecli==0.1.7 runs through a managed authenticated loopback server. Passwords travel in the local request body, not argv or ConfigEntry storage. Each entry has isolated sessions and reauth. Vehicle discovery has a controlled native cache initialization exception. SMS flow is implemented and covered offline; live SMS verification remains pending.

## Scheduling and retained data

b37 shares identical in-flight reads through a bounded broker, including failures. One account owns one wire transaction; two accounts can progress concurrently through a global gate. Each account admits at most 16 active/queued operations (12 reads, 4 commands), 64 readers overall and 32 on one shared read. Status is prioritized with bounded background turns and vehicle fairness. Commands are distinct single attempts; authentication and native routing transactions remain serialized.

Executor parsing retains the original request start/receipt times and monotonic revision. Cache rereads do not renew freshness, and stale generation/ownership responses cannot overwrite telemetry or the historical ledger. Concurrent ledger preparations commit in order. Transient GET failures can retry once within the same deadline; authentication/schema failures and controls are not retried. Only an actual transport Retry-After is honored, with bounded shared-transport recovery cooldown and existing per-group backoff. Diagnostics expose counters, not request keys, identities or payloads.

For a loaded account and still-known vehicle, retained daily statistics, get_statistics without refresh, import_statistics and entity migration reports remain usable after live profile expiry or authentication failure. Explicit cloud refresh and live telemetry still use their freshness/authentication checks. Removed/foreign vehicles and unloaded entries remain rejected. 2.0.0b39 restores minimal, account-scoped profiles and historical Actions from the archive when the first cloud refresh fails. Cached profiles do not grant live freshness or control permission, and invalid local sessions still require reauthentication. Retained data does not prove all upstream history was returned.

A command attempt establishes a new status request barrier. b38 lock confirmation uses separate single-attempt status reads, so pre-command requests and ordinary auto-retried polling cannot substitute for the command confirmation budget.

## Entities

Created entities are enabled and visible by default; user choices are preserved. Location display defaults on and preserves any explicit off choice; controls and debug views require opt-in. The range sensor prefers precise, then estimated, then AI. b24 removes low-confidence SOC cumulative estimates and duplicate ranges. Nominal V/Ah define rated energy, not measured capacity or SOH. Existing meaningful IDs and Recorder history remain stable.

BMS voltage/temperature/cycles require valid data and support. Energy ec is Wh and charging_power is W according to maintainer confirmation. Health score is not SOH. Emergency battery SOC uses battery_main.electricity (integer %); battery_type 1 is lithium and 2 lead acid (maintainer-confirmed). ACC 0/1 is off/on, seat 0/1 locked/unlocked, battery presence 0/1 absent/present, service expiry 0/1 active/expired. ACC, battery presence and service expiry retain their existing sensor identities. Seat lock and vehicle lock use the native Lock platform; their cross-domain upgrade is described below. Unfamiliar valid codes are unrecognized, missing/invalid reports stay unknown. Apple Find My and cycle-support binary states mean supported/not supported. GPS needs two valid coordinates; the coordinate system is unverified and is never automatically converted.

## History

get_trips, get_trip_detail and get_history return bounded response data. Local pagination covers only received rows; it does not prove all upstream rides were returned. Server maximum speed and distance/duration average are distinct. Point-speed/distance units remain unverified. Tracks require coordinate opt-in and include_track; automation traces may retain response locations. Events establish a baseline and suppress duplicates; cloud timing or incomplete lists can cause missed events.

## Controls

Controls require explicit enablement, allowlist and fresh ownership/status. Known denial and ambiguity block dispatch; unknown permission stays unknown and the cloud decides authorization. Vehicle unlock maps to engine/start, lock to engine/stop. The integration does not infer motion or P and does not suppress stop for missing telemetry: vehicle/cloud conditions decide whether it locks. Buck unlocks the seat compartment; seat locking is manual only.

Seat unlock maps to buck; locking is manual only. Calling lock.lock on the seat entity raises a translated manual-locking error without sending a command. The entity exposes remote_lock_mode and last_operation, and follows the next cloud status after local closing. It does not infer whether the lid is open. HA has no unlock-only feature flag; its generic Lock dialog may show a lock action that reports this limitation.

The original start/stop/seat/bell buttons remain. Locks and buttons use the same per-physical-vehicle lease across accounts, with independent credentials and observations. A competing action returns busy instead of waiting to run later; different vehicles may progress through the existing bounded broker. Pending locking/unlocking represents a request, never an optimistic change to is_locked.

Lock actions send one POST per explicit request, including when cached status already matches. Confirmation performs at most three single-attempt status GETs at approximately 0/2/5 seconds within ten seconds. After confirmation, up to three background status-only reads at 10/20/25 seconds observe automatic relocking; the entire follow-up ends within 30 seconds and does not keep the UI pending or hold the command lease. A newer same-vehicle command replaces this observation window. Authentication failure, rate limits, network errors, loss of ownership and unload stop further follow-up. No command is replayed. A request rejected before wire invocation reports not sent without confirmation reads. A fresh target confirms cloud state only; an uncertain POST stays uncertain, and an accepted command without the target reports a concise unconfirmed message. Historical command confirmation is not rewritten when the vehicle later locks automatically. Bell retains its ordinary one-read reconciliation.

last_operation contains only a bounded last result: request/readback outcome, target/observed lock state, confirmation and timestamps. It is excluded from Recorder when supported. There are no action-history entities, raw payloads, locations, automatic command replay or permanent high-frequency polling. No real controls were performed during development.

## Development

Product constraints are in [AGENTS](../AGENTS.md). Use targeted offline tests for small changes and the pinned compatibility suite at major boundaries. Record what ran and what was skipped; old mock/CI results do not prove current cloud or vehicle behavior. Runtime files must remain inside custom_components/ninebot. Private captures, developer plans and workspace tools must not be added to a release or integration runtime.

Raw diagnostics include measured backend metadata, fixed rejection reasons and value-free schema transition counts. Unknown key names/values and anonymous fingerprints are not exported. Cache references are memory-only and become invalid when records are replaced, expired, evicted or unloaded; this does not change entities or add cloud requests.


Ride detail queries preserve list values when the detail omits or invalidates them.
Action schema 2 retains its existing keys and additionally exposes `field_states`,
`detail_field_states` and `field_sources` on the ride, plus `detail_context` on
`get_trip_detail`. The first describes the effective model; the second describes
what the detail actually reported. Missing or null is not zero, and an explicit
empty verified trail differs from an absent trail. Raw references remain internal.
Maximum speed is the server-reported maximum; overall average is distance/duration,
not a mean of samples. Unverified sample units and coordinate systems stay unknown.


History Actions also return `statistics`: server month/scanned-month aggregates
and returned/indexed unique ride subsets use separate `basis` values. Wh/km is
sum(Wh)/sum(km) for the same dataset, not the mean of ride ratios. Missing values,
conflicting/missing IDs, zero distance and nonfinite calculations produce unknown
intensity. Cursor continuation keeps indexed totals across pages; the response
page is only a slice. Coverage remains explicit: scanning every month does not
prove every ride was obtained. No extra requests or per-ride entities are added.


Today distance is a current-state projection of the validated monthly daily chart.
It uses Asia/Shanghai, and requires a successful travel sample on that business
day within the travel freshness limit. Yesterday's cached chart becomes unknown
at local midnight; a prefilled zero is not treated as a new measurement. Missing
or inconsistent charts stay unknown. The sensor is visible by default and uses
km/distance; no total-increasing statistics or extra history/detail polling.


Rated battery parameter configuration uses vehicle registry IDs and native number
fields with V/Ah units. If a previously opened form still rejects a vehicle, close
and reopen it after the updated integration has been loaded. A file version alone
does not prove a running options flow has loaded that version; follow HACS restart
guidance when required. The integration never restarts Home Assistant itself.

Ride completion events use a conservative local stability policy: at least two
independent successful travel samples must report unchanged start/end, distance,
duration, energy and maximum speed over a ten-minute window. Cache rereads do not
count. This delays events and cannot guarantee an upstream final report; a later
correction updates telemetry but never emits the same ride again. Startup and
re-enabling establish a baseline, so historical rides are not replayed.

The original completed-event timestamp is retained through an unavailable state
for display on reload. This restoration never triggers automations. Old restore
data without a saved timestamp remains unknown rather than inventing one from
the ride's end time. No additional cloud polling or ride-detail requests are used.

2.0.0b39 uses an account-scoped SQLite archive for normalized month
summaries and scalar ride metadata. Ordinary travel polling and explicit queries
write the same archive. The previous v1 statistics file is validated and imported
once without modifying its original bytes; it is then a read-only migration
source. In-memory day statistics project only current and adjacent months, while
range Actions read the selected months directly from the archive.

`ninebot.sync_history` explicitly fills missing months in a range of at most
360 months. Target one vehicle device, set `operation: start`, `start_month`
and `end_month`; each call queries at most three missing months. The response
contains `job_id`, `next_month`, counters and coverage. Use `operation: continue`
with that job ID for the next batch, including after a restart. `status` reads
progress without cloud access; `cancel` stops the active call and preserves
already committed facts. One unfinished job is allowed per account; finish or
cancel it before starting another vehicle or range.

Known months, including partial reports, are reused locally rather than repeatedly
queried as presumed upstream pagination. `complete` means the selected month
range has been visited, not that every upstream ride or detail has been obtained.
Check `all_rides_complete`, `incomplete_months` and `unknown_months` separately.
Network failures retain the checkpoint with a 60–3600 second cooldown; respect
`retry_after_s`. The job does not poll in the background or resume cloud access
automatically. Month facts and their checkpoint are committed together. Sync
never requests ride details, tracks or vehicle controls.

The archive has a 100 MiB write budget and does not silently evict historical
rides at the previous 500-record limit. Storage exhaustion pauses writes while
retaining readable history. Unreadable/unsupported archives are preserved, and
ordinary vehicle telemetry continues with a storage Repair. Official Home Assistant backup pre/post hooks: archive writes
pause and earlier SQL operations drain before backup, while historical reads remain
available. A transient backup pause does not clear a capacity failure. Setup cannot
create another archive writer during backup. Account removal closes the actor and
removes only that entry's database/journal files; unexpected files or symlinks are
retained with a Repair. Removal during backup is deferred until its post hook.
These hooks prepare local storage; they do not upload archives or create a backup
agent. See the [official backup contract](https://developers.home-assistant.io/docs/core/platform/backup/).
Retain a complete configuration/storage backup, including identities, registries,
sessions and the private archive, when restoring or rolling back. A SQLite-only
copy cannot restore entity migration or account configuration. This archive does
not claim full upstream history.

`get_trips` and `get_history` prefer recorded closed months without another cloud
request. Current-month observations keep their original age; offline reads return
`current_sample_stale` rather than claiming a fresh sample. `get_statistics`
without refresh remains entirely local. Explicit refresh can correct existing
month facts. Details are fetched lazily, and only verified scalar detail facts
persist; raw JSON, tracks and speed samples remain in bounded runtime caches.
Historical list completeness and server totals remain separate, and omitted
rows in a partial report are not deleted from the underlying identity archive.
Ride IDs/timestamps remain private: protect configuration backups.

The five manual-history progress/summary entities remain retired; query scope
and progress stay in response data. No Recorder rows are deleted or reused for
different statistics. Native historical imports and trend examples follow below.

Yesterday distance uses the validated daily chart. Today/Yesterday ride count,
duration and energy require a complete retained monthly list, known end times
and current-report metric fields. Missing/evicted/partial lists stay unknown,
with a small availability reason; proven empty windows return zero. Historical
days require a report received after that day ended, rather than a partial
snapshot taken during it. Rides are assigned as a whole to their Asia/Shanghai
end date; no invented midnight split or proportional energy allocation is made.
Cross-month attribution needs both relevant month reports, which can still be
incomplete if the cloud duplicates or limits rows. Yesterday data keeps its
actual timestamp even if live travel temporarily fails.

Daily consumers add a cached, bounded adjacent-month query on the first two
business days only. Disabling every travel consumer stops regular travel polling.
Last-ride completion attributes describe the local stability policy; reported
ride totals are not proof of physical completion. Month/last-ride consumption per
distance sensors use the matching Wh/km ratio, not charging electricity. Missing
energy and zero distance produce unknown; no total-increasing state class is
assigned to resetting daily/monthly totals.


## Period statistics Action

`ninebot.get_statistics` accepts `device_id`, `start_month`, `end_month`,
`include_daily` (default true) and `refresh` (default false). One response covers
one to six months. The default reads the selected local archive months and sends no
cloud request. Use `sync_history` jobs to fill missing months in a longer
history, then query it in six-month windows; this Action is not a second scan
and cannot discover unqueried upstream history. Explicit refresh makes at most one cached/queued month
query per requested month, without fetching details or controls.

`months` contains server-reported totals; `days` contains business-day distance
and conditional ride count/duration/Wh. Each field has an availability reason.
A month total does not imply a complete ride list or a completed month.
`summary.complete_by_metric` means that every requested month reported that
metric, while `observed_after_all_period_ends` separately indicates reports
sampled after the selected month ends. Missing months make the corresponding
summary unknown, not a subset total presented as complete. Server day distance
and whole-ride end-date attribution can differ. First-day ride totals need a
verified adjacent-month report; its timestamp/revision is included.

Future padded days are omitted; actual zero and unknown remain distinct. Stored
source timestamps are preserved, including after reload. No ride IDs, raw JSON,
GPS trails or arbitrary attributes enter this response. The get_statistics response alone does not automatically become a dashboard data
source. The explicit import Action below provides native Recorder series.

This script returns the same response to a calling automation or Developer Tools
Action call. Replace the vehicle device ID and choose valid months. The
`response_variable` and `stop` contract is exercised in isolated HA tests.

```yaml
script:
  ninebot_period_statistics:
    alias: Ninebot period statistics
    mode: single
    sequence:
      - action: ninebot.get_statistics
        data:
          device_id: your_vehicle_device_id
          start_month: "202609"
          end_month: "202610"
          refresh: false
        response_variable: period
      - stop: Return period statistics
        response_variable: period
```

Calling it from another script with `response_variable: result` exposes
`result.months`, `result.days`, `result.summary` and `result.scope`. Large
response objects belong in response variables, not template-sensor attributes.


## Ride calendar and offline last ride

Each vehicle has one read-only **Rides / 行程日历** calendar. Use HA's Calendar
page or a native Calendar dashboard card to navigate dates, select day/month/list
views and open a ride for its times, distance, duration, maximum speed, overall
average speed and energy, when known. Coordinates and tracks are excluded.

```yaml
type: calendar
entities:
  - calendar.replace_with_your_vehicle_ride_calendar
initial_view: dayGridMonth
```

Use the actual entity ID from the vehicle device page; the account/vehicle naming
seed is stable. Browsing only reads the local archive. It never discovers missing
months, requests ride detail or writes to Recorder. Populate missing history
explicitly with `sync_history` or an existing historical query, then revisit the
date. Calendar `coverage: observed_subset` means recorded observations, not every
ride in the cloud. Omission from a complete revised list removes a ride from the
current calendar selection while preserving its archived evidence; omissions from
a partial list retain known rides. Missing/conflicting times do not become invented
all-day events. Future or ongoing rides are not presented as completed rides.

One date query supports up to five years, 5,000 scanned rides and 8 MiB of source
records. Larger requests report an error and require a shorter range, rather than
silently dropping events. Missing dates are empty, not zero-distance rides. The
calendar state stays `off` when no active/upcoming event exists: past rides are
accessed through date ranges, not copied into state attributes. New Core uses its
native event subscription API to refresh changed ranges; older HA, including
2026.1, supports normal date reads/navigation. Unrelated live telemetry does not
requery subscribed ranges.

Existing last-ride sensors retain their identities. A valid last ride can be read
from older archive months even during cloud/authentication failure. Archived
presentation includes its query month, actual stored receipt, `source: ride_archive`
and `ride_phase: reported`. The receipt is the last archived observation, not a
claim that each retained field was reported again or that the vehicle is online.
Fresh live values take precedence for the same/newer last ride. Cached history
never extends lock/GPS freshness or emits a new ride-completed event. No archive
or valid known ride remains unknown rather than a fabricated value.

Native calendar dates answer **when and what happened**. The following Recorder
trend cards answer **how distance/energy/count/duration changes by day or month**.
Historical Actions provide bounded lists, coverage and optional detail responses;
these views share one archive instead of creating one entity per ride.

## Native historical trend graphs

`ninebot.import_statistics` writes verified closed day/month aggregates from the
existing ledger to Recorder. It never requests the cloud. First use bounded
`get_statistics` with explicit `refresh: true`, or `get_history` continuation,
if you want to populate a specific missing range. Neither happens implicitly.
Normal current-month polling can already provide past days in the current month.

Call the import Action with response data:

```yaml
action: ninebot.import_statistics
data:
  device_id: your_vehicle_device_id
  start_month: "202609"
  end_month: "202610"
  include_daily: true
response_variable: imported
```

One call covers one to six months. `refresh: true` is rejected. Recorder must
already be running and HA's time zone must be `Asia/Shanghai`; in other time
zones use the query response until a safe chart mapping is supported. Native
statistics graph cards also require HA's `history` integration (normally included
by `default_config`). If a card reports that History is disabled, enable that
HA integration; importing statistics alone does not enable it.

The response lists up to eight `series`: day and month distance (km), energy
(Wh), ride count and duration (s), with stable `statistic_id`, source/skipped
point counts and queued point counts. Only available metrics are imported. A
month must have been sampled after its end; the current partial month is skipped.
A day must be over and meet the existing per-field availability rules. First-day
ride metrics need a verified adjacent month. Monthly energy is never spread
across days. Returned ride totals and server day distance can differ.

`dashboard_cards` contains ready-to-copy native card configurations with actual
statistic IDs. Paste one configuration into the dashboard's manual card editor
after Recorder processes the queue. No dashboard is changed automatically. The
cards use bars and `change`, with `period: day` for day series and `period: month`
for month series. They show a rolling window up to 730 days; older imports are
retained, but viewing them requires choosing a suitable longer display window.
Example (replace the ID with the returned **day distance** statistic ID):

```yaml
type: statistics-graph
title: 每日骑行里程
chart_type: bar
period: day
days_to_show: 90
stat_types:
  - change
entities:
  - entity: ninebot:replace_with_returned_day_distance_id
    name: 骑行里程
```

For monthly energy, use the returned **month energy** ID, `period: month` and
a larger `days_to_show`. Keep units in separate cards. Do not interpret hourly
views as an actual ride/energy distribution, or coarser sums of incomplete day
series as complete calendar totals. Missing days stay gaps; verified zero stays
zero. This follows the [native statistics card contract](https://www.home-assistant.io/dashboards/statistics-graph/)
and [Recorder metadata API](https://developers.home-assistant.io/blog/2025/10/16/recorder-statistics-api-changes/).

Each period's `state` is its value and `sum` is the running sum of known values.
Repeated imports replace the same periods. A correction rebuilds subsequent sums,
so a downward correction is not a reset or double count. Previously verified
points are retained when a newer response omits a value; import warnings state
this explicitly. Existing incompatible metadata/units, malformed periods or
more than 12,000 points per series reject the batch before queueing. Do not
manually repurpose these statistic IDs; removing/recreating an account yields
a new namespace, while renaming its vehicle does not change IDs.

`status: queued` acknowledges the Recorder queue, not a durable database commit.
Imports serialize per account with at most two active/waiting callers, and
revalidate device ownership before writing. Only normalized aggregates enter
Recorder; no rides, coordinates, trails or raw JSON. Normal entity IDs, names
and their recorded history remain unchanged. Offline SQLite and the real
statistics WebSocket contract are tested. Native day/month cards also rendered
in an isolated HA 2026.1 frontend using synthetic data; no production dashboard
or vehicle control was exercised.


## Canonical entity IDs and upgrade

Starting with b36, new entities use:

```text
<platform>.<account_slug>_<vehicle_model_slug>_<serial_slug>_<stable_key>
```

ASCII model tokens are preferred over translated vehicle nicknames. Non-ASCII,
lossy punctuation and long inputs use a deterministic hash suffix; no arbitrary
`_2` is chosen to resolve an occupied target. Naming seeds are saved once per
account/vehicle, so nickname, account-label and model changes do not alter IDs.
Entity names remain translated and independent of these IDs. New accounts have
business-identity-scoped unique IDs/device identifiers; separate accounts can
own separate HA representations of the same serial number.

Upgrade preserves existing meaningful unique IDs and registry row IDs. Only
recognizable generated IDs on an exclusively owned vehicle are renamed in place
through HA's entity registry API. Custom/unverifiable IDs remain unchanged.
Conflicts create a Repair and retain the original row; the integration does not
claim another entity or use a generated numeric suffix. A private versioned
identity store contains frozen seeds and an old/new migration journal. Intent
is committed before registry mutation; reload reconciles interrupted renames.
Missing/corrupt established identity storage pauses setup rather than silently
creating fresh identities. Back up the entire matching HA configuration/storage;
restoring only integration code does not undo this migration.

Inspect the local mapping using a response-capable script or Developer Tools:

```yaml
action: ninebot.get_entity_migration
data:
  device_id: REPLACE_WITH_HA_VEHICLE_DEVICE_ID
response_variable: identity_mapping
```

The response contains `object_prefix`, `migrations[]` (registry ID, unique ID,
old/new entity ID, status and reason) and a reference warning. It makes no cloud
request. It contains local identity information; keep it private. Update YAML,
templates and external dashboards using renamed IDs. Automatic rewriting of
all references is not promised. Diagnostics contain migration counts/statuses,
not account, serial or ID mapping values.

HA Recorder handles same-domain rename metadata; isolated regression tests
verify state history and native statistic metadata identity. This does not
merge unrelated histories already stored under the target ID or move sensor
history into the lock platform. Starting with b38, conversion of reviewed unlocked/vehicle_lock binary sensors and seat_lock_raw sensors is journaled before public registry changes. Exactly one owned old identity is replaced; the UID string, custom name/icon/area/labels/aliases and user disabled/hidden choices are retained. Ambiguous identities, occupied targets and concurrent user changes are preserved with a Repair. No numeric suffix is invented.

The old binary/sensor registry ID cannot become a Lock registry ID. Recorder rows remain queryable under the old entity ID; new Lock states use locked/unlocked and start separate history. get_entity_migration includes conversion mappings and this boundary. Update YAML/templates/dashboard references and on/off conditions explicitly. Full configuration/storage backup is required for rollback: older versions cannot read the new conversion journal statuses. No production registry or database was edited during development.


## Local archive browsing

`ninebot.get_recorded_trips` is a read-only local Action for manual browsing or a
custom dashboard. Unlike `get_history`, it never fills missing months or refreshes
the current month. It works while live authentication is unavailable, provided
the account is loaded and the vehicle is still locally known.

```yaml
action: ninebot.get_recorded_trips
data:
  device_id: REPLACE_WITH_VEHICLE_DEVICE_ID
  start_date: "2026-09-01"
  end_date: "2026-10-08"
  limit: 20
response_variable: recorded_trips
```

Dates are inclusive in Asia/Shanghai, independent of the HA display time zone.
One call returns at most 100 scalar rides, ordered by start time and ride ID;
ongoing/future rides and conflicting or absent times are excluded. The maximum
range is five years (1,830 days). Narrow the range if the 5,000-row scan or 8 MiB
read budget is exceeded; there is no silent truncation or automatic retry loop.

The response includes `source_mode: ride_archive`, `revision`, `rides[]` with
`received_at`, `precision`, field provenance and units, `months[]`, and `coverage`.
Coverage lists missing, partial and unknown months. `all_observed_lists_complete`
only describes stored lists, **not complete upstream history or finalized rides**;
`upstream_history_complete` remains `unverified`. A month can contain malformed
rows without usable timestamps. No track, precise location or raw JSON is returned.
The local page uses the same selection as the calendar: latest lists plus retained
observations from partial lists. Complete-list corrections can remove a ride from
the current view while retaining its historical evidence in the archive.

Pass `next_cursor` as `cursor` with the same device and dates to get the next page.
A cursor preserves the first page's `as_of`, is held only in this account's memory,
expires after 15 minutes, and can be evicted when eight continuations are in use.
An archive content change (including another vehicle in this account), reload or
range change requires starting again without a cursor. An observation timestamp
refresh without content changes does not invalidate the cursor. The Action does
not retain response pages, copy rides into state attributes or write to Recorder.

For missing months, explicitly use `sync_history` and its bounded continuation
contract. To retrieve one cloud detail, explicitly call `get_trip_detail` with the
selected `ride_id` and `query_month`. Neither is implicitly called during local
browsing. Point speed/distance units and coordinates remain unverified.

Local browsing and `get_trips`/`get_trip_detail` check standard HA read permissions.
A normal full reader is allowed; a restricted user needs read access to the selected
vehicle's Ninebot calendar, and its tracker for a GPS response. Unrelated telemetry
access alone does not grant trip history. These checks run before querying and
again before returning. Internal automation calls without a user context retain
HA's normal trusted automation behavior. Existing coordinate opt-in is still
required for GPS; automation traces may retain an explicitly requested track.

**简体中文：** `get_recorded_trips` 按上海时区的起止日期（含两端）仅读取本地
档案，每页最多100条，不自动补采缺月、不拉轨迹、不调用控制。返回来源、采样时间、
原始精度和缺月/部分/未知覆盖；已存列表完整不等于云端全部历史。继续分页时带回
`next_cursor`，档案修订、重载或游标过期后从第一页重新读取。缺月同步和单条云端
详情必须分别显式调用。账号/车辆实体身份及既有远程锁轮询逻辑保持不变。


## Optional ride detail card

After loading b43 or later, manually add a dashboard **JavaScript module** resource:
`/ninebot-static/ninebot-trip-card.js`. The integration serves only its packaged
assets using HA's async static API; it does not install resources or change your
dashboards. Reload the browser after an integration update. No separate download,
root-directory runtime dependency or external map/chart service is needed.

```yaml
type: custom:ninebot-trip-card
entity: calendar.REPLACE_WITH_NINEBOT_RIDE_CALENDAR
limit: 20
# Optional fixed dates; otherwise the current Shanghai business month is used.
# start_date: "2026-09-01"
# end_date: "2026-09-30"
```

Choose exactly one vehicle: its Ninebot calendar `entity`, **or** `device_id`
instead of `entity`. Optional `title` changes only the card heading. `limit` is
1–100. Editing dates requires clicking **Read locally**; browsing and Next page
read one local archive page, never backfill missing months. HA state updates do
not repoll history. Use the existing explicit `sync_history` Action for gaps.

Each ride expands distance, duration, timestamps, server maximum speed, overall
distance/duration average speed, Wh and the actual observation receipt. Unknown
values display a dash. Coverage remains an observed subset even when no missing
month is listed. Dates select Shanghai business days; times use HA's display
time zone. Source paths and parser information are available in a separate
disclosure. Source precision is retained; average speed is a calculated metric.

**Fetch cloud detail** explicitly requests only the selected ride/month and
uses the existing backend/cache. It requests no coordinates. Verified raw speed
samples can produce a small curve against sequence, labeled **unit unverified**;
this is not a calibrated speed-versus-time plot or a GPS route. Sample means do
not replace the server maximum or overall average. Unverified electricity usage
is shown separately with an unknown-unit label.

The browser keeps one scalar page and up to five current-page details, at most
500 speed samples each; no tracks/raw payloads/localStorage. User, connection,
vehicle or date changes discard old results, including late responses. Next
page cursors can expire or be invalidated by archive changes; read from the first
page again. Disconnect/reconnect performs at most one local page read.

All historical queries, statistics reads and migration reports use standard HA
read permissions for the selected vehicle's Ninebot calendar (or full-reader
permission). Synchronization changes and Recorder imports require control
permission; `sync_history` status remains read-only. Checks apply before work
and before returning, with the existing account/vehicle generation guards.
Trusted internal automations keep HA's normal no-user-context behavior.

**简体中文：** 手动添加上面的JavaScript模块资源，再添加自定义卡片；用车辆的
Ninebot行程日历或device_id选择一辆车。打开、改日期后点击“本地读取”和翻页只读
本地档案，不自动补月。展开显示单趟指标；“获取云端详情”才按需查询这一趟，
不请求GPS。速度采样曲线明确标注单位未确认，不冒充实测速度时间图或地图。
缺月使用显式sync_history；同步修改/统计导入需要对应车辆的控制权限。


## b44 display and naming update

On upgrade, loaded vehicles' existing entity IDs, including pre-upgrade custom
IDs, are migrated once to `domain.account_ascii_vehicle_serial_field`. Initial
configuration uses the same rule. Naming seeds and unique IDs remain stable;
registry renames use HA's public API and retain a durable old/new mapping.
Previous naming plans remain in the identity journal. Target collisions, foreign
ownership and concurrent registry changes are preserved with a Repair, rather
than deleting or overwriting another entity. Use `get_entity_migration` to review
the mapping and update YAML, templates or external dashboards referring to old
IDs. The migration does not promise an automatic rewrite of those references.
Subsequent user renames are not reapplied on every reload.

Each account title is normalized to `username：account[region]`. Login parses only
the reviewed whoami username/region fields. Existing accounts missing a username
try one display-enrichment read on successful setup; failures preserve cached
metadata and do not start a retry/polling loop. Separate accounts retain separate
ConfigEntries and sessions. An unreported username/region is not fabricated.

The Add account button is integration-localized. The section heading beneath
the search box is generated by HA from the manifest's integration type; current
HA provides no integration-specific Account heading override. This release does
not patch global frontend translations, inject DOM replacements or claim that
heading has changed.

Distance/range display suggests one decimal; energy consumption and maximum
speed suggest integers. Zero suggests zero decimals. Native values, source
precision, archived facts and calculations remain unrounded. Ride durations
keep native seconds and suggest minutes for display, with the original seconds
available as `duration_seconds`; the optional detail card shows minutes and
integer seconds. Native duration rendering and explicit user precision/unit
overrides follow the installed HA frontend. Nominal energy is a specification,
not consumption: its existing kWh unit keeps three decimals.

Main battery fields use consistent names in shipped locales. Battery type codes
are unchanged; Chinese names are shortened to 铅酸/锂. Health score 0 and smart
service remaining value 0 display **Not supported** by maintainer request; they
do not establish a new protocol capability or convert score to SOH. Cycles display
Not supported only when the corresponding cycle-support flag is explicitly false;
unknown support remains unknown. These are translated scalar states without a
numeric device class/unit/state class, avoiding an invalid numeric sensor.
Unreported odometer displays **Not reported**, without inventing distance units.

Vehicle Image is diagnostic. Device Location remains a native GPS tracker linked
to its vehicle DeviceInfo, so HA Map/Zones can consume it. Position display defaults
on while respecting previous opt-outs; valid latitude/longitude and freshness
are still required. No coordinates are copied into unrelated attributes and
no unverified coordinate conversion is performed.

**简体中文：** 本版会在升级加载时一次性强制规范已有实体ID（包含旧自定义ID），
保留unique_id和迁移映射；引用旧ID的模板/自动化等需按映射调整。数值仅调整显示，
原始值不舍入；骑行时长默认分钟、保留秒，详情卡按分/整数秒显示。主电池名称统一，
健康评分0/服务剩余0显示“不支持”，明确不支持循环计数时循环数也显示“不支持”；
总里程缺报显示“未上报”。账号标题补充真实用户名，搜索框下的全局“中枢”标题
没有集成专属修改接口，本版不冒称已改成“账号”。按维护者要求未运行功能测试，
没有采集云数据、修改生产HA或执行控制。
