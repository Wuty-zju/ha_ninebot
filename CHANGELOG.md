# Changelog

Entries describe each version at release time. See the [current guides](docs/README.md) for supported behavior and defaults.

## 2.1.3

- Skip Recorder writes for unchanged statistics while retaining corrections and chart configurations.
- Drain active client operations and late subprocess creation before releasing session resources, including concurrent close and repeated cancellation.
- Keep vehicle-discovery completeness bound to its own payload during metadata lookup.
- Reject pending query receipts when the broker closes during final consumer authorization.

## 2.1.2

- Add an optional, separate native task API for previewing missing trip details, requesting review and reading progress.
- Review exact vehicles, dates, selected identities, request limits and expiry in a native administrator panel before approving cloud work.
- Run approved scopes through the existing history scheduler with durable checkpoints, bounded retries, shared account pacing and verified saved results.
- Preserve caller identity and account/vehicle consent through execution; support cancellation, restart recovery and approval-safe backups.
- Add native review/result notices and two versioned task checklists alongside the local read tools.
- Verify the packaged panel, native HA WebSocket, task runner and SQLite result together with synthetic data.

## 2.1.1

- Add versioned public-help search with eleven bilingual topics, immutable source references and bounded local retrieval.
- Include eight read-only task checklists for vehicle selection, statistics, saved trips, synchronization and troubleshooting.
- Clarify cached-data readiness, observation times, access failures and account request pacing in Assistant results.
- Prepare scope-bound detail claims, durable attempt budgets and verified confirmation storage for subsequent task support.

## 2.1.0

- Add an optional native Ninebot LLM API with six local read tools for authorized vehicles, current readings, period statistics, saved trips, local details and history health.
- Require an active administrator and separate account/vehicle consent on every call; preserve user Context and verify ownership again before returning data.
- Return bounded values with units, observation times, coverage and source evidence. Keep precise locations, raw responses and cloud refresh outside the read API.
- Bound per-user call rates, concurrent reads, response size and execution time; release admission on cancellation.
- Document native MCP setup, privacy and troubleshooting in English and Chinese; localize Assistant settings in all shipped languages and provide synthetic evaluation specifications.

## 2.0.4

- Preserve the observed local endpoint and HTTP status in service startup failure logs.
- Identify vehicle-discovery failures while retaining more specific authentication context.
- Clarify native debug logging and diagnostic entity settings in the English and Chinese guides.

## 2.0.3

- Document native dashboard sections for vehicle and period summaries, preserving numeric sensors and their statistics.
- Add a read-only Actions download audit with full release pagination and per-package counts.

- Show control-vehicle selection and historical-sync settings only when their parent feature is enabled on Home Assistant 2026.11+.
- Preserve saved values when conditional fields are omitted, including unlimited request budgets and previously selected vehicles.
- Keep the existing direct form on earlier Home Assistant versions, with unchanged defaults and archive-management access.

## 2.0.2

- Add the native vehicle category and Marketplace installation instructions while retaining HACS installation for earlier Home Assistant versions.
- Read device identifiers through the supported DeviceInfo representation on new and older Core versions.
- Use Home Assistant's contextual entity-name formatter in the history browser and editor, retaining legacy display names on older frontends.
- Add an independent published-Core API check alongside the compatible runtime test boundaries and document default-catalog preparation.

## 2.0.1

- Add native sign-in failure logs with a per-attempt support identifier and reviewed local stage, endpoint and proxy error metadata.
- Keep authentication errors distinct from unclassified service failures, without logging credentials or upstream response text.
- Export basic diagnostics when account setup has failed or its runtime is unloaded, including when the backend package is unavailable.
- Clarify the diagnostic-entity display option and document native debug logging before an account exists.

## 2.0.0

- Complete the 2.0 refactor across 107 betas and four release candidates, with independent accounts, coordinated live data and native Home Assistant representations.
- Provide five adaptive polling profiles, bounded control confirmation and recovery from incomplete vehicle discovery.
- Combine period summaries, semantic events, optional calendars, response Actions and verified long-term statistics imports.
- Preserve ride details and better GPS geometry through incremental acquisition, restart checkpoints, coverage checks and local recovery.
- Store account-owned schema 7 archives with retained history, same-account reconnection and validated scoped export/import.
- Carry forward rc4 runtime behavior, entity identities, archive schema and dependency pins.
- Consolidate bilingual guides and the [2.0 development history](docs/releases/2.0.md); clarify package download counts and third-party attribution.
- Compare stable changes against the previous final version even when an RC was promoted to GitHub Latest.

## 2.0.0rc4

- Include the polling-profile migration and seat-unlock confirmation message fixes from PR #152.
- Drain owned resource work through repeated cancellation before releasing sessions, files or processes.
- Continue independent account-setting cleanup after a store failure and report actionable storage Repairs.
- Confirm rated-parameter writes, retain failed edits for retry and preserve newer changes during an in-flight save.
- Repair today-mileage demand when other daily sensors are disabled; remove its unused duplicate calculation path.
- Read historical progress in one actor snapshot and reuse unaffected vehicles’ summary views and caches.
- Refresh field evidence, translated options, installation guides and candidate-validation reuse.

## 2.0.0rc3

- Drain cancelled import and migration file work before releasing temporary directories, including repeated cancellation.
- Reclaim removed-entry credentials, nominal parameters and event cursors separately from retained account history, with ownership/reference checks and actionable cleanup Repairs.
- Apply consistent bounded Retry-After handling to native history and local REST reads; resume every archive before isolating deferred backup cleanup failures.
- Share daily summary projections across sensor consumers and restore each archived month once during validation.
- Centralize import projection notifications and declare broker-managed platform concurrency, preserving replaceable control confirmations.
- Retry interrupted GitHub metadata reads without replaying release mutations.
- Verify complete candidate validation provenance and matching Git inputs, reuse valid promotion evidence, and select topic changes from the shared ancestor.
- Refresh the English/Chinese guides, upgrade notes and development contracts.


## 2.0.0rc2

- Add five adaptive update-speed profiles with native selectors and translations; retain custom intervals until explicitly changed. Apply polling-only changes without reopening the account or archive.
- Adapt current queries to charging and ACC activity, with finite charge-transition, external-unlock and stop follow-ups. Keep trips settling through bounded current-month checks and preserve lock-confirmation behavior.
- Apply shared local periodic admission, fair deferral and safe activity diagnostics. Avoid routine warning logs for isolated status-query failures.
- Schedule status, battery, travel and discovery independently with bounded asynchronous admission and monotonic deadlines. Keep current readings responsive while another group normalizes or saves data.
- Use separate 60-second status and battery targets by default, retaining legacy custom intervals until a preset is selected. Keep observation validity, query caches and the ten-minute ride-completion window independent of polling speed.
- Keep daily-index repair on its own cadence and expose safe per-group targets, retry waits, queue time and request duration in diagnostics.
- Clean up cancelled or failed history exports after pending filesystem work finishes. Reserve export slots before creating files, and retain completed downloads until their normal expiry or shutdown cleanup.

- Publish rediscovered vehicles immediately and complete native manual refreshes before returning. Reap archive worker threads off-loop before closure finishes.

## 2.0.0rc1

- Prepare the first 2.0 release candidate, carrying forward the account-owned schema 7 archive, validated history transfer, incremental synchronization and connection-recovery fixes from the beta series.
- Expand the English and Chinese installation, history, database and migration guides. Document account removal/reconnection, scoped export/import, capacity management and first-upgrade consolidation.
- Refresh the architecture diagrams and contributor navigation, including the account archive lifecycle and current lock-confirmation timing.
- Add an explicit promoted-candidate publication channel: RC versions from main can be published as a regular Release and Latest.
- Update the full CI boundary to Home Assistant 2026.1.0 and 2026.10.2, verify dependency consistency, retain test/coverage reports and require documentation validation before publication. Refresh legacy test expectations and establish a measured 91% coverage floor.

### Upgrade notes

- Back up the full Home Assistant configuration before upgrading from 1.x or an earlier beta. Legacy history databases are validated and consolidated into schema 7; successfully merged copies are removed. The first upgrade can take a few minutes.
- Existing account settings and entity identities are retained. New accounts keep calendar and background historical acquisition off until enabled in account options.
- The bundled history map and custom cards remain early alpha. Core synchronization, local queries and native entities work independently of that interface.

[2.0 beta-to-RC overview](docs/releases/2.0.md) groups all 107 betas by design stage and delivered capability.

## 2.0.0b106

- Consolidate managed legacy, retired and imported databases into one account archive. Preserve distinct business originals in schema 7, then remove successfully merged source files and temporary rollback copies.
- Merge every selected source in older export packages, including rides present only in their evidence databases. Preserve better tracks and verified discovery coverage.
- Fix archive downloads from account options and use UTC timestamps, account/vehicle counts, scope fingerprints and unique export IDs in filenames and manifests.
- Upgrade supported older SQLite copies before import, retain failed sources for review and keep healthy history available when one source cannot be consolidated.

### Upgrade notes

- Back up Home Assistant before upgrading. Verified legacy database copies are removed after consolidation; failed, foreign or unrecognized sources are retained. Downgrading from schema 7 requires restoring a matching backup.

## 2.0.0b105

