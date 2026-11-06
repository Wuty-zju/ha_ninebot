# Ninebot Assistant — 2.1 preview

[简体中文](assistant_zh.md) · [Documentation](README.md) · [History and maps](history.md)

Ninebot Assistant makes selected local vehicle information available to a compatible AI host through Home Assistant's native LLM API and MCP Server. The first preview provides six read-only tools. Your existing entities, controls and history synchronization continue to work independently.

## Enable it

1. Install a **2.1.x prerelease** from Marketplace/HACS, or its packaged release. The recommended stable integration remains [v2.0.0](https://github.com/Wuty-zju/ha_ninebot/releases/tag/v2.0.0).
2. Use Home Assistant **2026.11+** for the native Assistant API. On earlier versions, ordinary Ninebot features retain the integration's **2026.1.0** minimum; the Assistant option is unavailable without the required native API.
3. Open **Settings → Devices & services → Ninebot → Configure** for each account you want to expose. Enable **Ninebot Assistant (Beta)** and select its vehicles. The default is off with no vehicles selected. This selection is separate from the remote-control allowlist.
4. In the native **MCP Server** integration, turn off access to all LLM APIs and select **Ninebot**. Keep administrator access required and read-only mode enabled. Connect your MCP client using Home Assistant's supported authentication.
5. Use an active administrator identity. Every tool call checks that user, the account's Assistant setting and the vehicle selection again. Calls without a user identity are rejected.

For a native Conversation/Assist host, select the **Ninebot** API if the host supports custom LLM APIs and preserves an authenticated user Context. Available selectors and tool support depend on that host. Ninebot does not configure a model, an API key or a separate listener. A generic Action tool or access to exposed entities is a separate capability; registering Ninebot Actions alone does not enable these dedicated tools.

The native MCP handler and tool chain are checked with synthetic authorization and local records. Model behavior and authentication setup depend on your chosen host; the preview does not include measured model-quality scores.

## Ask about local data

Examples:

- “How many trips did Vehicle A make yesterday?”
- “How much energy did Vehicle A use last month?”
- “List Vehicle A's trips on November 5, then show the selected trip's saved detail.”
- “Is Vehicle A's status fresh?”
- “What is the history synchronization waiting for?”

With several vehicles, select a vehicle before asking for a measurement. The host obtains an opaque `vehicle_ref` from `ninebot__get_vehicles`; an account number, vehicle serial number or MCP client-origin `device_id` is not a substitute.

| Tool | Parameters | Result |
|---|---|---|
| `ninebot__get_vehicles` | None | AI-authorized vehicle references, display labels and local-data capabilities. |
| `ninebot__get_vehicle_status` | `vehicle_ref` | Cached status/BMS values and per-group observation quality; no refresh or coordinates. |
| `ninebot__get_statistics` | `vehicle_ref`, `period` | Local `today`, `yesterday`, `this_month` or `last_month` statistics with per-metric coverage. |
| `ninebot__get_recorded_trips` | `vehicle_ref`, `start_date`, `end_date`; optional `limit`, `cursor` | One descending local page. Dates use `YYYY-MM-DD`, up to 31 inclusive days; default 10, maximum 20 records. |
| `ninebot__get_trip_detail` | `vehicle_ref`, `ride_id`, `query_month` | Saved scalar detail for a verified trip from that vehicle/month. Use the list's ID and `YYYYMM` month. |
| `ninebot__get_sync_health` | `vehicle_ref` | Local coverage, progress, budget and scheduling state. Does not start or resume work. |

All six tools read local information. Missing details remain missing until acquired through the normal [history features](history.md); an AI read never starts a cloud fetch. List cursors can be consumed: follow the returned next cursor rather than replaying the previous one. A page count is not the period's total.

## Read the evidence

Results include a business date range, observation times, units, metric quality, source evidence and warnings. Business dates use **Asia/Shanghai**, matching the integration's current archive contract, independently of browser timezone.

- A verified empty period can report **0**. An absent measurement is **null**, with its reason/quality.
- Partial coverage describes the records currently known; it cannot prove a complete total.
- Current values can be stale. A closed month's age alone does not make its historical facts stale.
- Daily-chart mileage and totals grouped by ride end date describe different source projections; the result identifies the basis.
- Maximum speed is separate from overall average speed calculated from distance and duration. Unknown sample units stay unverified.
- ACC does not prove movement; seat-latch status does not prove whether the seat lid is open. Battery score is not a health diagnosis.

The native MCP response carries the JSON result as text content. Failures use a fixed code such as `not_authorized`, `invalid_arguments`, `local_data_missing`, `retry_later` or `timeout`. A failed query does not mean zero trips. Where provided, follow `retry_after_seconds`; do not loop repeatedly.

Errors include a fixed explanation. `not_authorized` reports an access failure; it cannot establish whether a trip exists. A vehicle's query capabilities describe which local reads are available, independently of its current measurements or the number of saved rides.

## Privacy and limits

Enabling the option allows the chosen host to receive selected vehicle labels, scalar values, times, ride identifiers and quality information. Review names that you have assigned to vehicles. Ninebot excludes account credentials, account identifiers, serial numbers, precise coordinates, raw responses, logs, full tracks and sample arrays from these tools. An external model host may retain conversation and tool results under its own policy. Turning the option off revokes future calls; it cannot remove information already sent to the host. The saved vehicle selection is retained for later use.

The dedicated API admits **20 calls per authenticated user per rolling minute**, with **two calls in flight across the API**, a **10-second** call deadline, a **16 KiB** result limit and a **2 KiB** limit per listed ride. Vehicle discovery returns at most 20 allowed vehicles and explicitly reports when more are available; narrow the account selections in that case. These local limits are separate from the cloud history-request budget. Cancellation releases the caller's admission slot.

Selecting broader administrator APIs alongside Ninebot can give a host capabilities outside this dedicated read contract. Use the dedicated collection and native read-only setting when you only want these queries. The first preview exposes no vehicle commands, archive import/deletion or AI-started synchronization tasks.

## Troubleshooting

| Symptom | Check |
|---|---|
| Ninebot is missing from the API selector | Confirm the running integration is a 2.1 preview, Core supports the native API, and at least one loaded account has Assistant enabled with vehicles selected. |
| No vehicles or `not_authorized` | Check the authenticated active administrator, account consent and selected vehicle ownership. Existing remote-control permissions do not grant AI access. |
| A trip detail is missing | Use the recorded-trip list to select its exact `ride_id` and `query_month`. Acquire missing details through normal history synchronization. |
| Statistics are partial or unknown | Check the indicated source/coverage and history health. Read-only tools do not repair the archive automatically. |
| Rate-limited or timed out | Respect the retry interval and reduce concurrent requests. The integration does not add another polling loop for AI. |
| Need to share a failure | Use the integration's native debug logging and reviewed diagnostics described in the [everyday guide](guide.md#troubleshooting). Remove private conversation content before posting. |

[Developer entry points](development.md) · [Synthetic evaluation cases](../tests/fixtures/assistant/evaluation-tasks.json)
