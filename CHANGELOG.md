# Changelog

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

- 修复车辆在命令后消失、回读跳过时仍正常返回的缺陷；已完成的命令前manual任务不再代替命令后读取，旧任务清理不覆盖新任务。
- 非认证控制错误后最多协调一次status，始终保留原命令结果未知且不重发；认证/取消/卸载停止额外I/O。
- 新增仅内存、全局64条上限的最近控制结果诊断，分开记录接口接受、错误和回读；不保存响应、身份、位置或异常文本，不创建新实体。
- 中英回读错误说明同步；仅离线测试，无新云查询、真实车辆动作或生产HA修改。

## 2.0.0b15

- 按新的明确授权将本地发送条件与云端权限事实分开：控制启用、逐车允许、认证/新鲜度/错误/软件支持检查全部保留，未知权限由云端最终鉴权，已知拒绝和歧义仍阻止。
- 四类正常Button不再因parser没有权限证据而永久灰色；中英状态明确“可发送命令”，不宣称已获权限或物理动作完成，失败不自动重试、仍回读状态。
- 一辆车四种真实命令各尝试一次，均HTTP200/ok=true/空data，回读成功但选定状态未变化；生产会话文件未改动，临时目录/进程清理，物理效果待验证。
- 增加空成功响应的选定fixture与12条命令代理字段分类，独立于136条业务路径；移除测试注入假允许能力的依赖，保留明确拒绝/队列变化/用户禁用回归。

## 2.0.0b14

- status明确返回的sn必须匹配请求车辆，错车/非法身份不覆盖raw、telemetry或成功时间；缺失/null仍允许，partial failure与TTL保持。
- 新增instrumented临时binary的完整控制加密模拟测试：四种接受及一次拒绝，无真实云/车辆动作，原binary不变。
- 明确recon参考公钥/DeriveKey、opaque cmd、native成功与物理效果之间的边界，补MIT测试许可和当前字段用途。
- 376项测试通过（97.87%分支覆盖），Ruff/format/mypy通过；未知权限门禁不变。

## 2.0.0b13

- 新增可选“原始数据摘要”诊断：按车辆显示有界缓存记录数及四个整数指标，无原始值/任意字段名/位置，不增加云轮询。
- 汇总五个业务endpoint的136条已观察路径、122个非容器字段；逐项记录当前用途、单位、分类、来源和限制，保留历史清单。
- 明确status.sn尚未返回归属校验、车型语言fallback、CLI业务线路缓存及当前实体默认策略，避免文档冒称未实现能力。
- 增加recorded字段覆盖与隐私/车辆隔离/TTL/实际HA状态验证；369项测试通过（97.81%分支覆盖），静态检查通过。

## 2.0.0b12

- 新增正常命名的远程启动/关闭 Button，保留鸣笛/座桶身份，不重新引入 Lock。
- 已显式配置控制和车辆白名单时启用集成默认禁用的按钮；保留用户禁用，执行权限门禁不变。
- 新增默认显示、完整中英翻译的车辆控制状态枚举诊断，只有四个小型动作状态属性，不额外查询云端。
- 固定 native 二进制离线验证四种 REST 控制路径，纠正 REST 控制与 CLI/BMS 的缓存要求差异。
- 366 项测试通过（97.80% 分支覆盖）；Ruff/format/mypy通过，无生产写入/真实控制。

## 2.0.0b11

- 修复 ninecli 0.1.7 REST 列表不建立车辆缓存导致 BMS `no_cache` 的问题：使用原生 JSON 车辆发现生成业务线路缓存，再按需启动受鉴权 REST。
- 共用串行锁/8项队列、30秒超时、1MiB输出限额和子进程回收；未知 CLI 错误使用 REST 明确鉴权证据，不泄漏 stderr。
- 更新缓存数据来源诊断、双语文档及 native 实际路由回归。
- 353 项测试通过（97.77% 分支覆盖）；新版 client 在生产会话隔离副本上只读验证两车 BMS 成功，生产文件未改动，实车控制为零。

## 2.0.0b10

- 默认显示普通/AI续航、最近行程距离/时长/起止时间/速度及车型图片。
- 成功首刷后按精确身份启用旧的 integration-disabled 默认，保留用户禁用、名称和历史身份；不启用 GPS、控制、事件、估算或 raw 诊断。
- 新默认纳入按需求行程调度，不增加详情轮询；诊断仅记录迁移数量。
- 339 项离线测试通过（97.84% 分支覆盖）；不包含真实控制或生产 HA 修改。

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
- Parse confirmed semicolon-separated lon,lat,speed,distFromPrev trails on demand
  with bounds, truncation and invalid-point reporting. Point speed/delta units
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
