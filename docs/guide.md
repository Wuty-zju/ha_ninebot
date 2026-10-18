# Installation and everyday use

[简体中文](guide_zh.md) · [Documentation](README.md) · [History and maps](history.md)

## Requirements

- Home Assistant **2026.1.0 or newer** and internet access to the Chinese Ninebot App service.
- A Ninebot account with vehicles visible in the official App. Other service regions are unverified.
- A compatible 64-bit platform for the pinned runtime dependency. ARMv7/32-bit is unsupported; published Linux/macOS/Windows wheels do not certify every host or container architecture.
- HACS for the recommended installation. This is a custom integration, not a Supervisor app/add-on or a built-in Core integration.

The 2.0 series is currently a **prerelease**. Vendor service changes can affect availability. Back up your Home Assistant configuration and private storage before upgrading.

## Install with HACS

1. In HACS, open the three-dot menu and choose **Custom repositories**.
2. Add `https://github.com/Wuty-zju/ha_ninebot` with type **Integration**.
3. Open **Ninebot**, choose the desired 2.0 prerelease, and download it. Enable beta/prerelease versions in the version selector if necessary.
4. Restart Home Assistant when prompted. A downloaded file version alone does not mean the running integration has loaded it.
5. Go to **Settings → Devices & services → Add integration → Ninebot**.