- Retain local ride history by default when removing an account, and reconnect the archive when the same account is added again. An account option allows deletion instead.
- Consolidate retained entry archives into one account-owned database with validated ride deduplication, recovery snapshots and preservation of better track geometry.
- Add administrator export and import for whole accounts or selected vehicles. Accept versioned ZIP packages or current-format SQLite files with validation, preview and confirmed merging.
- Resume from verified historical pages and keep ongoing synchronization checkpoints instead of repeating the initial scan.

### Upgrade notes

- Back up Home Assistant before upgrading. Existing entry archives are validated and consolidated when the integration next loads; original files are retained for recovery.
- After migration, reverting to an earlier version requires restoring a matching backup because earlier versions do not read the account-owned archive directory.

## 2.0.0b104

- Preserve known vehicles, native routing and local trip history when cloud discovery returns an empty or incomplete list; retry discovery promptly without renewing uncertain ownership.
- Refresh demanded vehicle status independently of slow history rounds, publish successful readings immediately and reserve queue capacity for real-time queries.
- Use bounded recovery backoff, respect server cooldowns and reduce background acquisition during discovery/connection failures. Expose retry timing and status freshness in sanitized diagnostics.
- Retain entity identities, existing options, precise dependency pins and command confirmation behavior.

## 2.0.0b103

- Refresh demanded trip data at the business-day boundary and after entity setup, retaining request coalescing, account budgets and failure backoff.
- Keep verified same-period daily, monthly and latest-trip values available during short connection failures and offline restart, with original receipt times and explicit cache status.
- Update daily and monthly entities immediately when the local archive changes; preserve entity identities, original precision and strict historical-statistics validation.

## 2.0.0b102

- Enable controls and select all sign-in-verified vehicles for new accounts; retain existing user choices and use a 60-second default status interval.
- Leave rated battery voltage/capacity empty until provided, accept either field independently and support clearing values.
- Repair incomplete current-month ride lists on demand for daily statistics, sharing the account request budget without downloading details or tracks.
- Rename latest distance, average consumption and average speed without changing entity identities.

## 2.0.0b101

- Make the ride calendar and historical acquisition opt-in for new accounts, retaining existing preferences and labeling history settings as Beta.
- Move vehicle controls to the top of account options and use a native multi-select dropdown with clear vehicle identification.
- Replace the hourly history quota slider with an integer input: default 60, or 0 for no hourly quota while retaining serialization and upstream backoff.

- Allow new vehicle/seat unlock commands while an earlier confirmation is pending; newer commands replace the old readback without replaying an earlier command. A seat lock action may cancel an unconfirmed wait but cannot remotely close the compartment.
- Add reported, revised and late-confirmed trip events and concrete power/charging/lock event types, with one default-on Beta switch for both event entities.
- Move latest trip start/end into the existing trip event attributes, retiring the two standalone timestamp sensors. Update automations that reference those sensors.
- Standardize trip/average-consumption names, add today/yesterday average consumption from complete matching records, and display native ride durations in hours with four decimals.
- Clarify the read-only trip history calendar: no active event does not mean disabled.

## 2.0.0b100

- Extend vehicle and seat unlock confirmation to fifteen seconds with immediate target updates and bounded status polling.
- Keep an observed seat unlock visible for at least five seconds while retaining the reported latch state separately. Observe seat status for up to sixty seconds, tapering after fifteen seconds.
- Distinguish latch state from compartment-lid position; existing reports do not provide an independently verified lid-open field.

## 2.0.0b99

- Show compact ride time ranges and durations from original seconds, while retaining numeric sensor identities and native unit preferences.
- Add vehicle state events for observed power, charging and lock transitions, with silent startup/reconnection baselines and localized summaries.
- Clarify monthly trip-list completeness and history progress, and standardize recent-ride names across all supported translations.

## 2.0.0b98

- Consolidate history sync and resume into one diagnostic button; retain pause and the resume Action. Update synchronization icons from cached progress.
- Gate debug-only history status, last-success and raw views without changing their identities or user-disabled preferences. Classify history progress/calendar as diagnostics and use the device name for the location entity by default.
- Share one background-request policy across settings, manual actions and archive writes: default 60, maximum 120 per account/hour, with an adaptive scheduler and existing fairness/backoff.

## 2.0.0b97

- Observe lock commands with up to eight status reads across ten seconds, confirming each fresh target immediately and skipping overdue slots after slow responses.
- Observe seat relocking for ten seconds after confirmation. Keep five seconds of recent-unlock feedback separate from the actual lock state.

## 2.0.0b96

- Simplify sign-in to account and password, then open vehicle feature settings. Keep SMS sign-in unavailable until delivery is verified.
- Hide the opaque region suffix in automatic account titles while preserving custom titles and existing sessions.
- Use one rated-parameter setup entry, keep location settings after sign-in and place debug mode last.

## 2.0.0b95

- Distribute `ninebot.zip` through HACS, with SHA256 checksums and verified runtime contents. Older releases retain source-archive installation.
- Add draft-first automated package publication and cumulative package-download statistics in the English and Chinese documentation.

## 2.0.0b94

- Reuse the validated selected geometry within each local detail read instead of scanning the same full trail twice. Each new read still validates source hashes, identity, full-track quality and offsets; partial corruption fallback and retained higher-quality geometry are unchanged.
- Add a reproducible synthetic archive profiler and same-run baseline comparison. No schema, index, identity, dependency or cloud polling changes.

## 2.0.0b93

- Add permission-checked, cancellable local history subscriptions. Batched archive commits show a local update notice without replacing the selected rides, map view or playback; notifications contain only device versions, never GPS/raw data.
- Share identical in-flight local detail reads per connection/user/parameters; retry a transient local read once. Do not share consumed page cursors or retry cloud operations.
- Refresh stale selected geometry explicitly and preserve comparison state. Reconnect from local first pages, cancel listeners/timers/tasks on disconnect/unload, and fall back to manual reading on unsupported backends.
- Cancel a pending Canvas redraw before a synchronous view redraw loses its handle, so permission changes and map removal leave no queued work on a destroyed renderer.
- No persistent schema, entity identity, dependency or cloud polling changes.

## 2.0.0b92

- Fence descending local history continuations with a connection-local vehicle catalog version. Unrelated vehicles, progress writes and unchanged receipt refreshes no longer discard a valid page; relevant catalog/scalar/membership/saved-detail presence changes still reject mixed reads. Ascending Action cursors retain their account-wide behavior.
- Bind saved track continuations to the selected raw geometry and parser contract rather than unrelated archive/scalar revisions. Legacy responses retain the conservative fingerprint fallback.
- Use transactional TEMP counters/triggers only; no persistent schema migration or archive deletion. Page receipt/coverage metadata is fresh metadata, not an immutable snapshot certificate. Local update subscriptions and request coalescing follow in the next stage.

## 2.0.0b91

- Turn the history card into a map-first workbench with linked list selection, up to five compared rides, selected-record totals and a focused ride summary. Saved detail is read locally; cloud retrieval remains an explicit action.
- Link map route clicks, source-point sliders and sample-curve hover/brush without network requests. Reuse keyed map layers and preserve the viewport; use Show all routes to fit explicitly. Point units, timestamps and CRS remain unverified.
- Add a visual dashboard editor, responsive narrow-card/mobile layout, keyboard selection and reduced-motion playback. Clear private projections on user/connection/config changes and unload.
- No database schema, entity identity, dependency or cloud polling changes. Reload the browser after updating.

## 2.0.0b90

- Add explicit `sort: desc` local history queries with direction-bound keyset continuations. Existing Action calls remain oldest first by default.
- Merge vehicle streams into truly newest-first card pages, retaining unconsumed rows instead of dividing each page equally among vehicles. Local reads are limited to two in flight with bounded buffers.
- Add recent rides (up to five years), last 7/30 days and previous/next month navigation. No cloud requests, archive schema, dependency or entity identity changes.
- Descending continuations are single-use after a successful authorized read; failed requests retain them. Reload the browser after updating the integration.

## 2.0.0b89

- Allow a failed same-month discovery retry to reuse its existing active slot when all 12 month slots are occupied. New distinct months remain bounded by the same limit.
- Preserve superseded attempt evidence, discovered IDs, retry cooldowns and restart continuity; no database schema, dependency or entity identity changes.

## 2.0.0b88

- Automatically re-enumerate unverified baseline months outside the recent incremental window using existing month runs and the shared account budget. Persist fair gap selection and 6-hour-to-7-day retry cooldowns; manual work joins the same scope.
- Retain original failed pages/manifests/root counts. Effective progress separately reports initial, resolved and remaining gaps only from complete managed page chains; recovered run IDs and detail health are included before success.
- Keep stable old months and saved mature details reusable. Broken local evidence is not overwritten by an implicit cloud repair, and server-degraded geometry still shows gaps even when all known details are saved.
- No dependency, database schema or entity identity changes. This is recorded acquisition coverage, not independent raw-byte or whole-cloud certification.

## 2.0.0b87

- Local ride reconstruction now detects applicable scalar provenance/source/presence metadata conflicts even when the numeric values match, and restores precision below the retained detail's proven floor.
- Limit repairs to detail-supported fields. Preserve unrelated presence states, provenance, parser identity and retained higher precision; a later list's missing-field state is not overwritten from an older detail.
- Keep existing identity witnesses, backups, ownership/transaction guards, original raw and geometry selection. No database schema, dependency, entity identity or cloud-request changes; this does not certify all ride metadata.

