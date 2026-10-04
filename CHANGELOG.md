# Changelog

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
[entity matrix](docs/2.0-实体迁移矩阵.md) and [audit](docs/2.0-预发布验收.md).
