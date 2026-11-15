# Ninebot Assistant — 2.1 preview

[简体中文](assistant_zh.md) · [Documentation](README.md) · [History and maps](history.md)

Ninebot Assistant makes selected local vehicle information available to a compatible AI host through Home Assistant's native LLM API and MCP Server. The preview provides six local vehicle tools and a versioned public-help search. Your existing entities, controls and history synchronization continue to work independently.

## Enable it

1. Install a **2.1.x prerelease** from Marketplace/HACS, or its packaged release. The recommended stable integration remains [v2.0.0](https://github.com/Wuty-zju/ha_ninebot/releases/tag/v2.0.0).
2. Use Home Assistant **2026.11+** for the native Assistant API. On earlier versions, ordinary Ninebot features retain the integration's **2026.1.0** minimum; the Assistant option is unavailable without the required native API.
3. Open **Settings → Devices & services → Ninebot → Configure** for each account you want to expose. Enable **Ninebot Assistant (Beta)** and select its vehicles. The default is off with no vehicles selected. This selection is separate from the remote-control allowlist.
4. In the native **MCP Server** integration, turn off access to all LLM APIs and select **Ninebot**. Keep administrator access required and read-only mode enabled. Connect your MCP client to `/api/mcp` using Home Assistant's supported authentication.
5. Use an active administrator identity. Every tool call checks that user, the account's Assistant setting and the vehicle selection again. Calls without a user identity are rejected.

For a native Conversation/Assist host, select the **Ninebot** API if the host supports custom LLM APIs and preserves an authenticated user Context. Available selectors and tool support depend on that host. Ninebot does not configure a model, an API key or a separate listener. A generic Action tool or access to exposed entities is a separate capability; registering Ninebot Actions alone does not enable these dedicated tools.

MCP clients that support prompts can retrieve the **Ninebot** prompt through `prompts/list` and `prompts/get`. Apply it using the client's prompt interface; discovering tools alone does not ensure the client applies this guidance. Permissions are enforced on every tool call regardless of prompt support.

## Choose a host and model

Ninebot Assistant uses Home Assistant's native LLM API and MCP tools. Configure the model in your chosen Home Assistant conversation agent or MCP client. A compatible host must support tool calling, the Ninebot collection and authenticated user context. The integration has no required model vendor, local inference engine or model weights.

The host handles conversation and model access. Ninebot supplies authorized local queries, versioned help, task checklists and the separate human-reviewed task API. Model choice and response presentation belong to the host; the same tool schemas and permission checks apply to every model.

Developer test hosts and fixed model baselines are documented in the [evaluation guide](development.md#small-model-baseline). They are separate from the installation and model configuration above.

## Ask about local data

Examples:

- “How many trips did Vehicle A make yesterday?”
- “How much energy did Vehicle A use last month?”
- “How many trips did Vehicle A make on November 5, 2026?”
- “List Vehicle A's trips on November 5, then show the selected trip's saved detail.”
- “Is Vehicle A's status fresh?”
- “What is the history synchronization waiting for?”

With several vehicles, select a vehicle before asking for a measurement. The host obtains an opaque `vehicle_ref` from `ninebot__get_vehicles`; an account number, vehicle serial number or MCP client-origin `device_id` is not a substitute.

| Tool | Parameters | Result |
|---|---|---|
| `ninebot__search_help` | `query`; optional `language` (`en` or `zh-Hans`), `include_procedures` (default `false`) | Up to three cited public help topics; explicitly request task checklists when guiding a task. |
| `ninebot__get_vehicles` | None | AI-authorized vehicle references, display labels and local-data capabilities. |
| `ninebot__get_vehicle_status` | `vehicle_ref` | Cached status/BMS values and per-group observation quality; no refresh or coordinates. |
| `ninebot__get_statistics` | `vehicle_ref`, `period` | Local `today`, `yesterday`, `this_month`, `last_month` or one explicit business date, with per-metric coverage. |
| `ninebot__get_recorded_trips` | `vehicle_ref`, `start_date`, `end_date`; optional `limit`, `cursor` | One descending local page. Dates use `YYYY-MM-DD`, up to 31 inclusive days; default 10, maximum 20 records. |
| `ninebot__get_trip_detail` | `vehicle_ref`, `ride_id`, `query_month` | Saved scalar detail for a verified trip from that vehicle/month. Use the list's ID and `YYYYMM` month. |
| `ninebot__get_sync_health` | `vehicle_ref` | Local coverage, progress, budget and scheduling state. Does not start or resume work. |

All tools read local information. Missing details remain missing until acquired through the normal [history features](history.md); an AI read never starts a cloud fetch. List cursors can be consumed: follow the returned next cursor rather than replaying the previous one. A page count is not the period's total.

From **2.1.10**, `get_statistics` also accepts a single `YYYY-MM-DD` value in `period`, such as `2026-11-05`. Dates use Asia/Shanghai, start at 2000 and cannot be in the future. This reads the existing local daily projection for that date. A verified empty day can return zero; an unobserved day remains unknown. The four relative periods continue to work.

From **2.1.9**, recorded-trip pages retain each ride's `received_at` and up to three month observations in `quality.months`, including on an empty page. `query.as_of` is the current tool-call time; `quality.page_as_of` is the selection cutoff retained across cursor pages. These are distinct from the archive's observation times. A fixed cutoff does not freeze the entire database or prove cloud completeness. Missing observation metadata stays `null`.

## Read the evidence

Results include a business date range, observation times, units, metric quality, source evidence and warnings. Business dates use **Asia/Shanghai**, matching the integration's current archive contract, independently of browser timezone.

- A verified empty period can report **0**. An absent measurement is **null**, with its reason/quality.
- Partial coverage describes the records currently known; it cannot prove a complete total.
- Current values can be stale. A closed month's age alone does not make its historical facts stale.
- Daily-chart mileage and totals grouped by ride end date describe different source projections; the result identifies the basis.
- Maximum speed is separate from overall average speed calculated from distance and duration.
- Point-by-point raw sample units stay unverified. This policy also covers an unverified fourth column: do not label it `mph` or the trip's overall average speed, or convert it. Independent maximum and distance/total-duration average values cannot establish a sample column's meaning or unit. This is an uncertainty policy, not a definition of the Ninebot protocol.
- `acc_active` reports accessory/ignition power, not movement or a ride in progress. `seat_locked` reports only the seat latch, not whether the lid is open; that latch evidence does not describe the other readings. Battery score is not a health diagnosis. The integration displays score 0 as unsupported under a maintainer rule; it does not establish battery damage or SOH.

The native MCP response carries the JSON result as text content. Failures use a fixed code such as `not_authorized`, `invalid_arguments`, `local_data_missing`, `retry_later` or `timeout`. A failed query does not mean zero trips. Where provided, follow `retry_after_seconds`; do not loop repeatedly.

Errors include a fixed explanation. `not_authorized` reports an access failure; it cannot establish whether a trip exists. The vehicle list's `capabilities` describe local readiness: `cached_status` means a successful status receipt exists, and `local_history` means the account archive is ready. A false flag does not establish unsupported hardware. Read the state tool for actual measurements and their freshness.

Synchronization health includes the configured request rate and minimum interval. This account-wide budget paces history requests; it does not report an exact number remaining in a wall-clock hour. Follow `next_request_at` and `retry_at` for the observed scheduling state. A configured rate of zero removes quota pacing; ordinary serialization and upstream backoff still apply.

## Versioned help

Public help and task checklists are available from **2.1.1**.

Ask about unknown statistics, request budgets, coverage, missing details, stale readings, units, authentication, retention or Assistant access. `ninebot__search_help` searches bundled bilingual topics (thirteen in 2.1.2) without contacting a website or indexing private archives. Questions are limited to 240 characters; results include a fixed source commit, source/content hashes, language, applicable versions and corpus version. A help result explains documented behavior; current vehicle facts still require the corresponding data tool.

Corpus 1.2.6 contains fourteen bilingual topics and ten advisory task checklists. The separate `sample_units` topic explains unverified raw sample units, including fourth-column and `mph` questions; `measurement_units` retains maximum/overall-average speed and battery-score semantics. Both use the existing measurement checklist and pinned documentation source. Login guidance distinguishes the support records added in 2.0.1 from 2.0.0. Retrieval favors matching topic keywords, reducing unrelated results from shared words. The source contract version remains 2.1.2; a corpus update does not change the installed integration version or verify unknown fields.

From **2.1.13**, request-budget searches exclude generic task-application keywords. The exact `preview_detail_sync` and `request_detail_sync_approval` names retrieve their applicable task checklist. Approved tasks continue to share the account request budget.

Each returned match includes a ready-to-use `citation`: a Markdown link with the fixed label `Source`, the validated pinned source URL, and `KB` followed by the returned `corpus_version`. Clients can display or cite it directly. The source commit/hash, content hash and applicability range remain available separately. Citation text shares the same 4 KiB response budget and normal topic/checklist trimming; it never adds a source supplied by the question or a free-form title.

General help questions need no vehicle selection or date. For example, “What does waiting for request budget mean?” can go directly to help search; “What is Vehicle A waiting for now?” also needs that vehicle's synchronization-health result.

Search returns at most three topics in 4 KiB. It reports no match or no applicable knowledge rather than using another release’s rules. Other languages use English. A damaged help catalog leaves the six vehicle tools available. Published source links stay pinned even when the main documentation changes.

## Task checklists

From **2.1.6**, `search_help` accepts the optional Boolean `include_procedures`, defaulting to `false`. Default help returns topics and citations with `procedures: []`. Match metadata, including `procedure_id`, remains available along with the top-level `procedure_contract`. General concepts need no vehicle selection or vehicle read. When guiding a specific task, explicitly set `include_procedures: true` to retrieve applicable, deduplicated advisory checklists within the same combined 4 KiB limit. Check their required inputs before following them.

An explicitly requested versioned checklist describes: when it applies, information to ask for, permitted read tools, a call budget, stopping conditions and a result check. These cover vehicle selection, current state, period statistics, saved trips, unknown statistics, synchronization gaps, authentication support and measurement semantics. For example, an unknown-statistic check reads the selected period’s quality, then synchronization health and matching help when needed; it does not fill a missing value with zero or start synchronization.

The host follows the checklist within its existing tool loop. There is no separate Skill installer or execution framework. Checklists are advisory data and cannot grant permission; every call still uses the same program authorization. Missing inputs require clarification. Access denial stops the read, and unresolved cases end with the observed evidence and a specific next step. Sync requests and task approval remain outside this read-only preview.

## Reviewable history tasks (Beta)

From **2.1.2**, a separate **Allow reviewable history tasks (Beta)** option is available. Enable Assistant and select its vehicles first, then enable this option and save. **Ninebot Assistant** appears in the administrator sidebar. Tasks remain off by default.

For an AI client to propose work, select the separate **Ninebot tasks** native API. Applications are local writes, so the global `/api/mcp` route hides that tool when the server's read-only option is enabled. Keep the ordinary **Ninebot** collection selected for local data and help. The task tools list permitted vehicles, preview missing details over up to 31 business days, request human review, and read task progress. They cannot approve a request.

For a host that supports several API selections, select **Ninebot** and **Ninebot tasks** together to use versioned help and task checklists during an application. From **2.1.11**, task identifier types and constraints are preserved in either selection order.

Previewing a range leaves the application unsubmitted. Submission is confirmed by `task_recorded: true` and a returned task ID. Human approval follows in the review panel; a promise to submit does not record a task.

The selected detail count and the attempt allowance are separate. From **2.1.12**, preview/application responses name the allowance `requests.maximum_detail_attempts`, matching task metadata. `attempts_include_retries: true` and `attempt_scope: entire_selection` describe one shared allowance for the selected details. For example, one selected detail with two attempts allows one retry; it does not select a second detail. The earlier `maximum_after_approval` key remains an equivalent alias for existing clients. An attempt is a scheduler-level detail request, not a count of HTTP transmissions or guaranteed saved records.

Sign in as the same Home Assistant administrator used by the AI client. Open **Ninebot Assistant**, choose the vehicle and review the dates, selected detail count, total request limit and expiry. Only **Approve and synchronize** starts the approved work. The plan includes at most twenty missing details and a total request allowance of twice the selected count, capped at twenty; retries share that allowance and the account's pacing. Existing saved details are reused. **Cancel task** stops that task's remaining work while other synchronization continues.

The task view reports the last saved checkpoint, actual request consumption, retry eligibility and result. Approval is valid for ten minutes; approved work expires after 24 hours. Normal restarts resume permitted work. Restoring an official HA backup requires fresh human approval. The panel reads local metadata; it does not refresh vehicle data or fetch whole months when you review a task.

Native notifications provide a review or result link, coalesced into one generic notice per account. They contain no account, vehicle, trip identity, location or confirmation credential. Waiting, retry counters and nonce rotation do not produce repeated notices. Notifications confer no approval and do not prove completion; use the saved task state in the panel. After a restart, open the panel directly to inspect retained tasks.

From 2.1.2, public help adds two task checklists—bounded detail applications and saved-task results—for thirteen bilingual topics in total. Each requires the separate task API and allows at most four tool calls. Read checklists retain their original read tool sets. The application checklist confirms intent, vehicle and dates before preview and local application; the result checklist reads matching metadata, preserves unknown/zero/partial distinctions and respects retry timing. Neither provides an approval or cancellation tool.

## Privacy and limits

Enabling the option allows the chosen host to receive selected vehicle labels, scalar values, times, ride identifiers and quality information. Review names that you have assigned to vehicles. Ninebot excludes account credentials, account identifiers, serial numbers, precise coordinates, raw responses, logs, full tracks and sample arrays from these tools. An external model host may retain conversation and tool results under its own policy. Turning the option off revokes future calls; it cannot remove information already sent to the host. The saved vehicle selection is retained for later use.

The dedicated API admits **20 calls per authenticated user per rolling minute**, with **two calls in flight across the API**, a **10-second** call deadline, a **16 KiB** result limit and a **2 KiB** limit per listed ride. Vehicle discovery returns at most 20 allowed vehicles and explicitly reports when more are available; narrow the account selections in that case. These local limits are separate from the cloud history-request budget. When the host propagates cancellation to a tool, that call releases its admission slot. The selected host controls model-generation deadlines and retries; the ten-second limit applies to each Ninebot tool call.

Selecting broader administrator APIs alongside Ninebot can give a host capabilities outside this dedicated read contract. Use the dedicated collection and native read-only setting when you only want these queries. The read-only collection exposes no vehicle commands, archive import/deletion or synchronization requests. The separate task collection can request review; cloud work requires human approval in the panel.

Explicit `/api/mcp/<api_id>` URLs select registered collections directly and do not inherit the global API selection or read-only setting. The `/api/mcp/ninebot` collection contains only read tools. The task collection still requires account task consent and human approval before cloud execution.

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
