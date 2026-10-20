# Upgrades, entity identity and rollback

[简体中文](migration_zh.md) · [Documentation](README.md)

## Before upgrading

Back up the **complete matching Home Assistant configuration/storage**: registries, ConfigEntries, account sessions, identity/nominal-parameter stores and private archives/recovery copies. Protect these files as credentials and location history. A code-only downgrade or SQLite-only copy does not reverse entity or storage migration.

Install one complete selected release and follow HA/HACS restart guidance. Reload the browser for bundled card updates. Existing `custom:ninebot-trip-card` configurations and resource path remain supported. Do not delete/recreate the integration as a normal upgrade step.

## From 1.x to 2.0

2.0 replaces the earlier backend and reorganizes authentication, snapshots, scheduling and historical storage. Reauthentication may be necessary. Meaningful legacy identities and reviewed aliases are migrated rather than blindly recreated. Vendor API availability and dependency/platform limits remain relevant.

For the original transition table, see the [fixed historical migration matrix](https://github.com/Wuty-zju/ha_ninebot/blob/9ba20bcadea1ea0500d9c080e60a48adc35e2387/docs/2.0-实体迁移矩阵.md). It is historical evidence, not a second current manual.

## Generated IDs and names

New entities use:

```text
<platform>.<account_slug>_<vehicle_model_slug>_<serial_slug>_<stable_key>
```

ASCII model tokens are preferred over translated nicknames. Lossy/non-ASCII/long inputs get deterministic hash suffixes; occupied targets do not get arbitrary `_2` names. Frozen naming seeds prevent later nickname/account-label/model changes from renaming IDs. Display names remain translated and separate from identity.

b36 introduced conservative generated-ID migration. **b44 then performed a one-time canonical migration including prior custom IDs** for loaded vehicles. Later user renames are not overwritten on every reload. Preserve this distinction when comparing old release notes.

Meaningful unique IDs and user metadata remain protected. Exclusive ownership, conflict detection, a durable identity journal and HA's registry APIs guard renames. Occupied targets, foreign ownership and concurrent user changes create Repairs rather than deletion/overwrite. An invalid established identity store can block setup to avoid duplicate identities.

## Inspect the migration mapping

In Developer Tools → Actions, or in a response-capable script:

```yaml
action: ninebot.get_entity_migration
data:
  device_id: REPLACE_WITH_VEHICLE_DEVICE_ID
response_variable: mapping
```

The response contains the prefix and old/new entity-ID mappings with status/reason. It is local-only and contains private identity information; do not publish it. Update YAML, templates, automations and external dashboards referring to renamed IDs. Automatic rewriting of every reference is not promised.

## Vehicle and seat lock conversion

b38 converted reviewed old vehicle-lock/unlocked binary sensors and seat-lock sensors to native Lock entities. Unique-ID strings and applicable custom names/icons/areas/labels/aliases/disabled/hidden choices are retained where safely owned. A different platform needs a different entity-registry row.

Old Recorder rows remain under the old entity ID; new Lock history begins separately. Change automation conditions from `on`/`off` to `locked`/`unlocked` as appropriate. Same-domain renames use HA's history/statistic metadata handling, but unrelated existing histories are not merged.

## Deliberately retired entities

Low-confidence plugin-generated SOC cumulative estimates and reviewed duplicates remain retired. Nominal V/Ah and rated energy describe a battery specification, not an estimate of measured capacity/SOH. The three cloud range sensors are separate current fields; their missing values do not fall back to another range algorithm.

Historical manual-job entities were retired separately from the current automatic-history progress sensors. Old beta statements about having no progress entities should not be used to remove the current sensors. No Recorder rows are deleted to clean up presentation.

## Archive schema and rollback

Archive migrations validate the source, check space and create an exclusive consistent `rides.schema<old>-<digest>.sqlite3` recovery copy before transactional schema changes. Source evidence and checkpoints are retained inside the account archive. After validated consolidation, inactive legacy files and schema safety copies are removed. Keep an external/full HA backup for rollback.

Older versions may not read the upgraded schema or identity journal. Restore the matching full pre-upgrade backup in a deliberate recovery environment with the integration stopped. Keep the only recovery original intact; an older copy lacks data acquired after it was created. Do not overwrite a live archive, lower its schema marker manually or delete it to force setup.

Use recovery-job copies such as `.recovery-<job_id>.sqlite3` through the supported [verification and recovery](history.md#verification-and-recovery) workflow. Keep the matching full HA backup for version rollback and retain unresolved Repairs for diagnosis.


## History controls and debug entities

Sync trip history now also resumes paused work. The old Resume history sync button retires; update automations using it to the existing sync button, or retain the compatible `ninebot.sync_history` Action with `operation: resume`. Pause remains available. Saved rides, other entity identities and user names are retained.

With debug off, related diagnostics are integration-disabled and hidden; enabling debug restores the same identities without overriding user-disabled/hidden choices. The default request budget becomes 60 and its maximum becomes 120; explicit valid preferences are retained.

## Trip events and duration display in b101

The two standalone latest-trip start/end sensors are retired into the existing Vehicle trip event. Update dashboards/templates to its `latest_start_time` and `latest_end_time` attributes; archived rides and old Recorder rows are not deleted. Vehicle events now use concrete event types instead of `state_changed`; update automation filters. Event IDs and other trip sensor identities remain. Duration display is migrated once to hours/four decimals. Existing calendar/history options remain as selected; only new accounts default these Beta features off.

## Account-owned archive directories

History uses a stable authenticated-account directory. Schema 7 consolidates matching entry databases, previous `imports/` and `retired/` copies, rollback files and supported schema snapshots. Each source is upgraded on an isolated copy, validated and committed before its managed file is removed. Distinct business originals are preserved in the canonical database; unrelated accounts, unknown names, symlinks, changed/in-use files and failed sources are not deleted. A failed source does not hide a healthy account archive; a Repair identifies incomplete cleanup.

Back up Home Assistant **before upgrading**. Successful consolidation removes the old local database copies; downgrade requires a matching external/full HA backup. Same-account re-addition reconnects its canonical archive, and entity identities are unchanged. Do not copy files over a running database or manually change schema markers.