## 2.0.0b86

- With history preservation enabled, retain one private current raw source per recorded month alongside its normalized projection, including ordinary, fallback, action and native-page receipts. No additional cloud queries.
- Migrate archives to schema 6 with a validated schema-specific recovery copy; retain schema 5 missing-detail startup recovery and prior reconstruction checkpoints. No historical page/detail deletion or entity identity changes.
- Existing local `sync_history` verification actions can replay one month selected by `start_month`, returning `month_source_check` for summary, precision and returned IDs. Missing, stale, unsupported, corrupt and conflicting sources remain distinct; no raw payload is returned.
- Current sources are bounded replaceable snapshots, not a log of every poll. Legacy uncaptured responses cannot be recreated. Raw page/detail receipt certificates retain their original scope.

## 2.0.0b85

- Preserve a history response that explicitly reports a different month as an unverified schema-gap page, instead of rolling back its evidence and retrying the same page indefinitely.
- Do not import its ride IDs or month summary. Continue scanning subsequent months, retaining the gap across restart; local page reuse and reference checks apply the same month constraint.
- No extra cloud requests, database schema, dependency or entity identity changes. A saved anomalous response is not a complete or verified month.

## 2.0.0b84

- Add separate captured-page reference checks to the existing bounded local archive audit: compare verified raw page IDs/counts with page headers, identity indexes and ride/month membership presence.
- Reuse each decoded page, persist progress atomically with the original audit, and report conflicts/unverifiable pages separately from raw-byte integrity. Old raw-only scans remain raw-only.
- Expose scoped results through existing local verification actions; no automatic index repair, cloud requests, schema or entity identity changes. Whole-reference/normalized/cloud certification remains false.

## 2.0.0b83

- Rotate vehicles independently for detail preservation and month discovery, avoiding a fixed vehicle/request-family pairing when both queues stay busy.
- Persist both family cursors across restart, retain legacy cursor fallback and keep the shared account request budget unchanged. No extra cloud requests or entity identity changes.

## 2.0.0b82

- Distinguish current-month recorded pagination evidence from partial single-page observations in local trip responses and the history card.
- Match bounded page headers/ID indexes/membership to the current receipt; newer summaries or inconsistent evidence remain unverified. No cloud requests or entity identity changes.
- Preserve raw-page coverage and unknown coordinate/unit semantics; this metadata indicator is not a raw-payload integrity or whole-cloud-history certificate.

## 2.0.0b81

- Block new history completion when enumerated IDs have missing ride/month links or inconsistent latest-list membership flags.
- Expose affected-ride counts and show verification pending even for a previously completed task with newly detected gaps.
- Keep structural startup/backup validation separate so retained archives can still be backed up and repaired; no automatic selection rewrite or cloud requests.

## 2.0.0b80

- Recover missing ride/month membership links only when bounded original page evidence proves the same identity and start time.
- Use existing local reconstruction backups, ownership guards and atomic checkpoints; preserve current complete/partial-list selection and all raw originals.
- Version the expanded reconstruction contract while accepting old checkpoints; unproven links stay blocked and global reference-completeness claims remain false.

## 2.0.0b79

- Read per-vehicle statistical month facts, the latest ride and their archive revision in one actor operation, preventing mixed versions during background writes.
- Capture historical list detail-saved flags within the same read as its rows, coverage and cursor revision.
- Keep bounded reads, latest-query budget fallback, ownership checks, entity identity and offline browsing behavior unchanged.

## 2.0.0b78

- Preserve the oldest confirmed ride ends first within the existing 150–180 day local risk band, instead of selecting younger trips first.
- Keep newest-first ordering outside that band and use deterministic ride-ID ties; unknown end times do not receive an inferred deadline.
- Preserve per-account budgets, vehicle rotation, stable detail reuse and persisted retries without changing historical range or retention.

## 2.0.0b77

- Show an explicit history-sync reason when an accepted account vehicle list no longer includes a retained vehicle, instead of only an unavailable status.
- Keep history reads, network requests and controls blocked for absent vehicles; retain the original baseline and resume it when the vehicle is positively rediscovered.
- Include only safe vehicle-list freshness metadata in the absence state, with translations for every supported locale.

## 2.0.0b76

- Reuse complete, verified independent old-month page evidence during first history discovery, without consuming another cloud request.
- Validate bounded raw pages, hashes, timestamps, totals and indexed ride membership before atomic promotion; keep original receipts and separate local acceptance provenance.
- Fall back to normal discovery for recent, incomplete, stale or oversized evidence; preserve shared budgets, detail completeness and entity identity.

## 2.0.0b75

- Use the current decision time for detail claims so timer jitter cannot favor later month claims at the budget boundary.
- Publish active history phases, next local request/retry timestamps and successful page/detail commits separately from full-task completion.
- Expose recorded multi-page month counts instead of a hardcoded false real-world verification claim; keep rate limits and entity identity unchanged.


## 2.0.0b74

- Continue vehicle-scoped manual incremental work when automatic synchronization is disabled.
- Skip months waiting for retry so another due month can use the shared history budget.
- Join an existing current-month page run on repeated default sync requests instead of fetching another serve window.


## 2.0.0b73

- Follow up recently saved ride details instead of treating an early response as final.
- Count unchanged scalar and saved detail content as stable observations; growing geometry resets the follow-up count, and retries survive restart.
- Keep mature unchanged details cached, preserve the existing task checkpoint shape and shared request budget.


## 2.0.0b72

- Recover narrowly scoped missing detail originals at startup only when verified receipts and matching account-local sources prove identity and provenance.
- Validate a temporary database before publication and preserve the damaged original file bytes; unsupported or incomplete recovery leaves the original untouched.


## 2.0.0b71

- Persist per-role recovery-source search cursors instead of repeatedly inspecting only the newest eight copies. Inspect at most eight source copies per ride batch; continue through a captured managed-name interval across restart, pause and ordinary background ticks.
- Keep a ride pending while local source search has more work. Report source-copy checks and pending recovered roles without incrementing completed/failed ride counts early. Retain and publish partial recovery while waiting for the other role, then account for that ride once.
- Preserve old checkpoints through explicit additive defaults, use no arbitrary paths or cloud requests, and do not claim an immutable filesystem snapshot or recovery of future copies. Payload digest/owner/metadata verification and forensic preservation remain unchanged.

## 2.0.0b70

- Recover byte-corrupt referenced detail objects from bounded managed account-local recovery copies, only when owner, vehicle/ride, exact existing digest and all source metadata match. Validate decompression, identity and current redaction rules before copying; never change reference hashes, selected geometry or receipt timestamps.
- Preserve and verify bounded damaged bytes in quarantine before replacement, update matching integrity receipts and raw-inventory fences, and commit recovery with the existing per-ride checkpoint. A readable recovered role can be retained even if another role remains unavailable; report partial repair and gaps rather than full success.
- Keep foreign, unreadable, locked, absent and oversized sources isolated. No arbitrary backup path, implicit cloud repair, source deletion, entity identity or Recorder change. The newest-eight-copy search is bounded; a missing candidate is not proof every historical copy was exhausted.

## 2.0.0b69

- Verify explicit ride/detail identities when reading retained latest and selected-geometry originals. Refuse contradictory originals per role, preserve their bytes and keep a readable surviving role through the existing partial-view contract.
- Extend local reconstruction to recompute and restore derived track-quality indexes from independently decoded role originals. Quality-only repairs do not replace newer scalar observations, change geometry selection or invent coordinates, timestamps or units; reuse bounded role-quality parsing.
- Version the reconstruction checker and preserve older job cursors. A partially processed older scan finishes with a declared gap rather than claiming new checks covered earlier records. Refresh cached history progress after repair without clearing unresolved Repairs.

## 2.0.0b68

- Add explicit local reconstruction operations to `sync_history`: start, continue, status, pause and cancel. Rebuild only scalar/time-index facts supported by trusted saved originals; require recorded identity evidence for ID-less details and refuse stale source data.
- Persist bounded per-vehicle jobs and resume requested work on the existing low-priority tick, with one ride per batch, restart-safe fairness and one checked private recovery point per job. Commit repaired indexes and checkpoints together; preserve raw bytes and receipt times. Ordinary startup and backup checks remain strict.
- Refresh local projections after successful repairs and expose safe cached recovery counts on existing sync entities. Preserve unresolved Repairs, explicitly report changing scopes and unverified fields; this does not prove all references or complete cloud history. No cloud requests, vehicle commands, new entity identities or Recorder migration.

## 2.0.0b67

- Upgrade archives explicitly to schema5 with a validated source-schema recovery copy and atomic rollback. Preserve business payloads/checkpoints, expire pre-upgrade scan certificates and require the matching backup for older-version rollback.
- Bind reusable object-check receipts to account/vehicle, logical identity, digest, source metadata and checker contract. Between daily deep scans, verify only unmatched receipts; per-vehicle mutation fences avoid unrelated-car rescans. Report receipt scope separately from cloud/reference/normalized completeness.
- Preserve bounded corrupt bytes and their compressed hashes in a private deduplicated quarantine, without deleting originals. Oversized objects remain pinned in place; missing bytes are recorded explicitly. All blob reads remain bounded before materialization, and an oversized object no longer blocks healthy reads on reopen.
- Expose only safe receipt counts/scope through verification Actions and cached audit scope on existing progress entities. Trusted index reconstruction and complete projection/reference recovery remain subsequent M5 work.

