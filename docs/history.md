# Ride history, maps and statistics

[简体中文](history_zh.md) · [Setup and options](guide.md) · [Documentation](README.md)

This guide covers releases through the 2.2.8 preview. For the stable release, use the [v2.2.0 edition](https://github.com/Wuty-zju/ha_ninebot/blob/v2.2.0/docs/history.md).

> **Map and cards: early alpha.** Navigation and linked views are under active development. The instructions below cover the current implementation; synchronization, storage and Actions work independently.

## Choose the right view

| What you want | Use | Data source |
|---|---|---|
| Current vehicle position | Device location tracker / HA Map | Fresh cloud telemetry; Recorder can retain observations. |
| A ride or several routes with linked metrics | Bundled history map card | Saved account archive. |
| Rides on dates | Native Calendar | Local recorded rides; no GPS or implicit cloud fill. |
| Day/month trends | `get_statistics` and explicit `import_statistics` | Verified aggregates; native statistics-graph after import. |
| Automations or a custom dashboard | Response Actions | Bounded lists, detail chunks, provenance and coverage. |
| Trip reports and vehicle transitions | Native Event entities | New or revised trip reports, confirmed completion and concrete status transitions after a baseline. |

Historical rides and point arrays belong in the archive and bounded responses, not entity attributes or thousands of per-ride entities. Opening a history view does not increase live polling.

## Enable preservation and synchronization

In account options, review **Automatically sync trip details (Beta)**, **Automatically scan trip history (Beta)**, storage quota and history request budget. Calendar, preservation and automatic sync are Beta features and default off for new accounts; existing choices are retained. Enable preservation and automatic sync to start background acquisition. Turning preservation off keeps saved data but suspends background work. Location response/display consent is a separate option; protecting archived GPS/backups remains necessary.

First discovery persists a fixed baseline from the client's `200001` month lower bound through the bootstrap month for each owned vehicle. This is a **client search policy**, not an official earliest-history boundary. Empty months do not prove earlier history is impossible. Work is resumable and budgeted, so a first scan may take a long time. New months, current/previous-month changes and older gaps have separate incremental scheduling; stable saved details are reused instead of continually downloaded.

Month pages and detail requests share the durable account budget (default 60 logical calls/hour; numeric input 1–120, or 0 for no hourly quota, not recommended), with fairness between vehicles and priority below live telemetry/controls. Retries/backoff, authentication, pause, backup and storage guards apply. Restarting does not reset a positive request budget. Zero only removes that quota; request serialization and upstream backoff still apply. Recent details can receive bounded stability follow-up; late arrivals/corrections do not require rescanning all stable months. Within the local 150–180-day risk band, pending details prioritize older confirmed ride ends; this is not a guaranteed server deletion deadline.

### Manual and incremental controls

The vehicle's **Sync history / Pause history sync** diagnostic buttons (Sync also resumes paused work) use the same scoped Action. They do not operate vehicle locks. History mutations require HA control permission; status is read-only. Preservation must be enabled; manually requested work can proceed while automatic sync is off without enabling new automatic scans.

```yaml
action: ninebot.sync_history
data:
  device_id: REPLACE_WITH_VEHICLE_DEVICE_ID
  operation: synchronize
  # Optional: target 1–12 distinct months rather than rescan everything.
  months: ["202609", "202610"]
response_variable: sync
```

Repeated requests join active work. `force: true` reschedules selected month lists (current month if omitted), not destruction/redownload of protected geometry. Read `operation: sync_status`; use `pause`/`resume` to preserve checkpoints. A currently in-flight request cannot be unsent, but stale/paused results are fenced before commit.

Existing `start`/`continue`/`status`/`cancel` range jobs remain supported (up to 360 selected months and three missing months per call). `repair`/`repair_continue`/`repair_status`/`repair_cancel` target sparse months. They share request budgets and do not certify a complete cloud history simply because the requested months were visited. Prefer `synchronize` for current incremental operation; do not repeatedly start overlapping jobs.

### Read progress correctly

- **Sync state** describes the current acquisition phase. Waiting for request budget is local scheduling, not an application for Ninebot permission. Inspect `next_request_at`, `retry_at`, `sync_phase` and errors before intervening.
- **Month-scan progress** separates processed, verified and gapped months. Page totals, membership and references must agree. A conflicting or invalid month in a response keeps that response out of statistics. History scans retain the captured page as evidence of the gap.
- **Saved-detail progress** covers discovered ride IDs. 100% does not mean all cloud rides were discovered or that every saved route has a full geometry.
- **Synced with limited tracks** means the baseline scan and detail saving have completed. Some saved routes are simplified, consist of a single point, or have missing, invalid or incompletely verified geometry. See `simplified_tracks`, `single_point_tracks` and `unavailable_tracks`. These counts can overlap. **Completed with gaps** covers unresolved month scans.
- **Last successful sync** records accepted page/detail progress; task completion is separately identified by `last_completed`. A historical completion time is not erased merely because current verification is pending.
- **Month trip list completeness** uses verified pagination when the saved page chain matches the displayed observation for that vehicle and month. Local reconciliation can also establish coverage when retained rides agree with that observation's count and total distance and meet the time checks. Otherwise the value is the fraction of rides returned in that response. See `coverage_basis`, `returned_coverage` and `verified_pages`.

Missing/foreign vehicles are not queried through stale ownership. Saved evidence remains, and a positive rediscovery can resume the checkpoint; absence from one accepted list does not prove physical unbinding.

Synchronization progress sensors and controls appear in device diagnostics. Sync state and last-success entities additionally require debug mode; the calendar follows its own option. A diagnostic category does not change Recorder retention.

Each vehicle also has two archive counters in **Diagnostics**. **Trip count** counts distinct trips saved locally across all months, including trips with a summary only. **Simplified trip count** counts trips whose best saved route is marked as simplified by the server. A retained detailed route continues to count as detailed after the server starts returning only start and end points. Missing details and unclassified routes are excluded from the simplified count. The `details_pending_classification` attribute reports saved details whose quality metadata still needs to be checked. The counters work with background sync and debug mode off.

## Add the history map

The current-position Map card draws tracker history recorded by HA. The bundled Ninebot card draws the archive’s saved geometry on the same HA map component and offers ride selection and replay. A calendar entity or a Ninebot device ID supplies the target.

1. Install/load the integration. Review location/history options above.
2. In **Dashboard → Resources** (enable advanced mode if needed), add `/ninebot-static/ninebot-trip-card.js` as **JavaScript module**.
3. Add a manual card and choose your vehicle's actual calendar entity or HA device ID:

```yaml
type: custom:ninebot-trip-card
entity: calendar.REPLACE_WITH_VEHICLE_RIDE_CALENDAR
limit: 20
```

The resource and visual editor are bundled; no additional card repository is required. The integration registers a fixed static path, **not** dashboard resources/cards automatically. For `Custom element not found: ninebot-trip-card`, check that the resource loaded as a module, that the integration is loaded, then hard-reload the browser/app frontend. Find the installed integration version on its integration page.

The card places the ride library next to the shared map on wide cards and below it in narrower columns. Distance, duration and energy follow the focused ride. The player sits over the map; the scale ruler helps put a route in context.

Use `device_id: REPLACE_WITH_VEHICLE_DEVICE_ID` instead of `entity` if the calendar feature is off. For multiple vehicles (up to five, including different loaded accounts):

```yaml
type: custom:ninebot-trip-card
title: Ride history
vehicles:
  - entity: calendar.REPLACE_WITH_FIRST_VEHICLE
  - device_id: REPLACE_WITH_SECOND_VEHICLE_DEVICE_ID
    name: Second vehicle
limit: 40
# Optional inclusive Shanghai business dates:
# start_date: "2026-09-01"
# end_date: "2026-09-30"
```

`limit` is the **total** page size (1–100), including sizes smaller than the vehicle count. Lists merge genuinely newest-first; each vehicle has a bounded source buffer, and an empty vehicle does not restart. Date/vehicle changes require a local read; ordinary HA telemetry updates do not requery history.

### Replay saved routes

1. Choose vehicles and a business-date range in **Filters**. A row opens one ride. **+** adds it to the comparison and **×** removes it. Compare up to five authorized rides across vehicles, accounts or months. With the local archive connection active, existing selections survive a date change. The last five list pages are reusable.
2. In the **2.2.8 preview**, each compared route with saved geometry gets a numbered marker on the same map. Click or keyboard-activate its marker or choose the corresponding numbered chip. Focus switches the summary, point cursor and playback to that ride. Camera position and previously read geometry are reused. The chips also work when markers overlap.
3. Read distance, duration and energy beneath the active route. Expand **Selected rides total** for the same quantities across the selection. These totals cover the selected list records only. A quantity with any missing contributing measurement shows a dash. A confirmed zero remains zero.
4. Use **Play**, pause, restart or drag the sample slider. The cursor advances through saved points in sequence at a preview pace. Gaps break the drawn line. Point timestamps and a measured speed for each segment are unavailable. The live vehicle position comes from the separate device tracker.
5. **Follow replay** centers the current point and follows it at your zoom level when it leaves the view. **Show all routes** fits the routes above the playback controls. **Local records updated · Read latest** reloads the selection after the archive changes.

Each detail read uses up to four chunks of 512 source rows. **Read more saved points** continues from the next offset. The browser retains at most 4,096 samples per ride. The card can simplify lines for drawing while the archive keeps its original geometry.

A server-simplified ride may have two usable positions. The map marks the start and end individually. One position gets one marker. A ride without positions still has a summary. The server may report a detailed-track window of 180 days. Previously saved higher-quality routes remain available locally beyond that window.

If the local archive lacks a detail, enable background detail sync or fetch that ride once using `ninebot.get_trip_detail` in **Developer tools → Actions**. Check the persistence result, then read the local records again. [HA Map](https://www.home-assistant.io/dashboards/map/) · [Home Assistant Map Tiles](https://www.home-assistant.io/integrations/map_tiles/)

### Map privacy and units

When Home Assistant supplies its Map Tiles integration and compatible map frontend, the card uses the default HA basemap and its light/dark styling. Tile requests follow Home Assistant's normal map service. On older frontends, a bundled [Leaflet](../custom_components/ninebot/frontend/vendor/leaflet/LICENSE) view shows and replays the routes without a basemap. [Home Assistant Map Tiles](https://www.home-assistant.io/integrations/map_tiles/)

Enable **Show vehicle location** to display saved coordinates. The card plots them in their original coordinate system, which has yet to be verified. Per-point speed and the fourth trail column (`distance_delta_raw`) remain available through the detail Action with their raw values and unverified units.

The card clears private tracks on a user, connection or vehicle-filter change, loss of archive access, or removal from the dashboard. A date edit keeps selected routes only while the local archive subscription is active. List pages expire when the archive changes. Track geometry stays in browser memory for the open card. GPS returned by an explicit Action can also appear in automation traces.

Place native calendar or statistics cards next to the replay card for longer-term views. Earlier YAML with `statistics_entities` still loads. Configure the corresponding statistics graph as a separate card.

## Ride summaries and vehicle events

Numeric day/month/latest-trip sensors retain their meaningful identities, units and history. Use **average consumption** for Wh/km; today/yesterday averages divide energy by distance from the same complete ride set, never mix a chart distance with partial-list energy. No rides or zero distance yields no average. Monthly/today/yesterday keep distance, trip count, duration, energy and average consumption. Latest trip is one record, with distance, duration, energy, average consumption and maximum/average speed; it does not need a redundant count-of-one sensor.

An empty response that conflicts with activity already observed for the same month leaves the saved values and their original observation time in place. The coordinator retries through its normal schedule. For vehicles identified on the motor business line, paging uses the same route as the monthly query. A resumed history run completes a partially saved month on that month's original route.

**Vehicle trip event** replaces the two standalone latest-start/end timestamp sensors. Read `latest_start_time`, `latest_end_time` or the small GPS-free `latest_ride` object from the existing event entity. Event identity stays unchanged; update automations that referenced the retired sensors. The event timestamp is when HA observed an event, not the ride's physical start/end. `reported` means a new recent cloud record, `updated` a revised report, `completed` a stable completion, and `late_reported` a late completion report. Power-on is not inferred to be a ride start. Historical imports update the latest-data attributes without replaying ride events.

**Vehicle state event** emits the concrete type `powered_on`, `powered_off`, `charging_started`, `charging_stopped`, `locked`, `unlocked`, `seat_locked` or `seat_unlocked`. Each transition is published separately; `changes` retains the full same-receipt batch and `summary` describes that transition. `observed_at` is cloud observation time; `observation_revision` identifies the receipt. The legacy `state_changed` type can restore an old display but is no longer emitted. Update automations filtering that generic type.

The default-on **Enable vehicle events (Beta)** option controls both entities. User-disabled entities remain disabled. Startup, re-enabling, reconnection, invalid values and observation gaps establish silent baselines. Completion keeps its durable deduplication; live report/update notifications use a bounded runtime cursor and are not a physical push stream. No extra cloud polling is created.

Duration sensors keep seconds internally and suggest hours with **four decimals** for display. The initial migration runs once. Later changes to units and precision follow user settings. The replay card formats duration from seconds, for example **45 sec**, **31 min 46 sec** or **1 hr 2 min 3 sec**.

Diagnostic entities can appear in Recorder. Use explicit Recorder filters to exclude them. See [coverage and statistics](#coverage-and-statistics) for how monthly and daily totals are validated.

## Query Actions

Use Developer Tools → Actions or a response-capable automation/script. Targets are **HA vehicle device IDs**, not Ninebot serials. Month input is `YYYYMM`; dates are inclusive Shanghai business days. [services.yaml](../custom_components/ninebot/services.yaml) is the authoritative field list.

| Action | Use and limits | Cloud / write behavior |
|---|---|---|
| `ninebot.get_recorded_trips` | Date-scoped local pages, `limit` 1–100, optional `sort: desc` | No cloud; returns scalars, receipts, precision, provenance, coverage and `detail_saved`. |
| `ninebot.get_trips` | One month's rides; page/limit and optional details | Local-first; explicit refresh or missing data can query cloud. Returned rows do not prove full pagination. |
| `ninebot.get_trip_detail` | Ride ID plus verified query month; optional point chunks | Local-first by default; `local_only: true` guarantees no cloud fallback. |
| `ninebot.get_history` | Bounded month-range continuation | Can fill missing months; not the same as a local date page. |
| `ninebot.get_statistics` | 1–6 months, optional daily data | Default local-only; `refresh: true` explicitly queries needed month observations, not details. |
| `ninebot.import_statistics` | Verified closed day/month aggregates | Writes to Recorder; no cloud and no `refresh: true`. |
| `ninebot.sync_history` | Incremental/range/sparse tasks, progress, verification/reconstruction | Operation-specific: sync can query cloud; verify/reconstruct are local-only. |
| `ninebot.get_entity_migration` | Identity mapping | Local-only, private identity data. |

```yaml
action: ninebot.get_recorded_trips
data:
  device_id: REPLACE_WITH_VEHICLE_DEVICE_ID
  start_date: "2026-09-01"
  end_date: "2026-10-08"
  limit: 20
  sort: desc
response_variable: rides
```

Use `next_cursor` as `cursor` with identical device/date/sort. Default Action order remains `asc` for older automations. Descending cursors are single-use after successful authorization/read, expire after 15 minutes and are bounded to eight continuations/account. Local date reads support at most 1,830 days, 5,000 scanned rides and 8 MiB per call; narrow the scope on a budget error. Future/ongoing/conflicting/undated rides are not fabricated into completed history.

Descending continuation versions track the relevant vehicle catalog; unrelated vehicle/progress updates do not invalidate them. Ascending compatibility keeps its account revision fence. Reload/reopen invalidates cursors. Receipts/coverage are fresh per response, not an immutable database snapshot across calls/accounts.

```yaml
action: ninebot.get_trip_detail
data:
  device_id: REPLACE_WITH_VEHICLE_DEVICE_ID
  ride_id: REPLACE_WITH_RIDE_ID_FROM_RECORDED_LIST
  query_month: "202610"
  local_only: true
  include_track: true
  point_offset: 0
  max_points: 500
response_variable: detail
```

Each detail response allows 1–2,000 points; continue from `track_summary.next_offset` even through invalid rows. Coordinate opt-in and history/tracker read permissions are required for GPS. `local_only` cannot be combined with `refresh: true`. A cloud fetch needs current live access and a unique verified vehicle/month/detail mapping: call Get trips with the correct month first if mapping is absent. Do not guess another vehicle's detail ID.

Response `field_states`, sources, units, precision and `detail_context` distinguish missing/null from zero, list facts from detail facts, geometry receipts from newer scalars, and local partial integrity from complete data. Arbitrary full raw JSON is not an Action response. Identical pure local detail reads can share only their in-flight work; consumed page cursors remain independent. Cloud calls are not automatically replayed by the card.

Restricted HA readers need read access to the vehicle's Ninebot calendar and, for GPS, tracker. Full readers follow standard HA permissions. Checks run before work and before returning; ownership/generation still applies. Synchronization mutations/statistics imports need control permission. Trusted no-user-context automations keep normal HA behavior.

With history-detail synchronization off, enabled daily sensors can still fill a capped current-month list one page per account update. This uses the shared hourly budget, ownership/pause checks and bounded retries, and stores normalized list facts only. Full-history discovery and detail/track downloads remain off. Complete local indexes are reused; missing or conflicting metrics remain explicitly unknown rather than being replaced with zero.

## Coverage and statistics

Cloud monthly totals, the latest returned list, accumulated local rides, saved details, geometric quality and integrity checks are **separate evidence scopes**. Stored list completeness or a zero missing-month count never proves the upstream's entire history. `upstream_history_complete` remains unverified.

Today/yesterday count, duration and Wh need a verified complete selection or locally accumulated IDs/count/distance that reconcile with the month; energy and duration additionally reconcile independently. Missing/conflicting fields stay unknown; proven empty windows return zero. Rides are attributed wholly to their Shanghai **end date**, with no invented midnight allocation. Month boundaries can require adjacent-month evidence.

Daily distance prefers the server's daily mileage chart (`server_daily_chart`); when that value is missing, verified ride distances can supply `returned_unique_rides`. Count, duration, energy and matching ride-based average consumption use the whole-ride end-date selection. Check the returned `distance_basis` before comparing these projections. Recorded-trip pages select rides that overlap the requested dates, so page membership is a browsing scope rather than a daily-statistics attribution rule.

Numeric entities retain verified values for the same date/month through a short outage or restart. Attributes preserve the actual receipt time and distinguish `current`, `cached`, `awaiting_day_close` and `reported_history`; cached values do not claim a new cloud observation. Yesterday can show its last verified same-day report while waiting for a closing report. A new day/month never inherits the previous period's totals. After setup and at Shanghai midnight, demanded trip data is scheduled promptly through the existing coalesced requests and backoff. Archive updates notify affected entities without another poll.

Strict `get_statistics` daily responses and Recorder imports still require post-day-close observations for historical days and sufficiently fresh current-day evidence. Entity display continuity does not certify a period as complete for historical import. A first-ever account can remain unknown while its index is being filled; zero-distance average consumption is undefined, with `availability_reason: zero_distance` rather than an invented average.

`get_statistics` separates server month totals from conditional daily values; omitted future padding is not a zero. Missing months invalidate the affected complete-range summary. Wh/km is matching `sum(Wh) / sum(km)`, not mean per-ride ratios; zero distance cannot produce a valid intensity. Daily/month totals reset, so they are not forced into `total_increasing` entities.

### Native charts

```yaml
action: ninebot.import_statistics
data:
  device_id: REPLACE_WITH_VEHICLE_DEVICE_ID
  start_month: "202608"
  end_month: "202609"
  include_daily: true
response_variable: imported
```

Import reads existing data and queues up to eight native series: day/month distance (km), energy (Wh), ride count and duration (s). Recorder must run; HA timezone must be `Asia/Shanghai`. Only closed verified periods enter; no spreading monthly Wh across days. `status: queued` means queue acknowledgement, not a durable commit. Repeated imports compare the committed values, cumulative sums and metadata first. Identical series return `status: unchanged` with `unchanged_points` and queue no writes; chart configurations remain available. Changed periods replace previous values, and corrections rebuild later cumulative sums without double counting. Missing updates retain prior verified points with warnings. Incompatible metadata/units or over 12,000 points/series reject the batch.

Native statistics IDs currently include the integration entry identity. Removing and re-adding an account reconnects its ride archive, but a subsequent statistics import can use a new series. Existing Recorder series and dashboard references remain intact. Keep the returned IDs when maintaining charts; account-stable series migration will require an explicit mapping rather than automatic merging.

Use returned `dashboard_cards` or the actual returned `statistic_id`:

```yaml
type: statistics-graph
title: Daily distance
chart_type: bar
period: day
days_to_show: 90
stat_types: [change]
entities:
  - entity: ninebot:REPLACE_WITH_RETURNED_DAY_DISTANCE_ID
    name: Distance
```

Enable HA's History integration for statistics cards. It normally loads through `default_config`. Use `period: month` for monthly series and a separate card for each unit. Native graphs and calendars have their own date controls. Adjust the display window to see older imports, or call `get_statistics(refresh: false)` for a specific range.

### Calendar and events

```yaml
type: calendar
entities: [calendar.REPLACE_WITH_VEHICLE_RIDE_CALENDAR]
initial_view: dayGridMonth
```

The read-only calendar shows valid past ride times/metrics from local storage. It does not fetch missing months/details or write Recorder. Its current state may be `off` when only past rides exist; browse dates rather than attributes. Complete-list withdrawals can remove current selections while preserving evidence; partial omissions retain known rides. Queries are bounded to five years, 5,000 rides and 8 MiB. Newer HA can subscribe to changed calendar ranges; older supported HA can navigate/read normally.

Trip Event state is the timestamp of the most recent report/update/completion event, not last-ride end. Completion requires at least two independent successful travel observations with the relevant ride facts unchanged for at least ten minutes; startup/re-enable sets a baseline and does not replay historical rides. IDs are durably deduplicated; later corrections can emit a report-update event, but never repeat the completion event. Unknown before the first new event is expected. Restored event timestamps never trigger automations; rides during downtime can be missed.

A native `type: map` card with the location tracker and `hours_to_show: 24` displays positions recorded by Home Assistant. Open the bundled history map to play a trip saved in the account archive.

An interrupted historical month read may already have a complete verified page on disk. Once its claim or retry delay expires, the scheduler checks local page evidence first. A matching page advances discovery even when the hourly budget is exhausted. Active claims and future retry deadlines still take precedence.

## Verification and recovery

The account-scoped SQLite archive holds normalized facts and credential/profile-redacted compressed business evidence. Each detail can retain the latest payload and independently selected better geometry; a newer simplified/empty response must not erase a better saved route. When the same vehicle and ride are indexed in more than one month, each saved detail keeps its original source month. There is **no automatic TTL**, but quota/disk/backup settings apply. 180-day cloud observations are not a contractual retention guarantee. Protect saved details before the server potentially simplifies them.

Quota exhaustion pauses writes instead of silently evicting rides. Healthy reads and ordinary telemetry can continue when storage permits; structural startup failures remain strict. HA pre/post-backup hooks pause/drain archive writes and keep allowed reads available; they do not create/upload a backup themselves. See [upgrade and rollback](migration.md#archive-schema-and-rollback).

| Local operation | Contract |
|---|---|
| `verify` / `verify_continue` / `verify_status` | Bounded payload/reference audit; manual calls inspect at most eight objects. Background local audit uses the existing timer, checkpoint and low priority, not cloud budget. |
| Object receipts | Identity/hash/source/checker-bound evidence; unchanged receipts can avoid re-decoding, daily bounded deep checks still catch latent corruption. A receipt is not a future disk guarantee. |
| Quarantine | Retains bounded corrupt bytes without deleting sources; oversized objects stay in place, missing objects are recorded as missing, not copied. |
| Partial read | Validates latest and selected geometry independently; a surviving role is readable with explicit integrity/source warnings. Writes remain strict; a partial read does not clear Repair. |
| `reconstruct` / `reconstruct_continue` / `reconstruct_status` / `reconstruct_pause` / `reconstruct_cancel` | Durable local reconstruction from retained evidence with a private recovery point, identity/ownership/reference/byte budgets. No cloud download or guessed data. |
| Recovery-source search | Searches only managed copies, with persisted bounded continuation and exact digest/owner/ride/source checks. Captured filename bounds are not an immutable filesystem snapshot. |
| Missing-object startup recovery | Narrow validated schema-5 recovery from managed copies; preserves the original before atomic publication. Arbitrary schema/index corruption is not automatically repaired. |

Reconstruction keeps `.recovery-<job_id>.sqlite3` beside the archive, space-checks before copying and verifies its hash. Active reconstruction recovery points remain available for the job. Successful account consolidation removes verified inactive copies after their evidence has been merged. Missing/invalid recovery points enter `blocked_recovery` without endless retries; restore the matching original before explicitly continuing. A backup may itself contain an invalid derived index and is not a certified restore image.

Quality-only corrections keep newer scalar observations and selected geometry. Explicit identity contradictions, missing parents/links or unprovable memberships stay unavailable/blocked. Byte-corrupt roles can be restored only from exact matching managed originals after bounded quarantine; partial repair preserves readable roles and reports gaps. Old checker jobs retain their cursor and cannot retroactively certify a wider new contract. Verification of raw objects, references, normalized projections and cloud completeness are separate scopes.

Use the current `sync_status`, `verify_status` or reconstruction result to distinguish pending/invalid/repaired/with-gaps outcomes. Do not delete private storage or manually replace a live database to improve a percentage. Keep unresolved Repairs and evidence for supported recovery. Detailed boundaries live in [architecture](architecture.md#storage-and-integrity); original version-specific behavior remains in [release history](../CHANGELOG.md).

History belongs to the authenticated account, not the configuration entry. Removing/re-adding an account retains and reconnects its archive by default. Use the [archive export/import tools](guide.md#export-and-import-history-databases) for selected vehicles or multiple accounts; imports merge facts and better tracks without starting cloud queries. Distinct historical page evidence from merged sources remains inside the canonical schema 7 database; an absent discovery baseline can resume validated committed page coverage with in-flight request claims reset; an existing local baseline is never replaced.

Some backups index one ride under adjacent source months. Merge accepts the overlap when one validated archive proves both memberships for the same account, vehicle and ride, and the detail identifiers agree. Importing either backup first retains the newest measurements and best verified route. A conflicting identity or unproven membership stops the import and leaves the original archive available.
