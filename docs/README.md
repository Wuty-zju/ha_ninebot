<a id="documentation"></a>

# Ninebot documentation / 文档入口

[English overview](../README.md) · [中文首页](../README_zh.md)

| Release | Where to start |
|---|---|
| **v2.2.0 stable** | [Install the stable package](https://github.com/Wuty-zju/ha_ninebot/releases/tag/v2.2.0) · [English handbook](https://github.com/Wuty-zju/ha_ninebot/blob/v2.2.0/docs/README.md) |
| **v2.2.12 preview** | [Release notes and package](https://github.com/Wuty-zju/ha_ninebot/releases/tag/v2.2.12) · [Compare saved rides](history.md#replay-saved-routes) · [Read statistics evidence](assistant.md#read-the-evidence) |

Use the guides below for the current handbook. Stable users can open the matching tagged guide above. 正式版用户可查阅[随版中文安装指南](https://github.com/Wuty-zju/ha_ninebot/blob/v2.2.0/docs/guide_zh.md)。预览版用户可直接查看下表。

| I want to… | English | 简体中文 |
|---|---|---|
| Install Ninebot, add a vehicle or diagnose a failed request | [Installation and everyday use](guide.md) | [安装与日常使用](guide_zh.md) |
| Save rides, set up a map or read archive coverage | [Ride history, maps and statistics](history.md) | [历史行程、地图与统计](history_zh.md) |
| Set up Assist or MCP and read tool evidence | [Ninebot Assistant](assistant.md) | [Assistant 使用指南](assistant_zh.md) |
| Switch versions, check entity changes or restore a backup | [Upgrades, entity identity and rollback](migration.md) | [升级、实体身份与回滚](migration_zh.md) |
| Trace data, ownership or authorization | [Architecture and data contracts](architecture.md) | 英文正文含中文摘要 |
| Patch, test or package the integration | [Development and validation](development.md) | 英文正文含中文摘要 |
| Look up the 2.0 beta and RC milestones | [2.0 development history](releases/2.0.md) | [2.0 开发回顾](releases/2.0.md#简体中文) |
| Review upcoming work and its acceptance criteria | [Roadmap](roadmap.md) | [开发方向](roadmap.md) |

[Contributor workflow](../CONTRIBUTING.md) · [Agent entry point](../AGENTS.md) · [Security and privacy reporting](../SECURITY.md) · [Synthetic asset provenance](assets/manifest.json)

## Earlier documentation links

Older bookmarks open the matching topic below. To inspect an intermediate beta decision, see the [archived b94 guide](https://github.com/Wuty-zju/ha_ninebot/blob/fa3a7ec04dacc7f791bfd79d8ee469538ac95878/docs/README.md).

<details>
<summary>Find an earlier section by name</summary>

| Previous section | Current home |
|---|---|
| <a id="ninebot-integration-behavior"></a>Ninebot integration behavior | [Read current contract](history.md) |
| <a id="authentication"></a>Authentication | [Read current contract](guide.md) |
| <a id="scheduling-and-retained-data"></a>Scheduling and retained data | [Read current contract](architecture.md#realtime-scheduling-and-controls) |
| <a id="entities"></a>Entities | [Read current contract](guide.md) |
| <a id="history"></a>History | [Read current contract](history.md) |
| <a id="controls"></a>Controls | [Read current contract](guide.md#controls-and-confirmation) |
| <a id="development"></a>Development | [Read current contract](development.md) |
| <a id="period-statistics-action"></a>Period statistics Action | [Read current contract](history.md#coverage-and-statistics) |
| <a id="ride-calendar-and-offline-last-ride"></a>Ride calendar and offline last ride | [Read current contract](history.md#calendar-and-events) |
| <a id="native-historical-trend-graphs"></a>Native historical trend graphs | [Read current contract](history.md#coverage-and-statistics) |
| <a id="canonical-entity-ids-and-upgrade"></a>Canonical entity IDs and upgrade | [Read current contract](migration.md) |
| <a id="local-archive-browsing"></a>Local archive browsing | [Read current contract](history.md) |
| <a id="optional-ride-detail-card"></a>Optional ride detail card | [Read current contract](history.md#add-the-history-map) |
| <a id="b44-display-and-naming-update"></a>b44 display and naming update | [Read current contract](migration.md) |
| <a id="b45-calendar-battery-and-ride-presentation"></a>b45 calendar, battery and ride presentation | [Read current contract](guide.md) |
| <a id="native-location-history-map"></a>Native location history map | [Read current contract](history.md) |
| <a id="b46-service-classification-and-ride-hours"></a>b46 service classification and ride hours | [Read current contract](guide.md) |
| <a id="b47-ride-hour-precision"></a>b47 ride hour precision | [Read current contract](guide.md) |
| <a id="b49-tracker-battery-compatibility"></a>b49 tracker battery compatibility | [Read current contract](guide.md) |
| <a id="b50-accumulated-daily-statistics"></a>b50 accumulated daily statistics | [Read current contract](history.md#coverage-and-statistics) |
| <a id="historical-track-quality-b51"></a>Historical track quality (b51) | [Read current contract](history.md) |
| <a id="private-detail-retention-b52"></a>Private detail retention (b52) | [Read current contract](history.md) |
| <a id="local-first-detail-queries-b53"></a>Local-first detail queries (b53) | [Read current contract](history.md) |
| <a id="known-ride-background-preservation-b54-h4-first-step"></a>Known-ride background preservation (b54, H4 first step) | [Read current contract](history.md#enable-preservation-and-synchronization) |
| <a id="durable-month-continuation-b55-h4-in-progress"></a>Durable month continuation (b55, H4 in progress) | [Read current contract](history.md#enable-preservation-and-synchronization) |
| <a id="sparse-month-repair-and-late-arrivals-b56"></a>Sparse month repair and late arrivals (b56) | [Read current contract](history.md#enable-preservation-and-synchronization) |
| <a id="recorded-detail-mapping-and-saved-route-availability-b58"></a>Recorded detail mapping and saved-route availability (b58) | [Read current contract](history.md) |
| <a id="history-page-protocol-foundation-b59-m0"></a>History page protocol foundation (b59, M0) | [Read current contract](history.md#enable-preservation-and-synchronization) |
| <a id="incremental-history-and-synchronization-controls"></a>Incremental history and synchronization controls | [Read current contract](history.md#enable-preservation-and-synchronization) |
| <a id="history-synchronization-progress-and-storage-options"></a>History synchronization progress and storage options | [Read current contract](history.md#enable-preservation-and-synchronization) |
| <a id="local-payload-verification"></a>Local payload verification | [Read current contract](history.md#verification-and-recovery) |
| <a id="partially-damaged-local-detail-evidence"></a>Partially damaged local detail evidence | [Read current contract](history.md#verification-and-recovery) |
| <a id="scoped-object-receipts-and-private-quarantine-schema5"></a>Scoped object receipts and private quarantine (schema5) | [Read current contract](history.md#verification-and-recovery) |
| <a id="recent-first-card-navigation-b90"></a>Recent-first card navigation (b90) | [Read current contract](history.md#add-the-history-map) |
| <a id="linked-map-workbench-b91"></a>Linked map workbench (b91) | [Read current contract](history.md#add-the-history-map) |
| <a id="scoped-local-continuation-versions-b92"></a>Scoped local continuation versions (b92) | [Read current contract](history.md#enable-preservation-and-synchronization) |
| <a id="local-update-notifications-and-in-flight-sharing-b93"></a>Local update notifications and in-flight sharing (b93) | [Read current contract](history.md) |
| <a id="synthetic-archive-profiling"></a>Synthetic archive profiling | [Read current contract](development.md) |

</details>