## 2.0.0b66

- Recover read-only local detail views from independently validated latest or selected-geometry payloads. Preserve normalized scalar snapshots, explicit unavailable-role/evidence provenance and real receipt times; refuse when neither object survives.
- Keep archive writers and quality backfill strict, preserve corrupt bytes and Repairs, and flag missing payload objects without weakening database/schema/account checks. Bound local blob reads before opening oversized bodies.
- Show localized partial-data notices in the history card and include evidence-role changes in track chunk fences to prevent mixed routes. No implicit cloud repair, entity identity or Recorder changes; full trusted reconstruction and incremental audit receipts remain follow-up work.

## 2.0.0b65

- Advance local history payload audits in low-priority bounded batches after normal preservation work. Share manual checkpoints, persist vehicle fairness and cool completed scans for a day instead of restarting for every arriving page. Respect automatic-sync opt-out, pause, backup, shutdown and storage/ownership guards; use no cloud request budget.
- Reserve compressed and decoded byte budgets before opening bodies, load one object at a time, flag oversized objects from metadata and yield between objects after a cooperative time budget. A single bounded decode is not preemptible.
- Expose cached captured-audit phase/counts/time on existing history progress entities. These are not cloud coverage or current integrity certificates; full incremental audit receipts, reference/index checks and trusted recovery remain subsequent work.

## 2.0.0b64

- Isolate corrupt saved payloads from healthy archive writes and other vehicles. Preserve damaged bytes, report a separate translated Repair and retain strict database, schema and ownership protections.
- Add explicit local verify, verify_continue and verify_status history operations with persisted checkpoints and at most eight objects per batch. They use no cloud requests and do not imply complete cloud coverage or normalized-index verification.
- Fence audit validity against both projection revisions and transactional raw-inventory changes, including raw-only page receipts. Preserve earlier scan evidence, expire current proofs after later writes and treat older unfenced checkpoints as unverified.
- Document the bounded manual verification workflow. Automatic deep audit, proven recovery and map/calendar/statistics consistency remain subsequent M5 work; real cloud pagination is still unverified.

## 2.0.0b63

- Add four enabled local history sensors: sync status, verified month scan progress, saved discovered-detail progress and last successful synchronization. Keep unknown denominators and distinct completion stages; properties never query the archive or cloud.
- Add history sync/pause/resume buttons through scoped Actions, preserve user context and keep historical work independent of physical controls. Update cached views immediately after operations and for newly discovered vehicles.
- Expose automatic history sync and per-account archive quota (32–2048 MiB, default 100). Preserve unedited options, never delete data on quota decrease, and recheck capacity before resuming writes.
- Add names/states/icons in 21 locales; diagnostics now obtains the actual installed integration version from the HA loader. Real cloud pagination remains unverified; resumable deep archive verification is M5 follow-up work.

## 2.0.0b62

- Separate the fixed first-history baseline from coalesced single-month incremental scans. Reconcile recent observations locally before requesting extra pages; rotate baseline, incremental and legacy tasks under shared budgets.
- Add synchronize, pause, resume and local sync_status history operations. Explicit months all enter durable work; manual requests continue across restarts even when automatic seeding is off. Preservation and per-vehicle pause still govern execution.
- Deduplicate page bodies by content while retaining independent receipts and legacy inline evidence. Schema 3 upgrades save a validated recovery copy before schema 4 changes.
- Keep transient runtime fences from disabling healthy archive writes. Progress entities and user capacity options are separate follow-up milestones; real cloud pagination remains unverified.

## 2.0.0b61

- New accounts enable background full-history discovery for fresh owned vehicles, with lazy month/page checkpoints and the existing shared request budget. Existing explicitly disabled preservation stays disabled.
- Commit normalized ride memberships with private page evidence; keep live monthly raw data and legacy selected-range jobs separate. Reuse saved details and build quality indexes locally without downloading them again.
- Complete the baseline only after discovery and detail evidence are resolved; expose internal gap outcomes for simplified, missing or invalid tracks while retaining the best saved geometry. Progress entities and manual synchronization controls follow in later phases.
- Preserve custom account titles during reauthentication and setup; continue updating automatically generated account titles.

## 2.0.0b60

- Persist independent full-history roots, page evidence and monthly scan manifests. Commit evidence and checkpoints atomically; repeated/empty pages, changed totals and schema gaps cannot appear as full coverage.
- Share the existing durable history request budget; preserve in-flight claim leases and retry checkpoints across restarts. Retain unknown safe business fields while redacting credentials.
- Migrate schema 1/2 with a validated recovery backup and disk-space preflight. Preserve old data when capacity is lowered; support a bounded capacity recheck through the archive API. Default remains 100 MiB.
- This is the M1 storage foundation, not automatic full-history synchronization or progress entities. Downgrading after schema 3 migration requires restoring the pre-migration archive backup.

## 2.0.0b59

- Add a history-only encrypted page transport using the pinned ninecli session and shared client lock. Existing telemetry, controls and trip details continue through authenticated loopback serve.
- Only ninecli can refresh/write the session; account/file-revision fences reject foreign or rotated responses. History transport fixes the upstream host, verifies TLS, refuses redirects and bounds streamed/gzip responses.
- This is the M0 protocol foundation. Automatic full-history jobs, progress entities and cloud pagination completeness are not delivered in this version; no production cloud tests were performed.

## 2.0.0b58

- Fix fetching old recorded trip details when the latest monthly response contains only newer rides. Resolve verified vehicle/month memberships directly without rereading the month.
- Show saved-detail availability in the trip card; missing routes display guidance instead of sending a predictably failing local request. An explicit detail fetch enables the saved route when persistence succeeds.
- Keep partial history, account/vehicle guards, conflict and complete-list withdrawal handling, entity identities and archive schema unchanged.

## 2.0.0b57

- Extend the optional history card with up to five selected vehicles and saved-route overlays, bounded local pages and per-vehicle continuations. Keep existing single-vehicle configuration compatible.
- Add strict archive-only detail reads, version-fenced source chunks, gap-aware raw curves, local source-point playback and bounded display thinning for long routes; originals remain intact. Clear private projections on user, connection, date, selection or access changes.
- Bundle pinned Leaflet 1.9.4 and its license; tiles are off by default, with explicit optional OpenStreetMap privacy disclosure. Simplified historical points are previews, and coordinates/point units remain unverified.
- Provide standard native calendar/statistics card configurations without automatic Recorder writes or private frontend APIs. No new entities, identity migration, cloud capture or vehicle control.

## 2.0.0b56

- Add durable per-vehicle sparse month repair through sync_history repair/repair_continue/repair_status/repair_cancel. Select up to 12 months without rereading the intervening history; shared account budget and background fairness survive restart.
- When preservation is enabled, reconcile the previous month at most daily during the first seven days of the new month. Keep partial coverage explicit; one successful partial read ends a repair instead of repeatedly polling an unsupported cloud page.
- Bound queue metadata to 128 tasks, preserve all ride/track evidence when recycling finished task slots, drain cancelled/unloaded queries and retain backup/checkpoint fences. Jittered retries respect Retry-After; eight consecutive sparse failures suspend the task until an explicit new request.
- Keep ninecli serve, authentication, IDs and raw unit/coordinate boundaries. History maps remain the next H5 phase.

## 2.0.0b55

- Resume explicitly selected missing-month ranges in opt-in background preservation, one missing month per tick with durable checkpoints and the shared account detail/month request budget. Local known partial months remain explicitly partial rather than being repeatedly fetched as unverified pagination.
- Persist vehicle fairness and alternate pending range/list work with detail preservation across restarts; keep existing foreground sync batches when background preservation is disabled. No new automatic full-history range, entity identities or cloud endpoint assumptions.
- Previous-month reconciliation and explicit sparse month repair remain subsequent H4 work; H5 map rendering is not yet delivered.

## 2.0.0b54

- Add opt-in background preservation for known ride IDs, independent of enabled calendars/entities. Persist claims, retry times and account budget in the existing archive; restart does not reset the budget.
- Reuse stored stable details, detect scalar fingerprint changes, use finite recent-ride rechecks, round-robin vehicles and background Broker priority. Defaults to 30 logical detail requests/account/hour; no full-history scan or unverified cloud paging.
- Preserve existing backup/cancellation/owner fences, safe diagnostic status and no repeated ordinary failure logs. Sparse month-gap scheduling remains a subsequent H4 step; map rendering remains H5.

## 2.0.0b53

- Prefer account-scoped persisted ride details and month observations in historical queries; repeated saved-detail reads issue no cloud request and remain usable during authentication/freshness failures while local ownership remains valid. Explicit refresh requires live access and bypasses the response cache.
- Add bounded source-sequence point_offset chunks, preserving full-response quality counts and unknown point units/coordinates. Include archive/scalar revision and geometry receipt context without exposing raw payloads.
- Keep coordinate opt-in, permission/revocation fences and existing response keys; automatic incremental preservation and map rendering follow separately.

