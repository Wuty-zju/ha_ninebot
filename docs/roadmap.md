# Roadmap / 开发方向

[Documentation](README.md) · [Releases](https://github.com/Wuty-zju/ha_ninebot/releases)

These are directions, not delivery dates or guarantees of vendor API availability. Implemented behavior is in the guides; speculative fields are not supported data.

## Available in 2.0 prereleases

- Account-isolated login/reauth, demand-aware realtime data, native vehicle/seat locks with bounded readback.
- Vehicle/battery/range/location entities, calendars, completion events and historical query/statistics Actions.
- Resumable first discovery and incremental history, private SQLite evidence, quality-preserving tracks, quotas, verification and supported local recovery.
- Scoped local continuation versions, in-flight read sharing and measured per-read parsing reuse.

已具备：多账号、实时实体/锁反馈、日历/统计/查询、可续接的历史行程/详情/精细GPS同步与私有归档、较好轨迹保护及有界校验恢复。

## Early alpha: visualization and cards

Map-based visualization, UI interactions and custom cards are experimental **early alpha**. Existing code is an evolving prototype, not a stable showcase or demo. It may be substantially redesigned, changed or withdrawn at any time; future work can rebuild the interaction architecture rather than preserve its current appearance.

地图可视化、UI交互和自定义卡片为**早期alpha**。现有代码是持续演进的原型，不是稳定展示或demo；后续可大幅完善、重构、修改或随时撤回，不承诺维持当前界面。

## Next candidates

| Direction | Value | Gate |
|---|---|---|
| More intuitive history workspace | Clearer coverage/sync feedback; optional linked map/list/summary/chart cards | Shared selection/permissions without duplicate cloud reads or private-cache leaks. |
| More vehicle and battery fields | Useful native HA representation across models/packs | Reviewed fixtures, units/enums, stable identity and meaningful state semantics. |
| Archive/read efficiency | Faster startup/larger histories with bounded memory | Reproducible measurements; preserve integrity, better geometry and rollback. |
| Documentation and distribution | English/Chinese onboarding, documented data contracts, HACS readiness | Accurate support matrix, attribution, valid packaging and explicit submission approval. |

下一步候选：更直观的历史/同步反馈、按需拆分联动卡片、更多已取证实体/车型/电池能力、基于实测的存储读取优化，以及双语说明/HACS 准备。避免重复请求和实体身份变化。

## Research, not committed features

Spatial indexing/geometry levels of detail, richer verified point metrics and an independently auditable replacement protocol backend may be evaluated when evidence justifies maintenance cost. Energy recovery, unknown sample units/CRS, cloud retention boundaries and model-specific telemetry require evidence. Synthetic estimates will not be presented as confirmed official measurements.

研究项：空间索引/分级几何、可验证的逐点指标和可替换协议实现；先证据后模型，不把未知单位/坐标、回收里程或经验估计包装成官方事实。
