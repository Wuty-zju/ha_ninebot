# Roadmap / 开发方向

[Documentation](README.md) · [Development and validation](development.md) · [Release history](../CHANGELOG.md)

| Channel | Delivered scope |
|---|---|
| **v2.2.0 stable** | Native Home Assistant entities, controls, an account-owned ride archive and optional Assistant. [Release](https://github.com/Wuty-zju/ha_ninebot/releases/tag/v2.2.0) |
| **v2.2.18 preview** | Compare selected rides by Wh and Wh/km, open their authorized map, receive a ride receipt and create a monthly postcard. [Assistant](assistant.md#compare-saved-rides) · [Ride archive](history.md#ride-keepsakes) |

正式版 **v2.2.0** 包含原生实体、车控、账号档案和可选 Assistant。**v2.2.18 预览版**将助手的行程比较、同图回放、骑后小票和收藏月报接入现有档案。

<a id="added-in-200rc4"></a>
<a id="available-in-200rc1"></a>
<a id="added-in-200rc3"></a>
<a id="added-in-200rc2"></a>

## Available in 2.0.0

The 2.0 cycle established isolated account sessions, stable vehicle and entity identities, adaptive per-vehicle updates, single-attempt controls with confirmation, and native ride summaries and events. Resumable history scans, schema 7 archive protection, scoped exports and bounded integrity recovery laid the foundation for local maps and statistics. See the [2.0 timeline](releases/2.0.md).

2.0 建立了独立账号会话、实体身份、逐车自适应刷新、单次发令与回读确认、行程统计和事件。可续接同步、schema 7 档案、按范围导出及有界恢复是本地地图与统计的基础。[2.0 开发回顾](releases/2.0.md#简体中文)

<a id="optional-assistant--21-preview"></a>

## Ninebot Assistant in 2.2

- **v2.2.0 stable** ships optional read tools, versioned bilingual help, and separately enabled, human-reviewed history tasks.
- **v2.2.9–v2.2.11 previews** unify the native catalog at `/api/mcp/ninebot`, configure reads and task access per vehicle, distinguish same-name vehicles, and preserve the business date across a turn.
- **v2.2.12 preview** checks only the requested statistic under the selected vehicle's statistics grant. A verified result, including zero, ends the plan. An unresolved result can use the SK05 public checklist and may inspect synchronization health when the same vehicle grants that read. [Assistant guide](assistant.md)

2.2 正式版提供可选读取工具、双语版本化帮助及独立开关控制的人工审核任务。近期预览版统一 MCP 入口并逐车授权，支持区分同名车辆及固定单轮上海业务日期。v2.2.12 只核对所请求的统计项，已核验的零值也能直接结束排查。缺口可查 SK05 公开帮助，读取同步健康前须核对此车的对应权限。[助手指南](assistant_zh.md)

## Early alpha: visualization and cards

The stock HA **Map** card displays a tracker and its Recorder observations. The **Ninebot trip card** uses the shared HA native map component to display archive polylines and saved-point playback, with a bundled fallback on older frontends. The preview compares up to five authorized rides on one map. In **v2.2.12**, the table compares the current ride and selected totals, with per-metric coverage. Keeping only the current ride preserves playback. [Map setup and replay](history.md#add-the-history-map)

HA 自带的**地图**卡片读取定位实体与 Recorder 中的观察记录。随包的 **Ninebot 行程卡片**使用同一套 HA 原生地图组件绘制档案路线及采样点，旧前端保留随包绘图能力。v2.2.12 在一张地图上对比最多五趟已授权行程，展示当前行程、所选合计和缺失指标的覆盖数。仅保留当前行程会保留已播放位置。[地图设置与回放](history_zh.md#添加历史地图)

## Next candidates

| Phase | Work | Acceptance gate |
|---|---|---|
| 1. Record behavior | Reuse small synthetic fixtures for multi-vehicle archive reads, overlapping routes, statistics coverage, task grants and reload. | Identify the first differing observation and record the base SHA, input, actor and expected result before changing shared code. |
| 2. Refine map boundaries | Keep selection, replay state, archive access and rendering independently testable. Keep the default dashboard integration behavior and use the same view state for the native and fallback renderers. | On one synthetic set, compare HA native map and fallback behavior for focus, narrow layouts, keyboard/screen reader semantics, touch, endpoint-only tracks, revoke/disconnect and request counts. Verify that keeping one ride retains pointer, zoom and play state. Validate spoken feedback on a physical iPhone separately. |
| 3. Refine Assistant boundaries | Use the existing tool catalog and deterministic local plans. Change queries, guidance or presentation at the boundary where a measured failure occurs. | Run the same cases through the direct tool, native host and Action. Include same-name vehicle selection, another vehicle's grants, midnight boundary, verified zero, pending/partial coverage and missing sync-health permission. Inspect actual tool calls separately from host prose. |
| 4. Snow Leopard maintenance | Trace one regression at a time through auth/recovery, polling, storage, control, archive import or statistics. Prioritize duplicate month reads, restart recovery and protecting detailed geometry. | Compare before/after inputs and outputs, wire/SQL counts and timings where relevant. Preserve account identity, checkpoint/freshness semantics, rollback and better tracks. Keep behavior-preserving cleanup distinct from a verified fix. |
| 5. Maintain the archive reader | Exercise account changes, stale pages and GPS permission withdrawal against the [local archive panel](history.md#local-archive-replay). | Only extend the interface when a recorded case shows a missing workflow. Keep archive reads paged and scoped to the signed-in user. |

1. 建立可复用的合成场景，固定基线、输入、操作身份及验收结果。
2. 在 HA 原生地图组件与随包绘图路径复核选择、视角、键盘、触摸、简化轨迹和归档请求数。实体 iPhone 上的读屏实际播报单独验证。
3. 用原生工具、宿主及 Action 验证同名选择、逐车授权、跨午夜、零值、覆盖不足及同步健康未授权，逐项对照工具回执与宿主原文。
4. 雪豹式维护从具体反例切入，检查次数、耗时、重启恢复、归属与历史几何质量，先量测再修改。
5. 复用双账号跨月样例，继续核对只读面板在授权变化、后台恢复和分页期间的清理与恢复。

Each change integrates through a short branch from `dev`. Publish a numeric preview when a validated stage is usable. Stable promotion uses a separately reviewed candidate. [Branch and release channels](development.md#branch-and-release-channels)

## Research, not committed features

Point timestamps, speed units and coordinate systems require reviewed source samples before a clock-accurate replay or calibrated legend. Evaluate archive indexing against representative workloads, and require an explicit migration with duplicate-series and rollback checks for account-stable Recorder statistics IDs. New vehicle fields need verified units and stable entity semantics. A replacement protocol backend needs comparable behavior, auditable sources and a maintenance case.

真实时钟回放、轨迹速度图例以及更多车辆字段需补充可核验的源数据。归档索引以代表性负载评估，统计序列身份迁移须核对历史连续性、重复序列和回退。替代协议实现须具备可审核来源、等价行为验证和维护依据。