## 2.0.0b52

- Preserve requested ride details, full original trail and speed samples in the account-scoped private SQLite archive; sanitize credentials/profile data and retain unknown business fields. Payloads are compressed and content-deduplicated without card/Action point truncation.
- Store the latest response and independently selected geometry; valid scalar corrections still update while degraded geometry cannot erase an older detailed route. Bound revisions to two selected payloads per ride, with no automatic expiry or extra cloud polling. Capacity stops writes rather than deleting history.
- Migrate v1 scalar archives transactionally with a consistent local v1 recovery copy. Existing backup hooks cover private details. Local-first Action retrieval follows in H3; this stage does not yet change query routing.

## 2.0.0b51

- Validate the entire bounded trail response independently of the displayed point limit; report valid/invalid/duplicate counts and strict upstream simplification metadata in historical Action responses. Preserve original point indexes and unverified units/coordinates.
- Keep previous usable geometry when a new detail is empty, simplified, malformed or an unverified reduction, while still applying valid distance/duration/energy corrections. This prevents geometry protection from freezing scalar updates.
- This stage adds quality contracts only: full private detail persistence and cache-first queries follow separately. No extra cloud polling, production HA changes or entity identity migration.

## 2.0.0b50

- Restore today/yesterday ride counts, duration and energy when retained local rides reconcile with the latest monthly count and distance, even if one response returns only a partial list. Check energy and duration independently against monthly reports.
- Keep upstream list coverage, existing entity IDs, source seconds, precision, freshness, account isolation and complete-response omission rules unchanged. No extra polling or automatic historical scan.
- Add focused cloud regression for accumulated lists, restart, field conflicts, scope, query budgets and existing daily/action/export/calendar boundaries; no production HA changes or live vehicle requests.

## 2.0.0b49

- Remove the deprecated tracker `battery_level` property and use the main-battery SOC sensor for device battery information.
- Keep the emergency communication battery independent of the main battery.

### Upgrade notes

- Change automations that read the tracker's `battery_level` attribute to use the main-battery SOC sensor.

## 2.0.0b48

- Separate precise range (`precise_estimate_mileage`), estimated range (`estimate_mileage`), and AI range (`ai_estimate_mileage`).
- Display ranges in km with one decimal place and place AI range in diagnostics.
- Preserve existing entity identities and user display choices.

## 2.0.0b47

- Display all ride durations in hours with two decimal places, including zero; retain native seconds and HA unit conversion.
- Migrate existing owned ride duration entity unit/precision once to h/2 through public registry APIs; preserve IDs and subsequent user choices. Align calendar and optional detail card formatting.
- No functional tests, compatibility dispatch, cloud queries or production changes, per maintainer instruction. Automatic style/distribution checks only.

## 2.0.0b46

- Set integration_type to service as requested; the standard HA entry section now uses the Services heading. No custom account-heading patch.
- Change all ride duration sensors to suggested hours with native seconds retained. Migrate existing owned ride duration registry units once through HA public APIs, preserving IDs and subsequent manual unit changes.
- Use HA DurationConverter for calendar/hour response presentation; keep backward-compatible duration_s and add duration_h. Align the optional history card to hours. Retain integer presentation from b45: short rides may round to 0 h; exact seconds remain available.
- Per maintainer instruction, no functional tests, compatibility dispatch, live queries or production operations. Automatic style and distribution checks only.

## 2.0.0b45

- Reserve the vehicle battery device class for propulsion SOC; retain emergency communication SOC as a separate percentage sensor and explicitly supply main SOC to the GPS tracker.
- Suggest one decimal for Wh/km and integer minute display for ride duration through HA native seconds-to-minutes conversion. Keep unrounded seconds in domain/archive and duration_seconds. Month list coverage suggests integer percentages.
- Add an account option to enable/disable local ride calendars without deleting history or entity identity. Align calendar distance, duration, speed and energy formatting; add start/end and energy intensity to descriptions.
- Keep an observed ride event available through temporary cloud staleness while ownership/storage remain valid. Explain waiting-for-new-ride and expose the last reported ride end, without triggering historical events. Include energy in new completion events.
- Add bounded coordinate-free point-distance samples and track counts to historical responses and the optional card, alongside existing raw speed curves. Point units and CRS remain unverified; do not plot a supposedly accurate GPS map or fabricate km/h sample values.
- HA still controls the Hub heading; there is no supported per-integration Account section override. No functional tests, cloud queries or production operations were performed, per maintainer instruction.

## 2.0.0b44

- Force a one-time canonical rename of owned existing entity IDs, including pre-upgrade custom IDs, preserving unique IDs and old/new migration evidence. Keep collision/ownership guards and subsequent user renames; include inactive parameter entities and converted locks. Update external references from the migration mapping.
- Normalize account titles to the reported username/account/optional region, with one missing-name enrichment at successful setup and no ongoing whoami polling. Retain independent multi-account entries. HA owns the search-section heading; no global frontend patch or unsupported Account heading is introduced.
- Suggest minute duration display with retained native seconds, one-decimal distances, integer consumption/max speed and zero without fractional padding. Keep unrounded raw/domain/archive values and explicit user presentation choices. The optional card displays minutes/integer seconds.
- Standardize main-battery labels/localized statuses, shorten Chinese battery type names, display score/service-remaining zero and explicitly unsupported cycles as Not supported, and missing odometer as Not reported. Move vehicle image to diagnostics; retain device-linked GPS tracking and coordinate consent.
- Per maintainer request, no functional tests or live vehicle/production operations are performed for this release; JS tests join the existing manually dispatched major compatibility workflow rather than running on every beta. Routine distribution/style validation remains automatic.

## 2.0.0b43

- Package an optional ride-history dashboard card: local date-range pages, scalar ride details, source precision and coverage, with explicitly requested single-ride cloud details. Opening or paginating the card never queries the cloud; no GPS tracks, controls, background history scans or browser persistence. Raw speed samples remain explicitly unit-unverified.
- Use the authenticated Home Assistant service-response API, an integration-contained async static route and 21 locale dictionaries. Bound memory to one page and five details, discard stale responses after user/device/date/connection changes, and keep sources in an optional disclosure.
- Apply the same vehicle-scoped Home Assistant permissions to legacy history/statistics/migration Actions; synchronization and Recorder imports require control permission. Preserve vehicle identities, archive facts and b40 remote start/stop polling without a local motion/P prohibition. Correct the current-use field inventory without changing recorded payloads or frozen evidence.

## 2.0.0b42

- Add response-only `get_recorded_trips` for bounded date-range browsing of the local archive, including offline reads. Opening a page never refreshes a month, downloads details or controls the vehicle. Keep received timestamps, source precision and explicit missing/partial/unknown coverage.
- Use revision-fenced keyset pagination, at most 100 rides per page, five-year date ranges, and eight 15-minute runtime continuations per account. Archive changes require a fresh page; incomplete lists retain prior observations and complete corrections preserve evidence outside the current view.
- Enforce Home Assistant read permissions for local browsing and trip/detail queries. Restricted readers need the selected vehicle’s ride calendar, plus its tracker when requesting GPS. Recheck scope and permissions before returning; keep tracks out of the local Action, state and Recorder. Preserve b40/b41 remote command behavior and all entity identities.

## 2.0.0b41

- Add one read-only local ride calendar per vehicle, with native month/day/list navigation and small scalar ride details. Browsing is bounded to five years, 5,000 scanned records and 8 MiB, never triggers cloud backfill and never exports GPS tracks. Preserve complete/partial list correction semantics and archived evidence.
- Restore existing last-ride sensors from a memory projection across older archive months during cloud/authentication failure. Preserve entity IDs, source precision, actual observation receipts and live freshness; historical display never emits completion events or grants control permission.
- Notify new Core calendar subscriptions on archive content changes, without repeated reads for unrelated status/BMS updates. Retain 2026.1 date-read compatibility, 21 locale names/messages, exact ninecli pin and b40 command/readback behavior. Native calendar/trend UI was checked in a synthetic isolated HA, without vehicle/cloud access or production writes.

## 2.0.0b40

- Allow remote stop/vehicle locking without local motion or P telemetry. Keep account consent, allowlist, ownership and known cloud-denial checks; vehicle/cloud conditions decide execution. Retain both remote buttons and the seat-unlock button, with no remote seat locking.
- Send each explicit request once, confirm state at 0/2/5 seconds, then briefly read status at 10/20/25 seconds to observe automatic relocking. Replace follow-up on a new same-vehicle command, stop on errors/unload, and never retry the command or optimistically change lock state.
- Simplify consistent lock feedback in all 21 locales. Keep entity identities, migration history, the exact ninecli pin and minimum HA unchanged. No physical vehicle control was tested.

## 2.0.0b39

