# Roadmap / 开发方向

[Documentation](README.md) · [Releases](https://github.com/Wuty-zju/ha_ninebot/releases) · [2.0 development history](releases/2.0.md)

Current scope for the stable 2.0 series and optional 2.1 preview. Usage and data contracts are maintained in the guides; beta and RC milestones belong in the development history.

<a id="added-in-200rc4"></a>
<a id="available-in-200rc1"></a>
<a id="added-in-200rc3"></a>
<a id="added-in-200rc2"></a>

## Available in 2.0.0

| Area | Delivered capability |
|---|---|
| Accounts and entities | Isolated login/reauth, stable account/vehicle identity, translated native entities and configurable battery specifications. |
| Live updates and controls | Five adaptive profiles, shared reads, bounded activity boosts, discovery recovery, native vehicle/seat locks and replaceable confirmation observers. |
| Ride summaries | Month/today/yesterday/latest periods, daily-index repair, semantic events, optional calendars and historical query/statistics Actions. |
| Historical acquisition | Resumable month/page discovery, missing-detail updates, fair budgets, checkpoints and independent coverage scopes. |
| Archive and recovery | Account-owned schema 7 SQLite, source evidence, better-track protection, same-account reconnection, scoped export/import, backup hooks and bounded integrity repair. |
| Runtime reliability | Cancellation-safe resource ownership, acknowledged parameter writes, independent cleanup and reused local projections. |

2.0 已交付独立账号与原生实体、五档自适应更新、控制确认、日／月／最近行程统计、事件与可选日历、可续接的历史同步、账号数据库、导入导出及有界校验恢复。正式版延续 rc4 的业务行为；完整 beta／RC 演进见[开发回顾](releases/2.0.md#简体中文)。

## Optional Assistant — 2.1 preview

Six dedicated local tools expose authorized vehicle status, statistics, saved trips and synchronization health through the native LLM API/MCP Server. The feature is off by default, uses separate account/vehicle consent and keeps coordinates and raw responses outside tool results. [Setup and tool reference](assistant.md).

Versioned bilingual help and bounded task checklists are available from 2.1.1. From 2.1.2, a separate task option supports local proposals, human review, exact request budgets and resumable execution through the existing history scheduler. [Task workflow](assistant.md#reviewable-history-tasks-beta).

Next steps are a measured single-agent query/diagnostic baseline and retrieval comparisons. The evaluated CPU host has not passed conversation acceptance. Multi-agent work remains an experiment subject to a measured benefit over the single-agent baseline.

2.1预览提供六个专属本地只读工具，默认关闭，按账号和车辆单独授权。[使用说明](assistant_zh.md)。2.1.1已提供双语版本帮助和有界任务清单；2.1.2起，独立任务选项支持本地申请、人工审核、精确请求预算和既有调度器中的执行恢复。[任务流程](assistant_zh.md#可审核的历史任务beta)。后续继续验收单Agent查询／诊断及检索对照，已评测CPU宿主尚未通过对话验收；多Agent实验有实测收益才保留。

## Early alpha: visualization and cards

Map visualization, UI interactions and custom cards are **early alpha**. Next work centers on a shared vehicle/date/ride selection, linked map/list/summary/chart views and clearer synchronization feedback. The interaction model may change as these workflows mature.

地图可视化、UI 交互和自定义卡片为**早期 alpha**。后续围绕共享车辆／日期／行程选择、地图／列表／摘要／图表联动及同步反馈完善交互，界面结构会随之调整。

## Next candidates

| Direction | Value | Gate |
|---|---|---|
| More intuitive history workspace | Clearer coverage/sync feedback; optional linked map/list/summary/chart cards | Shared selection and permissions without duplicate cloud reads or private-cache leaks. |
| More vehicle and battery fields | Useful native HA representation across models/packs | Reviewed fixtures, units/enums, stable identity and meaningful state semantics. |
| Archive/read efficiency | Migration progress, faster startup, clearer whole-archive/baseline coverage and bounded reads | Reproducible measurements; preserve integrity, better geometry and rollback. |
| Statistics continuity | Account-owned external statistic identity across removal/re-addition | Explicit metadata migration, duplicate-series checks and historical continuity. |
| Documentation and distribution | English/Chinese onboarding, HACS readiness and complete package-count aggregation | Accurate support statements, attribution, paginated asset counts and explicit submission approval. |

后续按收益和证据推进历史交互、已取证字段、启动／存储效率、外部统计身份连续性与分发维护；每项独立评估，保持实体身份和历史完整性，避免重复请求。

## Research, not committed features

Spatial indexing/geometry levels of detail, richer verified point metrics and an independently auditable replacement protocol backend may be evaluated when evidence justifies maintenance cost. Energy recovery, unknown sample units/CRS, cloud retention boundaries and model-specific telemetry require evidence. Synthetic estimates will not be presented as confirmed official measurements.

研究项：空间索引／分级几何、可验证的逐点指标和可替换协议实现；先证据后模型，保留未知单位／坐标、回收里程和经验估计的来源与不确定性。
