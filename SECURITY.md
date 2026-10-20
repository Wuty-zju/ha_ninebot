# Security and privacy

Ninebot for Home Assistant is an independently maintained cloud integration. The current maintenance line is 2.0.

## Reporting a concern

For an ordinary bug, open an Issue with integration/HA versions, reproduction steps and reviewed diagnostics. Remove credentials, account or vehicle identifiers, precise locations and full routes before posting.

If the repository's Security tab offers **Report a vulnerability**, use that private channel. Private vulnerability reporting was disabled when checked on 2026-10-20. Until a private channel is available, open a Discussion asking the maintainer how to contact them privately, leaving the vulnerability details out of the public message.

If account credentials are exposed, revoke or replace them through the official account/App settings.

## Trust boundaries

- Authentication uses a managed local connection. Passwords travel in the authenticated HTTP body; session tokens remain in the private HA configuration directory.
- Each account has its own session and history archive. Vehicle ownership, control enablement and the selected allowlist are checked before commands. New accounts enable controls for vehicles verified at sign-in; users can change this in options.
- History archives, exports, backups and automation traces can contain precise routes. Archive export packages are not encrypted; transfer and store them privately.
- Optional online map tiles disclose the viewed area to the tile provider. Bundled maps keep external tiles off by default.
- Diagnostics expose reviewed metadata and redact credentials, identity and location. Debug information and arbitrary raw captures require review before sharing.
- Pinned dependencies and bundled third-party assets are documented in [NOTICE](NOTICE) and the [architecture](docs/architecture.md#dependencies-and-attribution).

For operation and recovery, see [privacy and backup](docs/guide.md#privacy-backup-and-removal), [map privacy](docs/history.md#map-privacy-and-units) and [migration](docs/migration.md).

简体中文：普通问题可附已检查的诊断；凭据、账号／车辆身份和精确轨迹应从公开内容中移除。漏洞细节通过私密通道反馈；若尚无通道，先公开请求私密联系方式。历史库、导出包、备份和自动化 trace 均需妥善保管，导出包未加密。
