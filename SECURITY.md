# Security and privacy

This is an independent cloud-connected custom integration. The actively maintained line is the current **2.0 prerelease**; older releases are not promised backported security fixes. There is no guaranteed response SLA or vendor endorsement.

## Reporting a concern

**Do not post passwords, tokens, private session files, real vehicle/account identifiers, exact locations or full trails in Issues, Discussions or PRs.** Sanitized diagnostics still need your review. Describe ordinary non-sensitive bugs using the issue form.

GitHub private vulnerability reporting was **not enabled when this page was prepared (2026-10-18)**. If the repository's Security tab now offers **Report a vulnerability**, use that private channel. Otherwise, open a Discussion containing only a request for a private reporting channel, without vulnerability details, logs, personal data or exploit instructions. Wait for a verified private channel before sending details. No unverified contact email or response time is promised.

Do not use a normal public bug report to disclose a credential leak. If your own credentials may have leaked, use the official account/App controls to revoke or replace them; do not attach the leaked files here.

## Trust boundaries

- Passwords go in the authenticated local request body, not ConfigEntry or command-line arguments. Local token/config files remain sensitive; same-user/privileged processes can access host data.
- Account sessions, cloud ownership and local archive permissions remain separate. Controls need explicit opt-in/allowlist and guarded state readback; never use real controls as an unsolicited test.
- Stored business evidence can contain GPS and unknown safe business fields. Private archives, recovery points, backups and automation response traces require protection even if diagnostics redact coordinates.
- Online map tiles are optional and disclose the viewed map area to the provider when enabled. No automatic coordinate transformation is applied to unverified CRS data.
- A fixed native dependency is not a sandbox or a proof of reproducible upstream builds. Read [dependency disclosures](docs/architecture.md#dependencies-and-attribution).

For operational precautions, see [privacy/backup](docs/guide.md#privacy-backup-and-removal), [map privacy](docs/history.md#map-privacy-and-units) and [migration/rollback](docs/migration.md).

简体中文：不要公开凭据、私人会话、车辆身份或轨迹。当前私密漏洞通道开启状态需在 Security 页确认；若没有，仅用 Discussion 请求私密联系渠道，不附敏感细节。2.0 预发布为维护线，无响应时限承诺；私有历史/备份/trace 仍需保护。
