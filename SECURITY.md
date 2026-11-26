# Security and privacy

## Reporting a concern

Check the repository’s **Security → Advisories** page for private reporting. If the private reporting option is unavailable, ask for a private contact through GitHub and share the details there. Ordinary integration bugs belong in Issues. Include the installed version, one reproduction and the smallest relevant redacted diagnostic. [Security advisories](https://github.com/Wuty-zju/ha_ninebot/security/advisories) · [Issue tracker](https://github.com/Wuty-zju/ha_ninebot/issues)

For exposed credentials, revoke or replace them through the account provider. Review attachments for account identifiers, tokens and exact routes before sharing.

## Trust boundaries

| Boundary | What travels across it |
|---|---|
| Vehicle service | Account-specific credentials are handled by an authenticated loopback service. Vehicle controls check the current owner and the account’s control selection. |
| Local history | Account archives, transfers, HA backups and automation traces can contain precise routes. An exported archive is unencrypted. Transfer and store it privately. |
| Maps | Saved geometry is delivered only for authorized vehicles with location enabled. The native HA map uses the configured HA tile service. A tile request reveals the viewed area to that service. |
| Assistant | Native HA identity, account consent, vehicle selection and current ownership govern every private read. An external model host may receive the selected measurements, dates and labels. Dedicated tools exclude credentials, raw payloads and exact GPS. |
| Sync tasks | Separate consent and human approval bind a request to its requester, vehicle, range and cloud budget. |
| Support | Diagnostics contain selected schema and error metadata. Share only the lines needed to reproduce a problem. |

[Account backup and removal](docs/guide.md#privacy-backup-and-removal) · [Map privacy](docs/history.md#map-privacy-and-units) · [Assistant access](docs/assistant.md) · [Third-party notices](NOTICE)

简体中文：普通故障请提供版本、一次复现和相关的脱敏片段。安全问题优先使用仓库的私密反馈通道。档案导出未加密，可能含精确轨迹。第三方模型只应获得你明确选择的车辆数据。[隐私、备份与移除](docs/guide_zh.md#隐私备份与移除) · [Assistant 授权](docs/assistant_zh.md)