- Persist normalized monthly facts, scalar ride details, field provenance and precision in an account-scoped SQLite archive. Import the validated v1 statistics source once without modifying it; retain history beyond its old 500-ride cap. Current/adjacent-month memory projections and bounded range reads use the same archive.
- Add response-only `sync_history`: at most three missing-month queries per explicit batch, one durable job per account, atomic facts/checkpoints, restart continuation, cancellation and bounded failure cooldown. Reuse known months including partial lists, without pretending to have upstream pagination or complete history. No background full-history polling, detail downloads or controls.
- Restore minimal known vehicle profiles and historical queries when the first cloud refresh fails. Preserve authentication, ownership, live freshness and control restrictions; cached profiles do not grant live permission. Closed-month queries prefer local facts, and offline current observations expose their actual age.
- Add official local backup hooks that drain and freeze archive writes, including setup-in-progress actors, and thaw on completion/failure. Close the actor on account removal and clean only its known database/journal files after backup; unexpected files/links remain with a Repair. Distinguish capacity, I/O and incompatible-storage Repairs.
- Keep a 100 MiB per-account write budget without silently evicting old history. Full storage retains readable facts. Raw JSON, GPS tracks and speed samples stay in bounded runtime caches, outside SQLite, entity state and Recorder. Preserve meaningful entity identities and the exact ninecli/HA dependency boundaries.

## 2.0.0b38

- Add native vehicle and seat Lock entities while retaining the original buttons. Share strict status interpretation, explicit controls/allowlist/ownership policy and a same-physical-vehicle lease across accounts. Vehicle stop requires reviewed fresh stopped-and-P evidence; current ninecli data has none, so remote locking is refused. Seat locking is manual only.
- Send each lock command at most once; compare its target against up to three new single-attempt status reads within a ten-second absolute deadline. Keep accepted, confirmed, unobserved and uncertain outcomes distinct, show pending without optimistic state, respect cooldown and reclaim on cancel/unload. Broker admission, safety and cooldown rejection before the wire reports not sent without extra reads. Bell retains ordinary one-read reconciliation.
- Journal cross-domain conversion before replacing exact owned old lock binary/sensor rows. Retain UID strings and user choices, preserve old Recorder data and expose the old/new mapping; new Lock history starts separately. Preserve conflicts rather than guessing. Back up matching storage before upgrade/rollback and update automation IDs and on/off conditions.
- Add Lock names, action errors and safety status in all 21 shipped languages, plus icons. Keep the existing authenticated loopback, dependency pin, entity naming seeds and telemetry budgets. No real controls or production deployment.

## 2.0.0b37

- Schedule shared reads through a bounded account broker: one wire request per account, two across accounts, priority with vehicle fairness, independent read/control budgets and cancellation-safe single-flight. Preserve the authentication/native routing transaction lock; commands are never coalesced or replayed.
- Preserve receipt timestamps and revisions through executor parsing and cached normalization. Reject stale ownership/generation results before telemetry, ledger and lifecycle commits; merge groups independently. Serialize ledger candidates so concurrent vehicles/months cannot overwrite each other's records.
- Retry transient read failures at most once within the same request deadline. Honor bounded transport Retry-After metadata and a shared-transport recovery cooldown without extending it on rejected requests; maintain existing per-group backoff and last-known data. No extra request logging or raw/identity diagnostics.
- Keep retained historical statistics and Yesterday sensors readable when live cloud freshness/authentication fails. Explicit cloud refresh still requires valid ownership/authentication; removed vehicles and unloaded accounts cannot access local history. Cold-start offline recovery remains a later archive improvement.
- Create a new status request barrier after every command attempt. This prevents a pre-command request from substituting for reconciliation; target lock-state confirmation and stopped/P safety remain a separate phase. Existing entity identities, options and the exact ninecli pin are unchanged.

## 2.0.0b36

- Freeze canonical account/model/serial entity ID seeds in a private, versioned store. New accounts use business-identity-scoped unique IDs and devices, including the same vehicle visible to multiple accounts; existing meaningful unique IDs remain unchanged. Names and model changes do not rename entities.
- Rename only recognizable generated IDs through the public entity registry API, preserving registry identity, user settings and same-domain Recorder metadata. Preflight occupied targets; preserve custom or unverifiable IDs and ambiguous devices. Journal intent before mutation, stop on storage failure and recover interrupted migrations.
- Add local response-only get_entity_migration for per-vehicle old/new IDs and outcomes. Review YAML, templates and external dashboard references after upgrading; they are not automatically rewritten. Back up matching HA storage before upgrade/rollback. Lock platform changes remain a separate phase.

## 2.0.0b35

- Add account entry labels and reviewed username/region titles after explicit login. Preserve custom titles, isolated password/SMS sessions and offline migrations; optional display metadata failures do not prevent a validated login.
- Add emergency communication battery SOC (integer %) and main battery type (1 lithium / 2 lead acid) from maintainer-confirmed fields. Interpret ACC, seat lock, battery presence and service expiry on their existing sensor identities; unknown codes remain unrecognized. Clarify Apple Find My and cycle-support states.
- Move vehicle location and returned battery pack count into ordinary entity categories. Location display defaults on; preserve existing explicit off choices. This reuses status data, adds no location query and does not convert the unverified coordinate system.
- Preserve reported decimal scale for measured/ride values and distinguish derived precision. Retain existing entity identities and control behavior; cross-platform lock integration and safety/readback improvements follow separately.

## 2.0.0b34

- Add explicit import_statistics response Action for native Recorder day/month trends from the existing ledger. No cloud queries or automatic production imports. Import only closed, verified periods; preserve real zeros and gaps without distributing monthly totals or inventing hourly values.
- Use separate account/vehicle/grain/metric statistic IDs, bounded serialized imports and strict metadata checks. Merge existing period values and rebuild cumulative suffixes for corrections and repeat-safe imports; retain previous verified points when newer data is missing.
- Return copyable native statistics-graph configurations and source/skip/queue counts. Initially require HA time zone Asia/Shanghai for accurate business-day grouping; period queries remain available elsewhere. Update English/Chinese Action text and all shipped Action names; preserve curated translations during regeneration.

## 2.0.0b33

- Add device-scoped get_statistics response data for one to six months, with separate monthly server aggregates and daily projections. Include real source timestamps, revisions, coverage, field availability, period-end observation and scope-matched Wh/km. Missing days remain null; future chart padding is omitted.
- Read the persisted ledger without cloud requests by default. Explicit refresh shares the existing cache/queue and updates only the ledger, not current-month telemetry or ride events. Recheck account ownership after processing; no tracks or per-ride entities are introduced.
- Expose adjacent-month provenance and an explicit missing-adjacent-month reason for first-day ride attribution. Add localized Action names and a script response-variable example, validated in isolated HA. Recorder trend imports remain a separate H4 increment.

## 2.0.0b32

- Add visible Yesterday distance and Today/Yesterday ride count, duration and energy sensors from the bounded statistics ledger. Preserve real source timestamps, field availability and window completeness; missing reports are gaps, never fabricated zeros. Server daily distance remains separate from end-date ride attribution.
- Add native Wh/km sensors for this month and the last reported ride using matching distance/energy scopes. Retain meaningful existing IDs, user names, disabled choices and Recorder history; last-ride attributes distinguish reported/stabilizing/revised/local-policy completion.
- Retire five runtime-only manual-history placeholders. Query progress and historical scope remain in Action responses. Registry cleanup is limited to exact reviewed identities on this account's unambiguously owned vehicle; Recorder history is not deleted.
- Fetch the adjacent month only when daily consumers need the rollover window (the first two business days), with existing cache/rate budgets. A slower adjacent query cannot renew the current month's received timestamp. Update names/icons in all shipped languages.

## 2.0.0b31

- Persist bounded, versioned normalized travel statistics separately from raw data, Action continuations and event cursors. Current and explicitly queried historical months share replace/upsert records with actual source timestamps, revisions, backend version and list coverage.
- Retain known fields through partial same-ID corrections while recording which fields were actually present in the new report. Restore does not refresh telemetry or replay events; metadata eviction cannot imply complete rides.
- Limit each account ledger to 360 month records, 500 ride metadata records and 2 MiB. Never store raw responses, GPS trails, tokens or account names; malformed/future storage is preserved and pauses only this optional ledger with a Repair. Flush on unload and remove the ledger when its account entry is explicitly removed.
- Correct diagnostics integration version to match the release. Existing entities and history Action contracts remain unchanged; additional day statistics and visualization follow in the next phase.

## 2.0.0b30

- Require distinct successful travel samples with unchanged ride metrics over a ten-minute quiet window before emitting a completed event. Growing end reports and cache rereads cannot establish completion; this is a conservative local policy.
- Keep stable identity separate from mutable metrics: later corrections never replay a completed event. Conflicting duplicate IDs and non-finite metrics fail closed; observations remain bounded and are cleared on unload or ownership loss.
- Restore the original event display timestamp after an unavailable state using Home Assistant restore data, without triggering an event or synthesizing a timestamp for older stored data. Existing entity IDs and cursor storage remain compatible.

## 2.0.0b29

- Fix the rated battery parameter form failing with HTTP 500 after selecting a valid vehicle: use serializable native number selectors with V/Ah units, retaining strict finite/range validation.
- Verify the actual HTTP options flow, including renamed/same-name vehicles, foreign-account rejection and invalid parameters. Existing device selector IDs, parameter storage and entity IDs remain unchanged.

## 2.0.0b28

- Add a visible-by-default Today distance sensor from the validated monthly daily chart. It requires the current Asia/Shanghai business date and a fresh successful travel sample from that day; missing, inconsistent or yesterday-only data remains unknown.
- Notify local midnight changes without a cloud call. Today distance requests only the month group, not last-ride fallback/detail/history polling.
- Add a native distance unit/class, icon and names in all 21 shipped languages. Existing entity IDs and user-disabled choices remain unchanged; no cumulative statistics class is assumed.


