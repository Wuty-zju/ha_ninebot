# Ninebot Assistant — 2.1 preview

[简体中文](assistant_zh.md) · [Documentation](README.md) · [History and maps](history.md)

Ninebot Assistant makes selected local vehicle information available to a compatible AI host through Home Assistant's native LLM API and MCP Server. The preview provides local vehicle queries, a bounded statistics check and versioned public help. Your existing entities, controls and history synchronization continue to work independently.

## Enable it

1. Install a **2.1.x prerelease** from Marketplace/HACS, or its packaged release. The recommended stable integration remains [v2.0.0](https://github.com/Wuty-zju/ha_ninebot/releases/tag/v2.0.0).
2. Use Home Assistant **2026.11+** for the native Assistant API. On earlier versions, ordinary Ninebot features retain the integration's **2026.1.0** minimum; the Assistant option is unavailable without the required native API.
3. Open **Settings → Devices & services → Ninebot → Configure** for each account you want to expose. Enable **Ninebot Assistant (Beta)** and select its vehicles. The default is off with no vehicles selected. This selection is separate from the remote-control allowlist.
4. In the native **MCP Server** integration, turn off access to all LLM APIs and select **Ninebot**. Keep administrator access required and read-only mode enabled. Connect your MCP client to `/api/mcp` using Home Assistant's supported authentication.
5. Use an active administrator identity. Every tool call checks that user, the account's Assistant setting and the vehicle selection again. Calls without a user identity are rejected.

For a native Conversation/Assist host, select the **Ninebot** API if the host supports custom LLM APIs and preserves an authenticated user Context. Available selectors and tool support depend on that host. Ninebot does not configure a model, an API key or a separate listener. A generic Action tool or access to exposed entities is a separate capability; registering Ninebot Actions alone does not enable these dedicated tools.

MCP clients that support prompts can retrieve the **Ninebot** prompt through `prompts/list` and `prompts/get`. Apply it using the client's prompt interface; discovering tools alone does not ensure the client applies this guidance. Permissions are enforced on every tool call regardless of prompt support.

## Check MCP API selection

On HA 2026.11+, the main `/api/mcp` URL uses **Expose all AI tools** (`all_llm_apis`) first. When enabled, it selects every registered API, including APIs added later; a saved **AI tools** selection (`llm_hass_api`) does not restrict that list. Turn it off to use the selected collections. **Read-only** (`read_only`) filters tools on this main URL.

An individual `/api/mcp/<api_id>` URL selects that registered API directly and ignores the main URL's API selection and read-only options. For `/api/mcp/ninebot_tasks`, a request application can therefore remain available while the main URL is read-only. Account task consent and the original user's separate human approval still govern Ninebot work. The ordinary `/api/mcp/ninebot` collection contains only local read tools.

These rules describe the MCP routes; they do not inspect or change your configuration. See the [native MCP configuration options](https://www.home-assistant.io/integrations/mcp_server/#configuration-options).

## Choose a host and model

Ninebot Assistant uses Home Assistant's native LLM API and MCP tools. Configure the model in your chosen Home Assistant conversation agent or MCP client. A compatible host must support tool calling, the Ninebot collection and authenticated user context. The integration has no required model vendor, local inference engine or model weights.

The host handles conversation and model access. Ninebot supplies authorized local queries, versioned help, task checklists and the separate human-reviewed task API. Model choice and response presentation belong to the host; the same tool schemas and permission checks apply to every model.

Start with one agent that can select tools, ask for a missing vehicle or date, and retain the returned units, coverage, observation time and sources. The bundled help provides task checklists inside that same tool loop. You do not need a separate RAG service, Skill engine or multi-agent framework.

The [evaluation guide](development.md#small-model-baseline) distinguishes native API checks from measured model behavior. Developer test hosts and fixed model baselines are separate from user installation and model configuration.

## Query through an existing AI task

From **2.1.15**, **Developer tools → Actions → Ninebot: Ask Ninebot Assistant** offers a bounded local query without an additional MCP client. Enable Assistant for the account and select its vehicles first. Choose an existing **AI task** entity that supports native tool calling and per-task LLM APIs, enter the question, and select the vehicle when more than one is allowed. The Action requires an authenticated active administrator. Model configuration remains in the selected provider.

```yaml
action: ninebot.ask
data:
  ai_task_entity_id: ai_task.your_model
  device_id: your_ninebot_device_id
  question: How many trips did this vehicle make yesterday?
response_variable: ninebot_answer
```

The response uses `answer_mode: verified_tool_results`. Its `results` contain the actual tool names, arguments, data, quality and evidence. `outcome: evidence` means a data/help result was obtained; check the returned period and coverage before interpreting a total. It does not certify that a model understood every part of the question. Discovery alone or no data tool produces `needs_input`; provide a vehicle, date or more specific question. A stopped query with usable evidence reports `partial`. Failed or denied reads never become zero trips.

If the provider fails after a successful data or help read, the response retains the revalidated tool results with `outcome: partial` and `stop_reason: model_task_failed`. An unsuccessful read or vehicle discovery alone remains a failure. Permission withdrawal removes the results.

The model selects among the eight local tools. From **2.1.21**, the Action binds vehicle queries to its authenticated selection; the model supplies dates and other query parameters without copying `vehicle_ref`. An additional target parameter is rejected. With several vehicles and no selection, the Action still requires the user to choose one. The public MCP/Conversation tools keep their explicit vehicle references.

Its free-form final text is discarded; numbers and source links come from the tools. General help can be queried without selecting a vehicle. Requests have a 180-second deadline, at most four tool calls, one active query across this integration and a 32 KiB response limit. They do not refresh the cloud, submit history jobs or control vehicles. The same user Context is passed to the model task and business reads, with consent/ownership checked again before returning data.

Use Core with `ai_task.async_generate_data(..., llm_api=...)` support and a compatible configured provider. An unsupported task API returns `unsupported_capability`; no new provider or listener is installed. Ordinary native MCP/Conversation tools remain available independently. This response Action is not added to the dedicated Ninebot LLM tool collection.

## Ask about local data

Examples:

- “How many trips did Vehicle A make yesterday?”
- “How much energy did Vehicle A use last month?”
- “How many trips did Vehicle A make on November 5, 2026?”
- “List Vehicle A's trips on November 5, then show the selected trip's saved detail.”
- “Is Vehicle A's status fresh?”
- “What is the history synchronization waiting for?”

With several vehicles, select a vehicle before asking for a measurement. The host obtains an opaque `vehicle_ref` from `ninebot__get_vehicles`; an account number, vehicle serial number or MCP client-origin `device_id` is not a substitute. Copy the returned reference exactly. A denied request does not identify a configuration fault or establish whether a hidden vehicle exists; check the selected reference and HA access. An allowed vehicle is not a substitute for a different target in the question.

| Tool | Parameters | Result |
|---|---|---|
| `ninebot__search_help` | `query`; optional `language` (`en` or `zh-Hans`), `include_procedures` (default `false`) | Up to three cited public help topics; explicitly request task checklists when guiding a task. |
| `ninebot__get_vehicles` | None | AI-authorized vehicle references, display labels and local-data capabilities. |
| `ninebot__get_vehicle_status` | `vehicle_ref` | Cached status/BMS values and per-group observation quality; no refresh or coordinates. |
| `ninebot__get_statistics` | `vehicle_ref`, `period` | Local `today`, `yesterday`, `this_month`, `last_month` or one explicit business date/month, with per-metric coverage. |
| `ninebot__get_recorded_trips` | `vehicle_ref`, `start_date`, `end_date`; optional `limit`, `cursor` | One descending local page. Dates use `YYYY-MM-DD`, up to 31 inclusive days; default 10, maximum 20 records. |
| `ninebot__get_trip_detail` | `vehicle_ref`, `ride_id`, `query_month` | Saved scalar detail for a verified trip from that vehicle/month. Use the list's ID and `YYYYMM` month. |
| `ninebot__get_sync_health` | `vehicle_ref` | Local coverage, progress, budget and scheduling state. Does not start or resume work. |
| `ninebot__explain_statistics` | `vehicle_ref`, `period`, `metric`; optional `language` | Three local steps: period statistics, synchronization health and cited help with the unknown-statistic checklist. |

All tools read local information. Missing details remain missing until acquired through the normal [history features](history.md); an AI read never starts a cloud fetch. List cursors can be consumed: follow the returned next cursor rather than replaying the previous one. A page count is not the period's total.

From **2.1.10**, `get_statistics` also accepts a single `YYYY-MM-DD` value in `period`, such as `2026-11-05`. Dates use Asia/Shanghai, start at 2000 and cannot be in the future. This reads the existing local daily projection for that date. A verified empty day can return zero; an unobserved day remains unknown. The four relative periods continue to work.

From **2.1.17**, `period` also accepts `YYYY-MM`, such as `2026-10`. Past months use the complete calendar month; the current month ends on the current business date. Years start at 2000; future months are rejected. Monthly results use the existing server-month summary, independently of daily projections and trip-page totals.

To investigate a particular value, call `ninebot__explain_statistics` with a selected vehicle, period and one metric: `ride_count`, `distance_km`, `duration_s`, `energy_wh` or `energy_intensity_wh_per_km`. Its fixed workflow returns actual statistics, synchronization health, and applicable cited help with checklist SK05. `data.steps` preserves each tool result and its observation time. `quality.partial` identifies failed or unexecuted steps; the reported metric reason describes the available evidence and does not establish a root cause. The workflow does not repair data or start cloud work.

From **2.1.9**, recorded-trip pages retain each ride's `received_at` and up to three month observations in `quality.months`, including on an empty page. `query.as_of` is the current tool-call time; `quality.page_as_of` is the selection cutoff retained across cursor pages. These are distinct from the archive's observation times. A fixed cutoff does not freeze the entire database or prove cloud completeness. Missing observation metadata stays `null`.

## Read the evidence

Results include a business date range, observation times, units, metric quality, source evidence and warnings. Business dates use **Asia/Shanghai**, matching the integration's current archive contract, independently of browser timezone. This field describes the archive, not the browser setting. The tools do not regroup daily/monthly totals into another timezone. A UI may display timestamps locally while the statistics retain their documented business-date boundaries.

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

From 2.1.24, cached status can include a small `quality.groups.<group>.failure_context`: the current reviewed error kind, local request/normalization/archive stage, endpoint template, proxy HTTP/code, attempt time and retryability. It contains no raw response, log text or identity. The proxy describes the local backend response; it cannot establish a cloud region, hardware fault or cloud-side cause. Successful recovery clears this context. It uses the same vehicle consent and query authorization as the status values.

## Versioned help

Public help and task checklists are available from **2.1.1**.

Ask about unknown statistics, request budgets, coverage, missing details, stale readings, units, authentication, retention or Assistant access. `ninebot__search_help` searches bundled bilingual topics (thirteen in 2.1.2) without contacting a website or indexing private archives. Questions are limited to 240 characters; results include a fixed source commit, source/content hashes, language, applicable versions and corpus version. A help result explains documented behavior; current vehicle facts still require the corresponding data tool.

Corpus 1.2.9 contains nineteen bilingual topics and ten advisory task checklists. For installations from 2.1.26, `control_diagnostics` uses the pinned 2.1.26 troubleshooting guide to explain `control_routing`, `confirmation_deadline_reached`, `upstream_code` and `backend_error`. The operation matters when interpreting a proxy 502. A command response, the local confirmation window and the selected route each provide different evidence about the original attempt.

Topics on missing vehicle data, control receipts and battery cycle support apply from 2.1.24 and cite the pinned 2.1.23 guide. `sample_units` covers unverified raw samples. `measurement_units` covers maximum and overall-average speed and battery-score semantics. Login guidance covers 2.0.0 and the support records introduced in 2.0.1. `mcp_configuration` explains main and individual URL settings and recognizes exact configuration keys. Retrieval favors matching topic keywords and applies each topic's own version bounds and source.

From **2.1.13**, request-budget searches exclude generic task-application keywords. The exact `preview_detail_sync` and `request_detail_sync_approval` names retrieve their applicable task checklist. Approved tasks continue to share the account request budget.

Each returned match includes a ready-to-use `citation`: a Markdown link with the fixed label `Source`, the validated pinned source URL, and `KB` followed by the returned `corpus_version`. Clients can display or cite it directly. The source commit/hash, content hash and applicability range remain available separately. Citation text shares the same 4 KiB response budget and normal topic/checklist trimming; it never adds a source supplied by the question or a free-form title.

General help questions need no vehicle selection or date. For example, “What does waiting for request budget mean?” can go directly to help search; “What is Vehicle A waiting for now?” also needs that vehicle's synchronization-health result.

For “the App has data but HA does not”, first identify the field and business period. Current values use cached status; a specific unknown statistic uses `explain_statistics`; history gaps use synchronization health and saved-trip queries. Use help for field meanings and capture instructions. A 100% progress value can describe a verified empty denominator. Ready controls, command acceptance and observed target state are separate facts. Read-only Assistant tools provide guidance; they do not send a control or collect fresh cloud data.

Search returns at most three topics in 4 KiB. It reports no match or no applicable knowledge rather than using another release’s rules. Other languages use English. A damaged help catalog leaves the six vehicle queries available; a statistics check can return its other steps with partial quality. Published source links stay pinned even when the main documentation changes.

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

Sign in as the same Home Assistant administrator used by the AI client. Open **Ninebot Assistant**, choose the vehicle and review the dates, selected detail count, total request limit and expiry. Only **Approve and synchronize** starts the approved work. The plan includes at most twenty missing details and a total request allowance of twice the selected count, capped at twenty; retries share that allowance and the account's pacing. Existing saved details are reused. **Cancel task** stops that task's remaining work while other synchronization continues. To cancel, the original requester signs in as the same HA user, opens this panel and selects **Cancel task**. A read tool or chat confirmation cannot cancel it. An unapproved request expires without execution; creating a different date range leaves the earlier request in place.

The task view reports the last saved checkpoint, actual request consumption, retry eligibility and result. Approval is valid for ten minutes; approved work expires after 24 hours. Normal restarts resume permitted work. Restoring an official HA backup requires fresh human approval. The panel reads local metadata; it does not refresh vehicle data or fetch whole months when you review a task.

Native notifications provide a review or result link, coalesced into one generic notice per account. They contain no account, vehicle, trip identity, location or confirmation credential. Waiting, retry counters and nonce rotation do not produce repeated notices. Notifications confer no approval and do not prove completion; use the saved task state in the panel. After a restart, open the panel directly to inspect retained tasks.

From 2.1.2, public help adds two task checklists—bounded detail applications and saved-task results—for thirteen bilingual topics in total. Each requires the separate task API and allows at most four tool calls. Read checklists retain their original read tool sets. The application checklist confirms intent, vehicle and dates before preview and local application; the result checklist reads matching metadata, preserves unknown/zero/partial distinctions and respects retry timing. Neither provides an approval or cancellation tool.

## Privacy and limits

A statistics check consumes one admission for setup and one for each of its three child reads, without nesting active slots. All steps share the ten-second deadline. Verified Ninebot `busy` errors on idempotent local Actions are retried at most once within the same admission and deadline; cursors, access failures and unknown errors are not replayed. Successful results report `query.local_action_attempts`, separately from model tool calls and cloud budgets.

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