[Open the HACS repository](https://my.home-assistant.io/redirect/hacs_repository/?owner=Wuty-zju&repository=ha_ninebot&category=integration) · [Add Ninebot](https://my.home-assistant.io/redirect/config_flow_start/?domain=ninebot)

These links open your configured Home Assistant instance; they do not install HACS itself. The repository is currently distributed as a **custom repository**, not advertised as a HACS default listing.

## Install manually

Download `ninebot.zip` from the selected [packaged release](https://github.com/Wuty-zju/ha_ninebot/releases). Its root contains the integration files: extract them into `<config>/custom_components/ninebot`, then restart Home Assistant and add the integration. For older versions or automatic GitHub source archives, copy the contained `custom_components/ninebot` directory instead. Do not copy the entire development repository or overlay files from different releases.

All runtime Python, translations, branding and dashboard assets are inside that integration directory. No separate service, external dashboard package or repository-root script needs to be installed manually.

## Add accounts and vehicles

Sign in with your account and password. SMS sign-in is temporarily unavailable and is not offered in setup, reauthentication or reconfiguration. Passwords are used for authentication and are not stored in ConfigEntry or passed as process arguments. After authentication, current HA opens account options; older HA completes feature selection in the same setup wizard. Private local sessions/tokens remain sensitive: protect the HA configuration and backups.

Each integration entry represents **one account**, with its own session and historical archive. Add another entry for a different account. Vehicles are discovered within that account; shared physical vehicles can have separate account-scoped HA representations.

Account titles use reported `username：account`, omitting missing components. The opaque region metadata is retained internally and is not shown in the title. User-provided entry titles are retained. HA determines the surrounding entry-section heading from the integration type; the integration does not patch global frontend translations.

Expired credentials use **Reauthenticate**. Reconfigure updates the same account; use a new entry to add another account. An older entry missing display metadata can make one enrichment read on successful setup; there is no recurring username polling loop.

## Entities and data

Availability depends on the vehicle model, permissions and actual cloud reports. Created entities are enabled/visible by default; existing user-disabled/hidden choices are respected. Feature options still control the corresponding runtime features.

| Group | Available information | Meaning and limits |
|---|---|---|
| Vehicle | Main battery SOC, precise/estimated/AI range, charging, power, ACC, battery presence, service status | Three range fields stay separate; AI range is diagnostic. ACC on does not prove movement. |
| Battery | Voltage, temperature, cycles/support, charging power, health score, pack identity/count/type, Find My support | Main battery SOC describes the propulsion battery. Health score is not SOH; 0 can mean not supported. |
| Communication | Emergency communication battery SOC | A separate percentage sensor; it does not replace the vehicle's main battery indicator. |
| Location and image | Device location tracker, vehicle image | Two valid fresh coordinates are required; no unverified coordinate conversion. The image is diagnostic. |
| Rides | Today/yesterday/month totals, last-ride metrics, monthly list coverage | Completeness is checked per metric. Missing evidence is not silently converted to zero. |
| History | Sync state, month-scan progress, saved-detail progress, last successful sync | These are different scopes; 100% saved discovered details does not prove full cloud history. |
| Controls | Vehicle/seat locks and retained remote buttons | Explicit opt-in/allowlist and authorization required; see below. |
| Native HA | Ride calendar, ride-completed event, query Actions, statistics import | Historical lists and tracks are not expanded into hundreds of entities. |

Durations retain native seconds and suggest **hours with two decimals** through HA's native conversion. Distance/range and Wh/km suggest one decimal; energy and maximum speed suggest integers. Presentation rounding does not round archived source facts. HA user overrides take precedence. Rated battery V/Ah produce a specification in kWh, not measured capacity or health.

Cycle counts display not supported only when the cycle-support flag explicitly says so; unknown support stays unknown. Missing odometer is not reported, without invented distance. English and Simplified Chinese are the primary documentation languages; shipped entity/settings labels cover additional locales with English fallback for some longer text.

## Options

Open **Settings → Devices & services → Ninebot → Configure**.

| Option | Default / range | Effect |
|---|---|---|
| Status interval | 120 s; 30–3600 s | Status demand only; battery/travel use separate 600 s periods and profiles 3600 s. These are client policy, not guaranteed cloud freshness. |
| Enable precise location | On | Allows location display/track responses; preserves an existing explicit off choice. It does not add a separate high-precision cloud request. |
| Ride calendar | On | Enables the local per-vehicle calendar; turning it off does not delete rides or disable the tracker. |
| Preserve trip details | On for new accounts; existing opt-outs retained | Stores private historical details/tracks and enables budgeted acquisition. Review location/backup implications. |
| Automatic history sync | On for new accounts; existing choices retained | Starts/resumes first discovery and later incremental work when preservation is enabled. |
| History storage limit | 100 MiB/account; 32–2048 MiB | Quota exhaustion pauses writes and retains readable records; lowering the limit does not delete history. |
| History request budget | 60/account/hour; 1–120 | Shared logical month/detail calls; not a request for upstream permission. Transport retries are outside this counter. |
| Controls + allowed vehicles | Off / empty | Explicitly permit guarded vehicle commands. History synchronization permissions are separate. |
| Nominal battery parameters | Off | Optional configured voltage/capacity and rated energy. Retired low-confidence SOC estimators remain removed. |
| Debug mode | Off | Bounded parsed current information for investigation; no extra polling or public raw-data dump. |

Requests are demand-aware, account-budgeted and shared when identical reads are in flight. Status, battery and travel have separate freshness and failure/backoff state. Unload/reload cancels work; a cache read does not become a new measurement.

## Controls and confirmation

Vehicle **unlock** requests remote start; **lock** requests remote stop. The vehicle/cloud decides its motion/P requirements. HA does not disable remote stop by guessing whether the vehicle is moving. Seat unlock opens the compartment; seat locking is manual only. The retained buttons use the same executor as the Lock entities.

Each explicit request sends one command, never an automatic replay. Up to eight fresh status reads over ten seconds check the target and publish a confirmed transition immediately; a bounded follow-up at 10/20/25 seconds can observe automatic relocking. The whole follow-up ends within 30 seconds or earlier on errors/unload/new commands. A cloud observation is not a physical guarantee; pending/unconfirmed/failed results are kept distinct. A competing command for the same physical vehicle reports busy instead of being queued for later execution.

Seat-unlock confirmation starts a separate ten-second relock observation window. A five-second “Recently unlocked” attribute confirms the observed operation without delaying a newly observed locked state. Overdue read slots are skipped rather than sent in a burst.

History sync and pause appear in device diagnostics. Sync also resumes a paused task; icons and the synchronization-state attribute distinguish running, paused and budget-wait states. History status, last successful sync and parsed raw views appear only with debug mode enabled; the calendar has its own switch. Diagnostic classification does not exclude Recorder; configure Recorder filters if needed.

## Troubleshooting

| Symptom | Check first |
|---|---|
| Integration missing after download | Confirm the directory/domain, restart as prompted, and use a release compatible with your host architecture. |
| Authentication fails | Check the official App/account region; use reauth. Do not publish credentials or token files. |
| Rated-parameter form rejects a vehicle | Close and reopen the form after the updated integration is actually loaded. Choose a currently discovered vehicle. |
| Today/yesterday values unknown | Open [coverage and statistics](history.md#coverage-and-statistics). A partial month or missing metric may not support a reliable daily total. |
| Ride event unknown | It waits for a new stable completion after the startup baseline; it is not the last historical ride timestamp. |
| History waiting for request budget | Inspect `next_request_at`, `retry_at`, `sync_phase`, pause/auth/capacity state; do not repeatedly request a full scan. |
| Custom element not found | Register the [bundled module resource](history.md#add-the-history-map), then reload the browser. |
| Track missing or a single point | Check saved-detail state, coordinate option/permissions and cloud track quality. Old simplified data cannot recreate a lost trail. |
| Storage Repair | Preserve the files and read [verification and recovery](history.md#verification-and-recovery). Do not delete the database/registry to silence it. |
| “Custom integration has not been tested by Home Assistant” | Standard third-party loader notice; it is not suppressed. |

## Privacy, backup and removal

Location display defaults on, and detail preservation can retain precise tracks. The archive and HA backups are private location records even if an individual response omits coordinates. Automation traces may retain requested Action responses. Diagnostics export reviewed schema/status metadata, not credentials, identities or GPS trails; review any file before sharing.

Optional map tiles contact an external provider only when enabled; see [map privacy](history.md#map-privacy-and-units). Protect backups and local session directories from other users and privileged processes.

Removing an account closes its work and removes only its recognized archive/journal files; unusual files/recovery copies may be retained with a Repair rather than broadly deleted. Keep a complete backup before removal. Do not edit `.storage`, entity registries or a running archive manually. For upgrades/downgrades, follow the [migration guide](migration.md).
