# Ninebot for Home Assistant

[中文说明](README_zh.md) · [Releases](https://github.com/Wuty-zju/ha_ninebot/releases) · [Development entry](docs/README.md)

An independent, unofficial Ninebot vehicle integration using pinned `ninecli==0.1.7`.
Version 2 replaces the OpenClaw backend. The current code is a prerelease;
vendor API availability can change. It targets the Chinese App service; other regions are unverified.

## Installation and account

Add `Wuty-zju/ha_ninebot` as a custom integration repository in HACS and select a beta release,
or copy `custom_components/ninebot` into your HA configuration. Restart your HA after installation,
then add Ninebot in Devices & services. Requires **HA 2026.1.0+** and a compatible 64-bit ninecli wheel;
ARMv7/32-bit is unsupported. Published Linux/macOS/Windows wheels do not certify every platform.

Password and two-step SMS login are supported. Candidate sessions are isolated and validated before
commit; passwords are not saved in ConfigEntry or passed as command-line arguments. SMS is covered
by offline tests, with live verification pending. Reauth/reconfigure retain the same account.
Use Add account where supported by the HA frontend. Different accounts have separate entries;
titles use username：account[region] when explicitly reported, otherwise the account alone.
Custom entry titles are retained. Login reads username/region; older entries without a username can
make one display-enrichment read on successful setup. This is not a polling/retry loop.
Per-entry token/config directories are private local storage; same-user/privileged processes remain
inside the trust boundary. A managed random-Bearer loopback serve child handles requests.
Vehicle discovery uses a controlled native command to initialize the business routing cache.
See [backend/auth contract](docs/README.md).

## Current data and options

- Vehicle SOC, separate precise, estimated and AI-estimated range sensors (AI is diagnostic), charging, power and native vehicle/seat locks.
- BMS voltage, temperature, supported cycles, charging power in W and a health score that is **not SOH**.
- Today/yesterday and current-month statistics; last-ride distance/time/energy/max/overall average speed.
  Daily counts, duration and Wh use verified complete or locally reconciled archive records;
  missing records and inconsistent totals remain unknown. Ride durations default to hours with two decimals.
- Emergency communication battery SOC (%), main battery type (lithium/lead acid), Apple Find My and cycle support.
- Battery presence, ACC on/off and service active/expired; unfamiliar codes are unrecognized.
- Vehicle image, optional GPS, small ride-completed events and response actions for historical queries.
- Optional nominal V/Ah produce one stable rated-energy specification. **SOC cumulative estimation was removed in b24.**

Created entities are enabled/visible by default; user-disabled/hidden choices remain intact.
Location display defaults on; existing explicit off choices are preserved. Controls, debugging
and nominal battery parameters require their feature options.
Entity names use translations; English/Simplified Chinese are complete, with 21 languages covering
main names/settings/actions and English fallback for some other long help/error messages.

Default status/battery-travel/profile intervals are 120/600/3600 seconds. Per-vehicle/group freshness,
backoff, partial failures and typed consumer contexts govern requests. A bounded broker shares identical
in-flight reads, prioritizes status and gives other vehicles/background reads a turn. Each account has
one wire transaction; a shared limit permits two across accounts. Read/control queues have separate
budgets. Cancellation of one reader does not cancel other readers; unload reclaims pending work.
Transient GET failures retry at most once within the same deadline; controls never replay. Transport
rate-limit metadata and existing per-group backoff apply. Reading cached data does not refresh its age.
Disabled BMS/travel consumers stop regular queries except bounded initial/sparse discovery and ride-event demand.
Incomplete vehicle discovery does not prove unbinding or refresh missing vehicles' identities.
See [vehicles/entities/polling](docs/README.md).

GPS requires two valid coordinates and the location display option; its coordinate system is unverified and never auto-converted.
Image URLs use reviewed anonymous origins and HA caching. Diagnostics contain safe schema/metadata,
not raw values, credentials, identity, precise locations or trails. Debug views are bounded and do not request extra data.
See [raw/diagnostics contract](docs/README.md).

## History, events and controls

`ninebot.get_trips`, `get_trip_detail` and `get_history` target the HA vehicle device and return response data.
Historical lists/tracks are not entities or large state attributes. Local pagination only covers received rows;
reported monthly totals can exceed the returned list. History cursors are bounded memory; 2.0.0b39 persists normalized facts in a private per-account archive.
`sync_history` fills at most three missing months per call and retains its checkpoint across restarts.
`get_recorded_trips` browses dated local archive pages without cloud queries, even during
authentication failure. It returns coverage, observation receipts, provenance and source precision;
content changes invalidate its revision-bound cursor. See the [local browsing example](docs/README.md#local-archive-browsing).
Tracks require coordinates opt-in plus `include_track`; automation traces may retain response locations.
Server maximum speed and distance/duration average remain distinct; point-speed/delta units are unverified.
Ride events establish a startup baseline and suppress duplicates; cloud timing/pagination can cause missed events.
See [travel/actions/event contract and example](docs/README.md).

Controls require explicit enablement, an allowed vehicle, authentication and fresh ownership/status.
Known denials or ambiguity block dispatch; unknown permission stays unknown and the cloud decides authorization.
Native vehicle/seat Lock entities share the same guarded executor as the retained buttons.
Start unlocks the vehicle and stop requests locking. Motion/P policy is enforced by the vehicle/cloud;
the integration does not disable stop based on missing telemetry. Buck releases the seat lock;
seat locking is manual only. Explicit lock requests send one command even if cached status already
shows the target. Up to three single-attempt status reads at 0/2/5 seconds confirm the target,
then up to three short-lived reads at 10/20/25 seconds observe automatic relocking.
Follow-up ends within 30 seconds, stops on errors/unload, and is replaced by a newer same-vehicle command.
Target confirmation means observed cloud state, not physical verification; uncertainty is preserved.
Same-vehicle actions are mutually exclusive across accounts. Pending state is never optimistic confirmation.
See [control contract](docs/README.md).

## Upgrade and development

Back up matching HA configuration/storage before upgrading. Meaningful IDs, user names and Recorder history
are preserved; reviewed obsolete estimates/duplicates are removed only for confirmed exclusive ownership.
b36 introduces canonical generated entity IDs; see the [migration guide](docs/README.md#canonical-entity-ids-and-upgrade). Custom/unverifiable IDs stay unchanged; automations using renamed or removed IDs need adjustment. Downgrading requires matching pre-upgrade storage,
including identity/parameter stores and registries; code-only rollback does not reverse migrations.
b38 converts reviewed old binary/sensor lock rows to Lock entities with the same unique ID strings and
user choices. Old Recorder data remains under the old entity IDs; new Lock history starts separately.
Use get_entity_migration and update on/off automation conditions to locked/unlocked.
[Historical migration matrix](https://github.com/Wuty-zju/ha_ninebot/blob/9ba20bcadea1ea0500d9c080e60a48adc35e2387/docs/2.0-实体迁移矩阵.md) records the initial v2 transition.

Developers start with [product documentation](docs/README.md) and [product constraints](AGENTS.md), then the relevant code/tests/fixture provenance.
[CHANGELOG](CHANGELOG.md) is release history.
Maintain local developer handbooks outside the product repository instead of adding a report for every beta.
Small changes use targeted offline regression; major boundaries use the pinned compatibility suite.
Exact CI execution/skip scope and physical verification are separate facts; see [development constraints](AGENTS.md).

Native historical day/month trends are available through the explicit `ninebot.import_statistics` Action, with ready-to-copy standard statistics graph cards. See the [history and visualization guide](docs/README.md#native-historical-trend-graphs); imports read the existing ledger and do not query the cloud.

An optional packaged [ride detail card](docs/README.md#optional-ride-detail-card) adds local date pages and explicitly requested details/uncalibrated sample curves, alongside native calendars and statistics graphs.

See the [b44 naming/display migration](docs/README.md#b44-display-and-naming-update) for forced existing-ID renames, presentation and account-header limitations.

[b45 calendar and ride presentation](docs/README.md#b45-calendar-battery-and-ride-presentation)


Local archive recovery uses `ninebot.sync_history` with a vehicle `device_id` and
`operation: reconstruct`. It creates a durable local scalar-reconstruction job;
`reconstruct_continue` processes one ride or resumes a paused job,
`reconstruct_status` reads progress, and `reconstruct_pause` / `reconstruct_cancel`
pause or stop it. When history preservation is enabled, requested jobs continue
on the existing background tick. These operations send no cloud requests or
vehicle controls and require history-control permission except status reads.

Each job retains one private `.recovery-<job_id>.sqlite3` recovery point beside
its account archive. Space is checked before copying; existing recovery points
are never overwritten or automatically deleted. A recovery point may preserve
an invalid derived time index and is **not** a certified ready-to-load restore
image. The job checks its hash on first use, after restart or changed file
metadata, and daily during a long run. Repair uses trusted current originals;
missing fields, stale receipts, unreadable objects and changing scan scopes are
reported rather than guessed. Results describe captured local details, not all
cloud rides or every normalized field/reference. Corrupt source objects and
unresolved Repairs remain for subsequent recovery work.

If a recovery point is missing or fails verification, the job enters
`blocked_recovery` and stops automatic retries. Healthy archive reads/writes
remain available. Restore the matching private recovery point before explicitly
continuing; the integration never replaces it silently or clears its Repair just
because another record is readable.


Reconstruction checker v2 also compares the derived track-quality index with
readable latest and selected-geometry originals. A quality-only correction
preserves newer scalar observations and the selected raw geometry. Explicit
ride/detail identity contradictions make that source role unavailable; a healthy
other role can still be read through the existing partial-data view.
ID-less legacy originals retain their recorded query-scope provenance; this is
not independent proof of every reference or all cloud history.
Old checker-v1 jobs keep their cursor. If some records were processed before
v2, continuing the old job reports `with_gaps`; start a new reconstruction job
to cover the enlarged local check scope. No missing raw originals are guessed or
silently downloaded, and unresolved Repairs remain.


The local reconstruction job can also recover byte-corrupt referenced originals
from managed recovery copies in the same private account folder, using
persisted cursors and at most eight source-copy checks per ride batch. It requires the exact existing content digest, matching owner/vehicle/
ride and unchanged source metadata, then validates decoding, explicit identities
and redaction. Damaged bounded bytes are copied and verified in quarantine before
replacement. Receipt times and reference hashes are preserved; current integrity
receipts are refreshed. No arbitrary path or cloud request is accepted.
A recovered readable role is kept even if another role remains bad; progress
reports `partially_repaired` and finishes with gaps. Missing rows, oversized pinned
objects, reference remapping, startup corruption and recovery sources beyond the
bounded search require further recovery work. They are never guessed or deleted.


Local recovery-source search now continues beyond the first eight copies. Each
role records a captured name upper bound and continuation cursor in the same
account archive; restart does not repeat prior source checks. The interval is
not an immutable filesystem snapshot: sources added after the captured range can
be considered by a new explicitly started job. While searching, the ride remains
pending; `searching_sources`, `source_copy_checks` and `pending_role_recovery`
explain progress without exposing file paths or raw data. Previously recovered
roles stay readable, and final repaired/partial/blocked counts advance once per
ride after source search finishes.


### Missing local detail originals

When schema-5 startup fails only because referenced detail originals are missing, the integration can recover up to eight originals from up to eight managed local recovery copies. Existing verified receipts must match the account, vehicle, ride, digest and source metadata; the entire staged database must pass strict validation. The original file is preserved before atomic publication. This does not repair arbitrary schema/index damage or fetch cloud data; absent proof or unsupported cases retain the original and report a storage error.


### Recent ride follow-up

Recently saved details may still be incomplete. Background preservation can compare another response after ten minutes; changing scalars or detail content resets the stable-observation count. Unchanged recent details settle after two matching observations; older unchanged history remains cached. This is a bounded local follow-up policy, not a claim that the cloud marks a ride final. Account request budgets, pause and restart continuity still apply.


Manual history requests are scoped to the selected vehicle even when automatic synchronization is off. Default sync joins an existing current-month page run; an earlier month waiting for retry does not block another due month. Explicit old-month selections remain separately scheduled priority work. All requests continue to share the account budget.


### History progress and local rate limiting

The history request budget is shared by the account, not an application for upstream permission. Progress exposes `next_request_at`, `retry_at`, and `sync_phase`; a request in flight publishes its active phase. “Last successful sync” updates after an accepted page/detail commit, while `last_completed` records full-task completion. Recorded stable-total multi-page month counts describe saved evidence; they do not certify the upstream history boundary or every model. The legacy `real_cloud_pagination_verified` attribute is unknown rather than hardcoded false.


### Reusing verified history months

First discovery can reuse an independently enumerated old month already stored in this account archive. All original pages must verify within eight pages and 4 MiB of decoded JSON, with matching totals, ride membership and source timestamps no earlier than the baseline cutoff. Current and previous months, newer observations or incomplete evidence use normal discovery. This local reuse preserves original receipt times, consumes no cloud request budget and does not mark missing ride details complete. It does not infer an earliest historical month from empty results.


If an accepted account list no longer contains a known vehicle, history sync status shows **Vehicle missing from account list**. This describes the latest accepted discovery result, not proof of physical unbinding or account deletion. Vehicle-specific queries/actions stay blocked and saved data is retained. Normal account discovery continues; a positive rediscovery resumes the existing baseline. The status exposes only list freshness metadata while the vehicle is absent, not cached ride counts or GPS. Check the account/vehicle list if the condition persists; this status does not prove why the upstream list changed.


Within the existing 150–180 day local preservation risk band, pending details are now selected by oldest confirmed ride end first. Other pending history stays newest-first, with deterministic ride-ID ties. Missing end times are not assigned guessed deadlines. This is a local scheduling policy, not a guaranteed server deletion date or a local retention limit; shared budgets, vehicle rotation, saved-detail reuse and persisted retries still apply.


Cached statistical months and the latest ride now come from one per-vehicle archive read. Local history pages capture their detail-saved flags together with their rows and revision. Background writes cannot split those reads into mixed versions. Separate calendar/list/detail requests still observe their own read time; this is not a database snapshot token across requests or the whole fleet. Browsing remains local and bounded.


Local reconstruction can restore a missing ride/month link only when retained original pages prove the same ride/detail identity and exact start time. It checks at most eight missing months/eight page candidates and 4 MiB decoded evidence, then uses the existing private recovery backup, ownership fence and atomic commit. Existing links and complete-list withdrawals are not rewritten. Missing parents, unproven identity/time or over-budget evidence stay blocked; no cloud fetch is used. This is a narrow repair, not certification of all memberships or normalized fields. The repair starts when the normalized ride’s current query-month link is missing; older-only link losses are not a full-audit claim.


History completion now also requires consistent ride/month references for enumerated IDs. `reference_issues` counts affected rides, including missing parents/links and latest-list selection mismatches. Saved-detail percentages still describe saved known details; 100% does not override a reference gap. A previously completed task shows verification pending when current metadata is inconsistent, retaining its historical completion timestamp. Structural startup/backup checks stay separate, allowing local recovery; no selection is silently rewritten. This is consistency against declared local month IDs, not independent raw-parent or cloud-completeness certification.

Local history coverage distinguishes a partial source page from a month with a matching recorded pagination chain. The card shows **Enumerated months**; `get_recorded_trips` also returns `coverage.enumerated_months` and per-month `recorded_enumeration` page/count/receipt metadata. A newer summary, inconsistent or over-budget evidence leaves enumeration unverified. This reads locally, preserves the original single-page coverage flags, and does not certify raw payload integrity or the cloud’s earliest history boundary.

Background synchronization rotates vehicles independently for month requests and detail downloads. This prevents alternating families and vehicles from locking one vehicle into month discovery while another receives all detail slots. Both rotations survive restart and share the same configured account request budget.

Local verification responses now include `page_reference_checks`. This separate captured-page check compares raw page IDs/counts with stored page headers, identity indexes and membership presence while reusing the raw audit’s one decode. Conflicts do not rewrite indexes or invalidate otherwise intact raw bytes. Monthly parent metadata, orphan indexes outside captured pages, normalized metrics and cloud completeness remain outside this certificate. A pre-upgrade active raw scan must finish before starting a fresh combined audit.

Explicitly contradictory history response months are retained as unverified schema-gap evidence without importing their rides; scanning can continue after restart. They do not certify the month or repair an upstream response.

## Current-month source checks

When **Preserve history** is enabled, ordinary month queries also save a private current source snapshot in the existing account archive. Each recorded month has at most one current source; replacing it does not delete historical pages or selected detail/track originals. Normalized statistics continue without this consent. Raw sources may contain private location/unknown fields and never appear in entities or diagnostics.

Schema 6 migration first creates a validated `rides.schema5-<hash>.sqlite3` recovery copy (or the corresponding older version). An older integration cannot directly open a schema 6 database; rollback requires restoring a **copy** of its matching older-schema recovery file while the integration is stopped, preserving the newer archive separately. Do not edit production storage manually during operation.

To check a retained source locally, use `ninebot.sync_history` with your `device_id`, `operation: verify_status` and `start_month: "202610"`. The response adds `month_source_check`: `match`, `missing`, `stale`, `unsupported`, `corrupt`, `unverifiable` or `conflict`, plus safe conflicting field names and the read revision. This replays at most one 1 MiB source and makes no cloud request. Its scope is the current month summary, precision and returned ID list; it does not certify all rides, coordinates, units or cloud completeness. Omit `start_month` to keep the existing verification behavior. Previously uncaptured responses remain unavailable rather than being reconstructed from newer data.

Local reconstruction also checks applicable per-scalar provenance/source/presence metadata and a precision **floor** against the retained latest detail. Unreported fields and global parser labels remain outside that proof. Existing extra precision is retained when the value is unchanged, and a later list observation can legitimately mark an older retained detail value as missing. Repair keeps those unrelated states rather than treating the whole ride as newly reported.

With automatic history preservation enabled, failed baseline months are re-enumerated separately, including older months. Only known unverified manifests are selected; healthy old months are not periodically scanned. Gap attempts share the normal budget and persist a 6-hour exponential cooldown capped at 7 days, plus month rotation, across restart. Transport retries continue through the existing run retry policy. Repeated failures do not prove permanent deletion.

Progress now separates `initial_gap_months`, `resolved_gap_months` and current `gap_months`. A recovery needs an actual complete page chain, and recovered IDs/details participate in the health denominator. The historical root can retain `completed_with_gaps` while `sync_state` reports a currently recovered range; original failure evidence is not rewritten. Server-simplified tracks and unresolved local corruption remain gaps. Scope is managed month-completion evidence, not raw-integrity or earliest-cloud-range certification.
