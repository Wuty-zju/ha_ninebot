# Roadmap / 开发方向

[Documentation](README.md) · [Releases](https://github.com/Wuty-zju/ha_ninebot/releases)

Current scope and next priorities for the 2.0 series. Usage and data contracts are maintained in the guides.

## Available in 2.0.0rc1

- Account-isolated login/reauth, demand-aware realtime data, native vehicle/seat locks with bounded readback.
- Vehicle/battery/range/location entities, calendars, trip/vehicle events and historical query/statistics Actions.
- Resumable first discovery and incremental history, private SQLite evidence, quality-preserving tracks, quotas, verification and supported local recovery.
- Account-owned archives, same-account reconnection, validated multi-account/vehicle export and import, schema 7 source consolidation.
- Scoped local continuation versions, in-flight read sharing, discovery recovery and day-boundary statistics retention.

已具备：多账号、实时实体/锁反馈、日历/统计/查询、可续接的历史行程/详情/精细GPS同步与私有归档、较好轨迹保护及有界校验恢复。

## Added in 2.0.0rc3

- Cancellation-safe import/migration cleanup and explicit removed-entry credential ownership.
- Consistent rate-limit policy and isolated backup cleanup across accounts.
- Shared daily statistics projections and single-pass month restoration.
- Public import projection notifications and explicit broker-owned platform concurrency.
- Full-validation provenance, matching-input promotion reuse and shared-ancestor CI selection.

rc3完善导入／迁移取消清理、账号凭据回收、限流及跨账号备份收尾；共享日投影、减少重复月解析，并明确导入通知和平台并发边界。发布核验完整检查来源与输入摘要，提升时复用有效结果。外部统计 ID 的兼容迁移保留为独立后续设计。


## Added in 2.0.0rc2

- Independent monotonic status, battery, travel and profile scheduling, with per-group freshness and retry deadlines.
- Five native update-speed profiles, bounded charging/activity/stop boosts and shared periodic admission. Custom intervals stay until explicitly changed.
- Polling-only configuration updates keep the running session and archive; unchanged saves avoid a reload.
- Cancellation-safe export resources, completed archive-worker shutdown, immediate discovery publication and synchronous explicit refresh completion.
- Centralized CI selection/pins and one candidate documentation gate, with focused checks during development.

rc2新增：逐车逐组单调时钟调度、五档原生配置、有界场景加速、旧值保留及配置差异热更新；完善导出／线程清理和发现／手动刷新边界，统一选测和发布门槛。

## Early alpha: visualization and cards

Map visualization, UI interactions and custom cards are **early alpha**. Next work centers on a shared vehicle/date/ride selection, linked map/list/summary/chart views and clearer synchronization feedback. The interaction model may change as these workflows mature.

地图可视化、UI 交互和自定义卡片为**早期 alpha**。后续围绕共享车辆／日期／行程选择、地图／列表／摘要／图表联动及同步反馈完善交互，界面结构会随之调整。

## Next candidates

| Direction | Value | Gate |
|---|---|---|
| More intuitive history workspace | Clearer coverage/sync feedback; optional linked map/list/summary/chart cards | Shared selection/permissions without duplicate cloud reads or private-cache leaks. |
| More vehicle and battery fields | Useful native HA representation across models/packs | Reviewed fixtures, units/enums, stable identity and meaningful state semantics. |
| Archive/read efficiency | Migration progress, faster startup, clearer whole-archive/baseline coverage and bounded reads | Reproducible measurements; preserve integrity, better geometry and rollback. |
| Documentation and distribution | English/Chinese onboarding, documented data contracts, HACS readiness | Accurate support matrix, attribution, valid packaging and explicit submission approval. |

下一步候选：更直观的历史/同步反馈、按需拆分联动卡片、更多已取证实体/车型/电池能力、基于实测的存储读取优化，以及双语说明/HACS 准备。避免重复请求和实体身份变化。

## Research, not committed features

Spatial indexing/geometry levels of detail, richer verified point metrics and an independently auditable replacement protocol backend may be evaluated when evidence justifies maintenance cost. Energy recovery, unknown sample units/CRS, cloud retention boundaries and model-specific telemetry require evidence. Synthetic estimates will not be presented as confirmed official measurements.

研究项：空间索引/分级几何、可验证的逐点指标和可替换协议实现；先证据后模型，不把未知单位/坐标、回收里程或经验估计包装成官方事实。
