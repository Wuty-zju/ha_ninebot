# Ninebot integration behavior

This page describes the current user-facing contract. Developer investigations and local raw samples belong in a separate private workspace; they are not required to install or run this integration.

## Authentication

Pinned ninecli==0.1.7 runs through a managed authenticated loopback server. Passwords travel in the local request body, not argv or ConfigEntry storage. Each entry has isolated sessions and reauth. Vehicle discovery has a controlled native cache initialization exception. SMS flow is implemented and covered offline; live SMS verification remains pending.

## Entities

Created entities are enabled and visible by default; user choices are preserved. Location, controls and debug views require their options. The range sensor prefers precise, then estimated, then AI. b24 removes low-confidence SOC cumulative estimates and duplicate ranges. Nominal V/Ah define rated energy, not measured capacity or SOH. Existing meaningful IDs and Recorder history remain stable.

BMS voltage/temperature/cycles require valid data and support. Energy ec is Wh and charging_power is W according to maintainer confirmation. Health score is not SOH. Unknown enums and missing reports remain unknown. GPS needs two valid coordinates; the coordinate system is unverified and is never automatically converted.

## History

get_trips, get_trip_detail and get_history return bounded response data. Local pagination covers only received rows; it does not prove all upstream rides were returned. Server maximum speed and distance/duration average are distinct. Point-speed/distance units remain unverified. Tracks require coordinate opt-in and include_track; automation traces may retain response locations. Events establish a baseline and suppress duplicates; cloud timing or incomplete lists can cause missed events.

## Controls

Controls require explicit enablement, allowlist and fresh ownership/status. Known denial and ambiguity block dispatch; unknown permission stays unknown and the cloud decides authorization. Each command is sent once with bounded state reconciliation, without automatic replay or optimistic state updates. Engine commands are not lock/unlock; acceptance does not prove physical completion.

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

Normalized month summaries and small ride metadata are now retained in an
account-specific Home Assistant storage file. Ordinary travel polling and explicit
history queries update the same ledger; this does not start a full-history scan or
add requests. Restoration is cached data, not a new successful cloud sample.

The ledger is bounded to 360 month summaries, 500 ride records and 2 MiB per
account. Monthly server totals remain separate from returned-list coverage;
evicted/missing ride rows cannot prove complete daily totals. Corrections replace
records instead of accumulating them again. Raw JSON and location trails are not
stored there, but ride IDs/timestamps are private history: protect your Home
Assistant backups. Removing the account integration also removes this ledger.

An unreadable/unsupported file is preserved and only statistics storage is paused,
with a Repair explaining recovery. Vehicle telemetry continues. The five manual-history progress/summary entities are retired; query scope and
progress remain in get_history response data. This does not delete Recorder rows
or reuse those identities for different statistics. Historical Recorder imports
and trend examples are a separate upcoming change.

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
one to six months. The default reads the existing bounded ledger and sends no
cloud request. Use the existing `get_history` continuations to fill a longer
history, then query it in six-month windows; this Action is not a second scan
or full-history archive. Explicit refresh makes at most one cached/queued month
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
GPS trails or arbitrary attributes enter this response. Historical Recorder
trend import and graph examples are still a separate H4 increment; an Action
response alone does not automatically become a dashboard data source.

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
