# Installation and everyday use

[简体中文](guide_zh.md) · [Documentation](README.md) · [History and maps](history.md)

This guide covers **v2.2.16 preview**. For **v2.2.0 stable**, use the [documentation shipped with that release](https://github.com/Wuty-zju/ha_ninebot/blob/v2.2.0/docs/guide.md).

## Requirements

| Before installing | Check |
|---|---|
| Home Assistant | **2026.1.0 or newer** on a compatible 64-bit host/container. Use Marketplace on HA 2026.11+ or HACS on earlier supported versions. |
| Vehicle and account | Verify the vehicle appears in the official Ninebot App and you can sign in with a password. |
| Network | The HA host needs internet access to Ninebot's cloud. The verified service is for the mainland China App. |
| Release | Start with **v2.2.0 stable**. Select **v2.2.16** explicitly to try the [local archive panel](history.md#local-archive-replay) or [combined statistics follow-ups](assistant.md#compound-statistics). |

Before updating an existing installation, back up HA configuration and private storage. [Upgrade checklist](migration.md#before-upgrading)

### Update speed

Choose **Very fast**, **Faster**, **Balanced** (default), **Slower** or **Very slow** in Options. The values below are target intervals in seconds; vehicles adjust independently. Status contains SOC, range, locks, charging, ACC and location. Battery contains BMS data. Travel reads the current month summary/list.

| Profile | Parked status / battery / travel | Charging status / battery | ACC active status / battery / travel |
|---|---|---|---|
| Very fast | 15 / 30 / 300 | 5 / 10 | 2 / 10 / 30 |
| Faster | 30 / 30 / 600 | 10 / 15 | 5 / 15 / 60 |
| Balanced | 60 / 60 / 600 | 15 / 30 | 10 / 30 / 120 |
| Slower | 120 / 180 / 1200 | 30 / 60 | 30 / 60 / 300 |
| Very slow | 300 / 600 / 1800 | 60 / 120 | 60 / 180 / 600 |

Vehicle profiles refresh hourly. Higher speed increases cloud requests, subject to shared admission, queueing and server backoff. Status and battery are separate requests; querying SOC or location uses the shared status response. Changing only update speed keeps the session, local archive and entity identities in place. Feature, privacy and authentication changes retain their reload behavior.

## Install through Marketplace — HA 2026.11+

1. As an administrator, in **Settings → Marketplace**, open **⋮ → Add from link** and connect GitHub when prompted.
2. Enter `Wuty-zju/ha_ninebot` and choose **Integration**.
3. Open **Ninebot**, install the stable release and follow the account setup prompt. If HA has already loaded Ninebot, follow the restart reminder in **Repairs**.

[Home Assistant Marketplace instructions](https://www.home-assistant.io/integrations/marketplace/) · [Default catalog request](https://github.com/hacs/default/pull/6217)

## Install with HACS

1. In **HACS → ⋮ → Custom repositories**, add `https://github.com/Wuty-zju/ha_ninebot` as an **Integration**. If HACS applies a country filter, select China or **All**.
2. Open **Ninebot**, download the stable release and follow the restart prompt.
3. Open **Settings → Devices & services → Add integration → Ninebot**.

An update to HA 2026.11 transfers installed projects, custom repositories and update entities to Marketplace. [HACS migration](https://www.home-assistant.io/integrations/marketplace/#coming-from-hacs)

## Install manually

Download `ninebot.zip` from [the stable release](https://github.com/Wuty-zju/ha_ninebot/releases/tag/v2.2.0). Extract it so the manifest is at `<config>/custom_components/ninebot/manifest.json`. Restart Home Assistant and add Ninebot from **Devices & services**. The ZIP already includes dashboard assets and translations.

## Update an installed integration

1. Back up HA. Choose the intended release in **Settings → Updates**, in HACS, or from GitHub for a manual install. Marketplace and HACS require opting into pre-releases.
2. For a manual update, replace the Ninebot integration folder with the complete package. Follow the HA restart prompt.
3. Confirm the running version and vehicle on **Devices & services → Ninebot**. Reload dashboard clients after updating the bundled card. Continue using the existing account entry and archive.

[Upgrade and rollback](migration.md) · [Marketplace updates](https://www.home-assistant.io/integrations/marketplace/#keeping-installations-up-to-date)

## Add accounts and vehicles

Use the account and password from the official App. The setup form currently offers password sign-in. Newer HA versions open account options after verification. Older supported versions include feature selection in the same wizard. The password is used during authentication. Protect the resulting local sessions and any HA backups that contain them.

Each entry represents **one account** and owns a separate archive. Add another Ninebot entry for a second account. A physical vehicle shared between accounts can appear as a separate device under each account. Confirm the account when choosing devices for [history](history.md#add-the-history-map), [controls](#controls-and-confirmation) and [Assistant](assistant.md#enable-it).

For expired credentials, use **Reauthenticate** on the existing entry. Use **Reconfigure** to update that account. The entry title uses the reported `username：account` when available and preserves any title you have set. [Login troubleshooting](#login-fails-before-the-account-is-added)

## Entities and data

Available fields depend on the vehicle model, permissions and cloud reports. Standard entities are enabled and visible when their feature is active. Additional diagnostic entities follow their display option; events and calendars have separate switches. Existing user-disabled or hidden choices are retained.

| Group | Available information | Meaning and limits |
|---|---|---|
| Vehicle | Main battery SOC, precise/estimated/AI range, charging, power, ACC, battery presence, service status | Three range fields stay separate; AI range is diagnostic. ACC on does not prove movement. |
| Battery | Voltage, temperature, cycles/support, charging power, health score, pack identity/count/type, Find My support | Main battery SOC describes the propulsion battery. Health score is not SOH; 0 can mean not supported. |
| Communication | Emergency communication battery SOC | A separate percentage sensor; it does not replace the vehicle's main battery indicator. |
| Location and image | Device location tracker, vehicle image | Two valid fresh coordinates are required; no unverified coordinate conversion. The image is diagnostic. |
| Rides | Today/yesterday/month totals, last-ride metrics, monthly list coverage | Completeness is checked per metric. Missing evidence is not silently converted to zero. |
| History | Sync state, month-scan progress, saved-detail progress, last successful sync | These are different scopes; 100% saved discovered details does not prove full cloud history. |
| Controls | Vehicle/seat locks and retained remote buttons | Enabled controls, an allowlist and authorization required; see below. |
| Native HA | Ride calendar, vehicle/trip events, query Actions, statistics import | Historical lists and tracks are not expanded into hundreds of entities. |

Durations retain native seconds and suggest **hours with four decimals** through HA's native conversion. Distance/range and Wh/km suggest one decimal; energy and maximum speed suggest integers. Presentation rounding does not round archived source facts. HA user overrides take precedence. Rated battery V/Ah produce a specification in kWh, not measured capacity or health.

Cycle counts display not supported only when the cycle-support flag explicitly says so; unknown support stays unknown. Missing odometer is not reported, without invented distance. English and Simplified Chinese are the primary documentation languages; shipped entity/settings labels cover additional locales with English fallback for some longer text.

Daily statistics can repair a capped current-month ride index even with historical detail synchronization off. Only enabled daily consumers request this list-only repair: at most one page per 60-second repair opportunity, sharing the hourly history budget and pause/backoff. At month rollover it may also repair the adjacent month. Saved complete indexes are reused; this does not fetch details or tracks or enable full-history scanning.

## Dashboard organization

| Purpose | HA view | Data source |
|---|---|---|
| Find a vehicle now | A Map card with its device tracker | Latest allowed vehicle coordinates |
| Compare saved rides | Ninebot ride history card with one or more device IDs | Private archive, route selection and sample replay |
| Follow longer trends | Statistics graph or calendar | Imported daily/monthly aggregates or dated trips |

Turn on **Show vehicle location** for maps. The archive card can target a device directly while its calendar is disabled. [Card setup](history.md#add-the-history-map)

Create a **Sections** dashboard view and group entities by purpose. Numeric entities retain their sensor type and statistics. On HA 2026.11+, **Automatic section → Device / Entities** can generate a section for a vehicle or a selected set of entities; earlier versions can use manual sections and native cards.

| Section | Suggested content | Native presentation |
|---|---|---|
| Vehicle now | Main SOC, range, charging, vehicle/seat locks | Tile / Entities; open more-info when needed. |
| Month / Today / Yesterday / Last Ride | Distance, count, duration, energy, average consumption and recent speeds | One Entities card or subsection per period; retain units and history. |
| Local history | Ride calendar and imported day/month statistics | Calendar / Statistics graph; query full lists and tracks on demand. |
| Location | Current device location | Map; recorded position history follows Recorder retention. |

Copy IDs from your own entity picker. HA computes display names from registry context. These are synthetic placeholder IDs; existing entities do not need renaming:

```yaml
type: sections
sections:
  - type: grid
    cards:
      - type: heading
        heading: Vehicle
      - type: tile
        entity: sensor.my_vehicle_battery
  - type: grid
    cards:
      - type: entities
        title: Today
        entities:
          - sensor.my_vehicle_today_mileage
          - sensor.my_vehicle_today_ride_count
          - sensor.my_vehicle_today_duration
```

HA manages the standard regions on a device page. Dashboard sections provide independent grouping while preserving unique IDs, Recorder history and event semantics. The native Map does not read arbitrary archived polylines directly; see the [local history guide](history.md) for historical queries and the bundled replay card. These cards use existing states and local history without extra vehicle queries.

## Options

Open **Settings → Devices & services → Ninebot → Configure**.

| Option | Default / range | Effect |
|---|---|---|
| Update speed | Balanced; five profiles | Per-vehicle/group targets adapt to charging and ACC. Polling-only changes reuse the runtime. |
| Show vehicle location | On | Allows location display/track responses; preserves an existing explicit off choice. It does not add a separate high-precision cloud request. |
| Vehicle events (Beta) | On | Trip reports/completion and concrete vehicle transitions; turning off preserves entity identity and re-enabling establishes a silent baseline. |
| Ride calendar (Beta) | Off for new accounts | Enables the local per-vehicle calendar; turning it off does not delete rides or disable the tracker. |
| Automatically sync trip details (Beta) | Off for new accounts; existing choices retained | Stores private historical details/tracks and enables budgeted acquisition. Review location/backup implications. |
| Automatically scan trip history (Beta) | Off for new accounts; existing choices retained | Starts/resumes first discovery and later incremental work when preservation is enabled. |
| History storage limit (Beta) | 100 MiB/account; 32–2048 MiB | Quota exhaustion pauses writes and retains readable records; lowering the limit does not delete history. |
| History request budget (Beta) | 60/account/hour; 0–120 | Shared logical month/detail calls; not a request for upstream permission. Transport retries are outside this counter. Enter 0 to remove the hourly quota (not recommended); serialization and upstream backoff remain active. |
| Controls + allowed vehicles | On for vehicles verified at sign-in for a new account | The first settings on the page: adjust controls and the account vehicle selection in the native multi-select dropdown. Existing choices are preserved; vehicles added later are not silently allowed. Disabling controls retains the selection. History synchronization permissions are separate. |
| Keep local history database (Beta) | On | Retain account history on removal and reconnect it on same-account sign-in. |
| Nominal battery parameters | Off | Voltage and capacity start empty, can be filled independently or cleared, and produce rated energy only when both are present. A failed save keeps the edit pending and reports a storage Repair; save again after resolving it. Retired low-confidence SOC estimators remain removed. |
| Configure Ninebot Assistant (per-vehicle setup in 2.2.9+) | Off, no vehicle access | Enable Assistant for the account, then choose allowed reads and history task access for each vehicle. [Setup and privacy](assistant.md) |
| Show diagnostic entities | Off | Bounded parsed current information for investigation; no extra polling or public raw-data dump. |

On HA 2026.11+, allowed vehicles appear when controls are enabled; automatic history scan, capacity and request-budget fields appear when automatic detail synchronization is enabled. Hidden settings retain their previously saved values. Earlier HA versions show the same fields directly. Archive management and retention remain available independently.

With the default policy, charging targets status/battery at 15/30 seconds; ACC activity targets status/battery/travel at 10/30/120 seconds. New charge transitions, external unlocks and stops open finite observation windows. ACC indicates activity; power alone does not prove movement.

After activity stops, up to six current-month summary checks help settle recent trips. They reuse in-flight reads and never download old trip details. Existing lock confirmation and follow-up stay independent; a control command is never automatically repeated.

Periodic reads use local admission budgets: one per second per account with a burst of three, and two per second across accounts with a burst of six. Requests are fairly deferred under pressure. These are local scheduling protections; bootstrap, explicit queries, controls and the separately budgeted history worker retain their own bounded paths. Upstream retry instructions take precedence. Diagnostics distinguish target intervals, observed intervals, queue delays and deferral reasons.

Requests are demand-aware, account-budgeted and shared when identical reads are in flight. Status, battery and travel have separate freshness and failure/backoff state. Unload/reload cancels work; a cache read does not become a new measurement.

## Controls and confirmation

Vehicle **unlock** requests remote start; **lock** requests remote stop. The vehicle/cloud decides its motion/P requirements. HA does not disable remote stop by guessing whether the vehicle is moving. Seat unlock releases the latch so the cover can be opened by hand; seat locking is manual only. The retained buttons use the same executor as the Lock entities.

Each explicit request sends one command, never an automatic replay. Up to sixteen fresh status reads over fifteen seconds check the target and publish a confirmed transition immediately; a bounded follow-up at 10/20/25 seconds can observe automatic relocking. The whole follow-up ends within 30 seconds or earlier on errors/unload/new commands. Confirmation uses fresh cloud observations; pending/unconfirmed/failed results are kept distinct. A new command can replace the previous confirmation observer; only command transmission is serialized.

If a seat-unlock command is accepted but no unlock is observed, the message is “Unlock command sent. Check the seat compartment.”

After a verified seat unlock, its Lock display remains unlocked for at least five seconds while status queries continue. The `Reported latch state` attribute and vehicle state events retain the actual reported state throughout this display window. After five seconds, the Lock follows the latest valid latch reading. A separate status observation runs about once per second for fifteen seconds, then at 20/30/45/59.9 seconds, ending within sixty seconds. Missed read slots are skipped rather than sent in a burst.

The reported latch can reset while the compartment lid remains open. Existing data does not provide an independently verified lid-open/closed field, so the integration does not infer lid position or create a lid-state entity. The five-second display window does not prove the lid is open. New commands, unloading, authentication loss, rate limits and read errors stop the follow-up.

History sync and pause appear in device diagnostics. Sync also resumes a paused task; icons and the synchronization-state attribute distinguish running, paused and budget-wait states. History status, last successful sync and parsed raw views appear only with diagnostic entities enabled; the calendar has its own switch. Diagnostic classification does not exclude Recorder; configure Recorder filters if needed.

## Troubleshooting

| Symptom | Check first |
|---|---|
| Integration missing after download | Confirm the directory/domain, restart as prompted, and use a release compatible with your host architecture. |
| First upgrade stays at initializing | Legacy archives are validated and consolidated before setup completes. Several databases can take a few minutes. Check the latest log time and allow the migration to finish. Repeated failures require the traceback and diagnostics. |
| Authentication fails | Check the official App/account region; use reauth. Do not publish credentials or token files. |
| Check the vehicle type | Open the device's **Diagnostics → Vehicle type**. The name is localized for electric bicycles and electric motorcycles. The `business_line` attribute shows the reported value. |
| Rated-parameter form rejects a vehicle | Close and reopen the form after the updated integration is actually loaded. Choose a currently discovered vehicle. |
| Today/yesterday values unknown | Setup and midnight schedule a prompt refresh; incomplete lists or missing metrics still need evidence. Same-period verified values survive short outages. See [coverage and statistics](history.md#coverage-and-statistics). |
| Ride/vehicle event unknown | Events wait for a new/revised trip report, stable completion or known status transition after their baseline. History imports and reconnection do not replay events. See [event semantics](history.md#ride-summaries-and-vehicle-events). |
| History waiting for request budget | Inspect `next_request_at`, `retry_at`, `sync_phase`, pause/auth/capacity state; do not repeatedly request a full scan. |
| Calendar shows “No current trip” / off | This is a historical calendar with no active event. Browse saved dates in HA Calendar; only the account option disables the feature. |
| Custom element not found | Register the [bundled module resource](history.md#add-the-history-map), then reload the browser. |
| Track missing or a single point | Check saved-detail state, coordinate option/permissions and cloud track quality. Old simplified data cannot recreate a lost trail. |
| Storage Repair | Preserve the files and read [verification and recovery](history.md#verification-and-recovery). Do not delete the database/registry to silence it. |
| “Custom integration has not been tested by Home Assistant” | Standard third-party loader notice; it is not suppressed. |

### Login fails before the account is added

Version 2.0.1 added the per-attempt `Account setup failed` record and `support` identifier described below. Version 2.0.0 does not include these fields.

Enable logging for this integration in **Developer tools → Actions**:

```yaml
action: logger.set_level
data:
  custom_components.ninebot: debug
```

Submit the sign-in form once, note the time and form error, then download the **Home Assistant Core** log from Settings → System → Logs. Look for `Account setup failed` and its `support` identifier. The entry reports the observed local stage, error kind and proxy response status. A proxy status does not identify a cloud region or an internal authentication stage. `loopback_auth` with `loopback_auth_probe` identifies a local service handshake failure; `vehicles` identifies vehicle discovery. Expected handshake responses (401 without authorization and 404 for the authorized probe) are silent. On Home Assistant 2026.11+, the integration’s live log view is also useful when reproducing a failure. Restore the previous log level afterward, usually `warning`.

For an existing account, use the integration menu's **Enable debug logging**, reproduce the problem, then disable it and download the log. Download **Diagnostics** separately. **Show diagnostic entities** exposes the parsed current-data view and progress entities; DEBUG logging uses the integration menu. A failed or unloaded account can still export basic version/platform diagnostics.

When reporting sign-in failures, include the support identifier, form error, software versions, your App's account/service region if known, and whether you entered a phone number, email or username. Omit the actual account, password, tokens and vehicle/location data. Keep history exports private. Enabling logging sends no authentication request.

### Missing data and controls

Compare the same account, vehicle and business month in the App and HA. A working SOC or voltage reading establishes that those values were received; history and controls use separate requests and permissions.

| Display | Meaning and next check |
|---|---|
| Zero trips with 100% list/detail progress | Progress describes its known denominator. A confirmed empty list can be complete without containing a trip. Inspect the reported/returned counts, discovery state and scanned range. |
| Average energy unknown with zero distance | No distance-based average can be calculated. Zero total energy and an unknown average are distinct. |
| Last trip or trip event unknown | No usable recent record, or no new event after the startup baseline. Event entities do not replay all saved trips. |
| BMS value “Not supported” | This metric has no supported upstream value under the reviewed mapping. It does not establish a faulty battery; compare the same App field before reporting it. |
| Remaining charge time unknown while not charging | The endpoint may omit a charging estimate outside charging. Check its field availability and charging state. |
| Controls ready / command accepted | Ready is local eligibility; accepted is a command receipt. Only a later fresh observation confirms the requested state. |

For an existing account, enable native debug logging, note the exact time, operation and full message, then download Diagnostics and disable/download the log. Data collection alone does not require a vehicle control. If an entity is unavailable before an action reaches the integration, HA may reject it without a control record.

From 2.1.23, diagnostics include up to 32 vehicles and the current/previous cached month per vehicle, with counts, list field state and independently identified sources. History progress includes its known counts and cached scan range. Missing cache is marked unobserved; downloading diagnostics does not refresh the cloud or scan the database. Truncation is reported explicitly.

`groups.status/battery/travel.failure_context` retains the current safe query failure, distinguishing request, normalization and archive stages. Successful recovery clears it. `control_policy` explains current local gates; `control_results` retains the latest attempt for each action. Native DEBUG lines named `Control result` contain a short `support` identifier, acceptance/rejection, readback and confirmation. A rejected attempt was not sent; an uncertain invocation must not be treated as a safe automatic retry. Download diagnostics promptly because these observations are kept in memory and reset on reload.

From 2.1.25, `confirmation_deadline_reached` identifies the end of the local 15-second confirmation window. Completed reads retain their result and any actual transport error; expiration alone does not establish a connection failure. If no read completed, confirmation remains unknown. A proxy `502 / upstream_error` belongs to the command response and is recorded separately; it does not disclose the cloud's business rejection reason.

From 2.1.26, support records also retain a bounded numeric `upstream_code` when the backend provides it, plus recognized `backend_error` categories such as response decryption. These observations do not automatically identify an authentication or permission problem. Error text is omitted. Vehicle diagnostics include `control_routing` after an explicit command: the native cache's business line, observation time and selected transport, without identity values. Verified motor controls use the native path described in the [architecture](architecture.md#protocol-adapter), with the same enablement, single-attempt policy and confirmation window.

Vehicle discovery supplies the **Vehicle business line** attribute on the **Parsed information** diagnostic entity. Enable **Show diagnostic entities** to view it from the device page. The diagnostic download includes each vehicle's `business_line` and account-level counts for present vehicles. `unobserved`, `ambiguous` and `unsupported` distinguish missing, conflicting and unrecognized classifications. Reading either view uses the existing discovery cache.

If the month endpoint reports zero trips with a null list, increasing the detail budget cannot discover IDs absent from that response. Provide the date of an App-visible trip in the same month, omitting location. For a failed control, identify whether the App's corresponding operation uses nearby Bluetooth or remote connectivity; successful Bluetooth control does not establish cloud-control support. Reinstalling or deleting the archive is unnecessary for collecting these observations.

Include both software versions, model family, account-owner/shared status, the affected period and the specific missing field or failed action. Share only reviewed diagnostics or small relevant log excerpts; omit raw responses, account/vehicle identities, coordinates and history exports. The **Show diagnostic entities** option does not enable these logs.

## Privacy, backup and removal

Location display defaults on, and detail preservation can retain precise tracks. The archive and HA backups are private location records even if an individual response omits coordinates. Automation traces may retain requested Action responses. Diagnostics export reviewed schema/status metadata, not credentials, identities or GPS trails; review any file before sharing.

On compatible HA frontends, basemap requests follow the HA Map Tiles service. Protect backups and local session directories from other users and privileged processes. [Map privacy](history.md#map-privacy-and-units)

**Keep local trip history database (Beta)** is checked by default. Removing an account stops its tasks and retains trips, detailed tracks and recovery evidence. Adding the same authenticated account reconnects its archive; synchronization resumes under your selected options. If unchecked, the next account removal deletes that account’s managed archive and recovery copies. Unrecognized files are retained with a Repair. This setting does not delete Recorder history. Do not edit a running database or registry manually; see [migration](migration.md).


## Export and import history databases

In the account’s **Configure** form, select **Export or import history database (Beta)** and submit. The next menu provides **Export database** and **Upload and import database**. These operations require a Home Assistant administrator.

- **Export:** select one or more vehicles across loaded accounts. Click the administrator-bound download link, valid for ten minutes. Filenames identify UTC creation time, account/vehicle counts, scope fingerprint and export ID; the manifest includes the schema, counts and time range. It contains consistent SQLite snapshots, historical source evidence and a versioned SHA256 manifest; no password or sign-in session is included.
- **Import:** first sign in to every account represented in the package, upload the ZIP (or a supported earlier SQLite database), select vehicles, then confirm the backup and merge. Account ownership, schema, references, raw payload hashes and ride identities are checked before merging. Better saved geometry survives a newer simplified response; repeated imports are deduplicated.
- Each account merges all selected source databases in one transaction. Temporary recovery snapshots are removed after a validated commit; failed merges retain a recovery point. Accounts completed before a later account fails remain committed; retrying the package is safe. Existing options, live authorization and active sync jobs are not replaced.

Packages are private GPS records, not diagnostics. Transfer them securely; they are not encrypted by the integration. Uploads are limited to 100 MiB compressed and 512 MiB expanded, at most 16 accounts. Earlier packages may include up to 16 source snapshots per account; these are consolidated rather than kept as separate databases. New exports contain one SQLite per account. Distinct historical originals live inside the main database and count toward its capacity. Verified old migration/import copies are automatically removed; failed or unrecognized files are retained for review. Back up the full HA configuration before upgrading; keep external exports for migration or rollback.

## Connection interruptions

Temporary cloud or vehicle-discovery failures retain verified same-period trip statistics and saved history. Live readings keep their actual response age and become unavailable when stale. Recovery retries automatically; you do not need to remove/re-add the account. Controls for a vehicle missing from discovery remain blocked until it is verified again. Download integration diagnostics to inspect discovery retries, transport cooldown and actual status intervals. Configured polling intervals are targets; cloud outages and rate limits can delay new readings.
