# Security and privacy

## Reporting a concern

Use GitHub’s private reporting option under **Security → Advisories** for a suspected security issue. If private reporting is unavailable, request a private contact before sharing technical details. For a regular support issue, include the installed versions, one occurrence and the relevant redacted diagnostic. [Security advisories](https://github.com/Wuty-zju/ha_ninebot/security/advisories) · [Issue tracker](https://github.com/Wuty-zju/ha_ninebot/issues)

If a credential has been exposed, replace or revoke it with its provider. Review attachments and automation traces for account identifiers, secrets and precise routes before sharing them.

## Trust boundaries

| Data or operation | Who can receive it |
|---|---|
| Login and live queries | The account’s managed backend uses an authenticated loopback connection. Vehicle commands require current ownership and the configured control selection. |
| Local history and exports | SQLite archives, unencrypted exports and HA backups can contain exact routes. Transfer them privately. |
| History map | The signed-in frontend requires vehicle access and location permission for geometry. The configured external tile service can learn which area was viewed. |
| Assistant queries | The chosen model host can receive authorized vehicle labels, dates, scalar values and ride references. Each tool checks the caller, vehicle, operation and current grant. Exact GPS, credentials, full logs and raw responses are excluded. |
| Background sync applications | The requester reviews a bounded range and budget in HA. Approval, queued work and verified saved results are separate stages. |
| Diagnostics | The public support surface is a reviewed projection of schema and error metadata. Inspect any additional log excerpt before attaching it. |

[Account backup and removal](docs/guide.md#privacy-backup-and-removal) · [Map privacy](docs/history.md#map-privacy-and-units) · [Assistant access](docs/assistant.md) · [Third-party notices](NOTICE)

简体中文：安全问题优先走仓库的私密反馈渠道。普通故障可附版本、一次复现与脱敏诊断。档案导出未加密，可能含精确轨迹。模型可读取的车辆数据通过逐车权限设置。[隐私、备份与移除](docs/guide_zh.md#隐私备份与移除) · [助手授权](docs/assistant_zh.md)