## 2.0.0b27

- Add scope-matched Wh/km to ride, month and cross-month history Action responses. Server summary and indexed/returned ride statistics are separate, with explicit basis and coverage.
- Deduplicate identical returned ride IDs; conflicting or missing identity prevents misleading subset totals. Missing energy/distance and zero-distance intensity remain unknown.
- Preserve accumulated indexed statistics across history cursors; calculate weighted aggregate intensity from sums rather than averaging ride ratios. No new entities or cloud requests.


## 2.0.0b26

- Preserve known ride metrics, averages and tracks when a partial detail response omits, nulls or invalidates fields. Verified empty trail is distinguished from missing data.
- Add a scoped RideDetail domain result and per-field presence/source metadata to existing history Actions without changing schema 2 keys or entity identity.
- Reject cross-month/ID detail merges and revalidate combined timestamps/duration. Maximum speed remains separate from distance/duration average; unknown track units and coordinates remain unchanged.


## 2.0.0b25

- Preserve the measured installed ninecli version and trusted endpoint metadata through backend results into raw records; unknown metadata stays unknown.
- Add bounded, value-free schema-change diagnostics, entry-local anonymous unknown-field structure comparisons, and fixed raw rejection reasons.
- Add opaque raw references that expire on replacement, eviction, detail TTL, ownership removal and unload; keep existing byte/count budgets and entity identities.
- Separate local development material from the product repository; retain standalone public test inputs and user-facing documentation.
- Validation: 83 targeted offline tests, Ruff/format and mypy. No vehicle/cloud/control/production tests; full compatibility testing is deferred to the P1–P2 foundation milestone.

## 2.0.0b24

- Remove SOC-derived sample/daily/monthly/total energy and quality entities; retain optional rated specifications with one stable nominal-energy ID.
- Merge range display and remove redundant protocol/battery/lock metadata entities; preserve safe debug observations and meaningful IDs.
- Replace the model vehicle enum with an HA device selector and account/freshness validation.
- Read legacy V/Ah only; invalid optional parameter storage does not block telemetry or overwrite its file.
- Cache entity topology, remove model-driven polling/midnight writes, and provide 21-language entity/settings/action labels.
- Simplified Chinese and English are complete baselines; other locales use English fallback for some long help/errors.
- Upgrade removes reviewed obsolete identities; automations using retired entities require adjustment. Recorder data is not rewritten.

## 2.0.0b19–b23

- Completed current-field adaptation, month chart/coverage, model options/debug, SMS login and bounded cross-month history. See the [release-specific record](https://github.com/Wuty-zju/ha_ninebot/blob/9ba20bcadea1ea0500d9c080e60a48adc35e2387/docs/v2x-实施与验收记录.md).

## 2.0.0b18

- Validate vehicle identity before accepting the native routing-cache update; malformed profiles restore the previous cache.
- Use targeted regressions for small prereleases; keep routine lint/Hassfest/HACS and run the full pinned compatibility matrix manually for major updates.
- 20 targeted offline tests and type checks passed; no new cloud requests, vehicle actions or production changes.

## 2.0.0b17

- Distinguish partial native vehicle discovery from complete account lists; exit 0 alone cannot confirm vehicle removal.
- Preserve known native routing cache on failure, merge only verified native rows on partial success, and retain refreshed tokens.
- Track profile freshness per vehicle so another vehicle's success cannot renew missing ownership or enable its controls.
- Bound both CLI output pipes, finish cache I/O before releasing the operation, and add safe profile diagnostics without new entities or polling demand.

## 2.0.0b16

- Report failed state confirmation when a vehicle disappears after a command. A completed pre-command manual query cannot substitute for the new readback, and old task cleanup cannot replace a newer task.
- Reconcile status at most once after a non-authentication control error, preserving the uncertain command result without resending. Authentication failure, cancellation and unload stop additional I/O.
- Add a bounded, memory-only control-result diagnostic with up to 64 entries. Distinguish acceptance, errors and readback without retaining response bodies, identities, locations or exception text; no new entities.
- Align English and Chinese readback errors. Verification used offline tests.

## 2.0.0b15

- Separate local dispatch conditions from cloud permission decisions. Keep control enablement, vehicle allowlists, authentication, freshness, error and command-support checks; let the cloud authorize unknown permissions while rejecting explicit denials and ambiguity.
- Keep the four control buttons available when permission evidence is absent. Describe readiness as permission to send, without claiming cloud authorization or physical completion. Failed commands are not retried; status is still reconciled.
- Try each of the four commands once on one authorized vehicle. All returned HTTP 200, ok=true and empty data; readback succeeded without a change to the selected states, leaving physical effects unverified. Temporary sessions and processes were cleaned up.
- Add selected empty-success fixtures and classify 12 command-proxy fields separately from the 136 business paths. Remove fake-allowance test dependencies while retaining denial, queue-change and user-disable regressions.

## 2.0.0b14

- Require an explicitly returned status SN to match the requested vehicle. Invalid or mismatched identities cannot replace raw data, telemetry or success times; missing/null identity retains partial-failure and TTL behavior.
- Add full encrypted-control simulation with an instrumented temporary binary: four accepted commands and one refusal, leaving the original binary unchanged.
- Document the boundaries between the recon reference key/DeriveKey, opaque commands, native success and physical outcomes; add MIT test attribution and field usage.
- Pass 376 tests with 97.87% branch coverage, plus Ruff, formatting and mypy. Unknown-permission gating is unchanged in this version.

## 2.0.0b13

- Add an optional raw-data summary diagnostic with bounded cache counts and four integer metrics per vehicle. Exclude raw values, arbitrary field names and location; no extra cloud polling.
- Inventory 136 observed paths and 122 non-container fields across five business endpoints, recording usage, units, classification, sources and limitations.
- Document the unvalidated status SN, model-language fallback, CLI business-line cache and entity defaults against the implementation.
- Add recorded-field coverage, privacy, vehicle-isolation, TTL and native HA-state verification. Pass 369 tests with 97.81% branch coverage and static checks.

## 2.0.0b12

- Add remote start/stop buttons while retaining bell and seat-unlock identities; do not reintroduce a Lock platform in this version.
- Enable integration-disabled buttons only after explicit control and vehicle selection. Preserve user-disabled choices and existing authorization checks.
- Add a default-visible control-status diagnostic enum with four compact action-state attributes, complete English/Chinese translations and no extra cloud queries.
- Verify four REST control routes offline against the pinned native binary; distinguish REST control cache requirements from CLI/BMS routing.
- Pass 366 tests with 97.80% branch coverage, plus Ruff, formatting and mypy.

## 2.0.0b11

- Fix BMS no_cache failures caused by REST vehicle listing not creating the routing cache. Discover vehicles through native JSON, build the business-line cache, then start authenticated REST as needed.
- Share the serial lock, eight-item queue, 30-second timeout, 1 MiB output bound and child-process cleanup. Use explicit REST authentication evidence for unknown CLI failures without exposing stderr.
- Update cache-source diagnostics, bilingual documentation and native-routing regression coverage.
- Pass 353 tests with 97.77% branch coverage, plus static checks. Read-only BMS queries succeeded for two vehicles using isolated copies of existing sessions.

## 2.0.0b10

- Show standard/AI range, last-trip distance, duration, start/end, speeds and model images by default.
- After a successful first refresh, enable exact legacy identities previously disabled by the integration. Preserve user-disabled choices, names and history; GPS, controls, events, estimation and raw diagnostics remain separately configured.
- Include the newly visible entities in demand-based travel scheduling without detail polling. Diagnostics report only migration counts.
- Pass 339 offline tests with 97.84% branch coverage.

## 2.0.0b9 — 2026-10-04

- Remove reviewed obsolete registry identities after successful first refresh;
  stop recreating legacy unavailable sensor placeholders, the nonfunctional Lock
  platform and unused full-range model input. Keep real lock binary state and
  valid current/estimated sensor identities. This supersedes placeholder retention.
- Scope removal to exact known keys on exclusively owned vehicle devices; keep
  unrelated/shared/ambiguous/unknown identities and preserve data on setup failure.
- Remove experimental UI names, synchronize English/Chinese translations and add
  function-specific button, range, trip, diagnostic and model-input icons.
- Export only a cleanup count in diagnostics; no SQL edits or production changes.
  Hardware dispatch remains unchanged pending the next control stage.

## 2.0.0b8 — 2026-10-04

- Keep HA image cache and timestamp until the normalized model-image URL changes.
  Review the public HTTPS origin, remove the observed opaque signature, verify TLS,
  reject redirects and avoid URL/exception-text logs. Unknown origins are unavailable.
- Use native GPS tracker exports and verify Zones plus coordinate opt-out through
  actual HA entity tests; preserve IDs and do not transform unverified coordinates.
- Poll groups from typed per-vehicle entity contexts and internal estimator/event
  dependencies. Preserve bootstrap, bounded BMS discovery, group backoff/freshness,
  profile discovery, manual refresh and on-demand historical actions.
- Document the isolated vehicles query and one anonymous unsigned image HEAD;
  preserve only a selected synthetic image fixture and non-sensitive source evidence.
- Resolve HA's documented validation-engine alias centrally, preserving old HA
  and modern Probatio schema behavior without a new dependency.
- Add the pinned HA 2026.10.0b0 beta to minimum/stable CI. No dependency/minimum-HA,
  entity ID, translation key or storage schema change; no production HA writes or
  real vehicle controls. Battery physical identity/permissions/CRS remain unverified.

## 2.0.0b7 — 2026-10-04

- Share one control policy between availability, pre/post-queue execution checks
  and privacy-safe diagnostics. Require explicit consent and verified evidence.
- Report support/permission separately, fixed blocking reasons, and backend
  endpoint support without exposing identifiers or free-form evidence labels.
- Reject duplicate capabilities, unknown actions, missing/blank evidence and
  unsupported backend controls. Current raw permissions remain unverified and
  fail closed; engine commands still do not implement lock/unlock.
- Clarify English/Chinese options and document the permission evidence contract.
  No real control tests, new cloud queries, entity identity or storage changes.

## 2.0.0b6 — 2026-10-04

- Keep legacy vehicle battery measurements unknown with multiple reported packs;
  retain their IDs and current sole-pack semantics after replacement.
- Match pack measurements only to explicitly identified rows, independent of
  ordering; separate real serials from anonymous slot placeholders and reject
  conflicting aliases instead of silently attaching another pack history.
- Use a typed, order-independent SOC observation signature. Upgrade matching old
  signatures with a fresh baseline, preserving model generation and totals.
- Add privacy-safe battery grouping diagnostics and centralized Child Device
  registry capability detection. Physical identity/composition remain unverified;
  no battery device migration, dependency upgrade or additional cloud requests.
- Document all observed BMS fields and the evidence required before child/physical
  device registration. Test transitions, registry continuity and compatibility.

## 2.0.0b5 — 2026-10-04

- Add a disabled-by-default Ride EventEntity for verified cloud travel end
  reports, with small translated attributes and no GPS/track/sample/raw data.
- Require confirmed travel ID provenance, past start/end and consistent positive
  duration. Startup/re-enable establish a baseline without historical replay.
- Persist bounded, per-entry hashed vehicle/ride cursors. Compare sets across
  reordering/month changes, suppress duplicates and old/ambiguous reports.
- Preflight the actual cursor envelope and verify on-disk acknowledgement after
  HA Store writes, before emission. Suspend events with a translated Repair on
  unsupported/corrupt storage or failed acknowledgement; other queries remain.
- Add isolated tests using the real atomic writer in disposable HA directories,
  including cancellation, silent write failure and native EventEntity restore.

Best-effort polling does not guarantee all rides. The 30-minute late window and
24-hour gap threshold are local policies, not measured vendor guarantees.
Crash/cancellation between persistence and emission can lose a notification.
No production HA writes, new cloud queries, real controls or identity changes.
HA minimum and ninecli pin remain unchanged.

## 2.0.0b4 — 2026-10-04

- Add device-scoped `get_trips` and `get_trip_detail` response-only actions,
  registered independently of loaded entries. No entity or control is required.
- Share bounded month/detail memory caches with polling; reuse newer action
  results without extending their actual success timestamps. Queries do not
  directly update current-month state or ride-event baselines.
- Validate account/device ownership, fresh vehicle presence and unique
  ride-to-detail association. Reject child components and ambiguous devices.
  Registry capability detection is centralized for old and new HA APIs.
- Limit detail fanout to five, page size to 100 and track points to 2000.
  Default responses omit GPS; explicit tracks require coordinates opt-in.
  Return normalized data only, with unknown cloud completeness and raw units.
- Add service selectors, English/Chinese descriptions, icons and automation
  examples. Reject control characters in ride IDs before normalization.

No production HA changes, new cloud requests or real controls in this phase.
Minimum HA 2026.1.0 and ninecli==0.1.7 remain unchanged. Location responses may
persist in automation traces; response data is not a privacy-free storage path.

## 2.0.0b3 — 2026-10-04

- Add five disabled-by-default last-ride sensors: duration (seconds), UTC start/end
  timestamps, server maximum speed and distance/duration average speed (km/h).
- Require known end-time ordering for new values; ambiguous/future end times stay
  unknown. Average speed is unknown if the reported duration conflicts with the
  observed time span. Legacy last-distance/raw-energy identities are unchanged.
- Add English/Chinese entity translations and icon translations. No cumulative
  statistics classes, track/state attributes, detail polling or control expansion.

Speed/distance use ninecli 0.1.7 display contracts; direct App UI cross-check is
pending. All five sensors are optional and retain independent new identities.
Minimum HA and exact dependency pin remain unchanged; no production HA changes.

## 2.0.0b2 — 2026-10-04

- Introduce a typed NinebotBackend contract and production NinecliBackend around
  the existing authenticated client; add the read-only travel-detail route.
- Add immutable Ride/SpeedSample/RideTrackPoint models and explicit ninecli 0.1.7
  parsing contracts. Overall average remains distance/duration; server maximum
  speed is not replaced by a sample mean/max or the unverified avg_speed field.
- Select timestamped last rides by time rather than server row order. Preserve
  the legacy last-returned snapshot if older payloads have no valid timestamps.
- Parse semicolon-separated trail rows on demand with bounds, truncation and
  invalid-point reporting. Preserve lon, lat and the third/fourth raw values;
  the fourth value is not a verified point-to-point distance. Point sample units
  and coordinate reference remain unknown, without conversion or invented times.
- Add sanitized nonempty-month and detail recorded fixtures. Real IDs, schedules
  and GPS were substituted; real duration/time relationships were checked before
  sanitizing. The cloud returned 20 rows while times reported 128: month
  completeness is not assumed and no hidden history scan was added.

No new entities/actions yet: these models support subsequent phases. No real
controls or production HA changes. Dependency and minimum HA are unchanged.

## 2.0.0b1 — 2026-10-04

- Add seven replayable sanitized historical business payloads with explicit
  provenance and synthetic location/identity substitutions. Nonempty trips and
  trip-detail fixtures remain unverified.
- Preserve decrypted business responses in a private memory-only RawStore with
  an 8 MiB retained-data budget, 128-record cap, eight-detail LRU/15-minute TTL,
  structural limits and unload cleanup. Secrets/personal profile fields are removed.
- Diagnostics include approved schema paths/types/counts, runtime versions and
  control gate outcomes; arbitrary field names, values and coordinates are excluded.
- Controls now require opt-in, allowlist, fresh successful observations and proven
  support/permission/semantics. Current opaque/null permission data fails closed.
- Preserve the existing Lock identity and observed state; lock/unlock requests
  return a translated error rather than assuming engine start/stop equivalence.

Behavior change: current production permission contracts are unverified, so bell,
seat-trunk and engine controls are unavailable even when options are enabled.
Read-only refresh is unaffected. No production HA changes or real controls were
performed. Dependency remains ninecli==0.1.7 and minimum HA remains 2026.1.0.

## 2.0.0b0 — 2026-10-03

Beta release: replaces the old OpenClaw query backend with pinned ninecli 0.1.7
through a managed, authenticated loopback child. Passwords are sent in request
bodies and are not retained in ConfigEntry or command-line arguments.

- Vehicle SOC, precise range, charging/power/lock state, BMS voltage/temperature,
  current-month distance, optional cloud ranges, location, image and diagnostics.
- Per-vehicle/group freshness, bounded serial requests, forced vehicle refresh,
  partial-failure isolation, local expiry/month notifications and process cleanup.
- Isolated session validation, same-account reauth/reconfigure, recoverable
  commits, rollback protection and Repairs for identity/storage/recovery issues.
- Conservative migration of both v1 layouts, preserving existing entity identities,
  names, disabled settings and history; no recorder SQL changes.
- Optional versioned SOC energy estimates with explicit nominal parameters and
  new entity identities. Old estimated counters are not reused as new measurements.

Breaking changes and limits: missing GSM/address/report-time and old estimate
sources remain legacy values; raw lock diagnostics keep 0=locked/1=unlocked.
Unsupported BMS cycles are unknown. `ec`/`charging_power` have unverified units
and remain optional unitless diagnostics. Coordinates and ride ordering/pagination
are unverified. Experimental controls are off by default, individually opt-in and
only mock-tested; they never retry automatically.

Requires HA 2026.1.0+ and a supported 64-bit ninecli wheel; ARMv7 is unsupported.
HA 2026.1.0/Python 3.13 and HA 2026.9.4/Python 3.14 are the test matrix.
Existing-session queries were verified; real new-password login/refresh recovery,
all hardware controls and other published platforms remain unverified.
Complete Go source/build modifications/reproducible builds are unavailable.

Before upgrading, back up HA configuration/storage/database and the old integration.
Code-only rollback cannot reverse a ConfigEntry schema upgrade: restore the matching
pre-upgrade HA backup and integration version. See the bilingual README,
[entity matrix](https://github.com/Wuty-zju/ha_ninebot/blob/9ba20bcadea1ea0500d9c080e60a48adc35e2387/docs/2.0-实体迁移矩阵.md) and [audit](https://github.com/Wuty-zju/ha_ninebot/blob/9ba20bcadea1ea0500d9c080e60a48adc35e2387/docs/2.0-预发布验收.md).
