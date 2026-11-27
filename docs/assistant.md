# Ninebot Assistant

[简体中文](assistant_zh.md) · [Documentation](README.md) · [History and maps](history.md)

This guide describes the **2.2.9 preview**. Use the matching guide for [v2.2.0 stable](https://github.com/Wuty-zju/ha_ninebot/blob/v2.2.0/docs/assistant.md) or the [v2.2.8 preview](https://github.com/Wuty-zju/ha_ninebot/blob/v2.2.8/docs/assistant.md).

Connect a compatible conversation host to your saved vehicle data, cited local help and optional reviewed history tasks. The native Home Assistant LLM API and MCP Server preserve the signed-in user throughout each tool call.

## Upgrade to the single Ninebot API

The 2.2.9 preview removes **Ninebot tasks** (`ninebot_tasks`). Sign in as an administrator when checking the old `/api/mcp/ninebot_tasks` URL. It now returns 404 with no redirect.

| How your host connects | What to do |
|---|---|
| Dedicated `/api/mcp/ninebot`, or `/api/mcp` with **Expose all AI tools** | Keep the URL. Open a new session to discover the current tools. |
| MCP Server or a conversation host with an explicit **AI tools** selection | Open the host's settings, clear **Ninebot tasks**, keep **Ninebot** and your other selections, and save. If you selected only the old collection, review the data and task permissions before selecting **Ninebot**. A saved missing ID can prevent the entire tool selection from loading. |
| Client connected directly to `/api/mcp/ninebot_tasks` | Point it to `/api/mcp/ninebot` after reviewing the data and task permissions. |

Accounts keep their previously selected vehicles' read permissions. If you enabled Assistant and history tasks and had selected vehicles, use **Review existing task access** in the Ninebot Assistant options to choose **Apply per-vehicle task access**. Until you confirm, task tools stay hidden and existing requests retain their original review and execution rules. Revoking existing request access invalidates outstanding requests under the account's old grant. See [history tasks](#reviewable-history-tasks-beta) and [rollback](migration.md#move-between-22-releases).

<a id="ninebot-assistant--21-preview"></a>

## Enable it

1. Install the **2.2.9 preview** through Marketplace, HACS or a [packaged release](https://github.com/Wuty-zju/ha_ninebot/releases). Open the account you want to use.
2. Use Home Assistant **2026.11+** for the native Assistant API. On earlier versions, ordinary Ninebot features retain the integration's **2026.1.0** minimum; the Assistant option is unavailable without the required native API.
3. Open **Settings → Devices & services → Ninebot → Options** for each account you want to expose. Check **Configure Ninebot Assistant** and submit. In **Assistant access**, select **Enable Assistant for this account** and choose a vehicle. Assistant is off by default, and each newly discovered vehicle starts with no Assistant permissions. Remote-control access is managed separately.
4. In the native **MCP Server** integration, turn off access to all LLM APIs and select **Ninebot**. Keep administrator access required and read-only mode enabled. Connect your MCP client to `/api/mcp` using Home Assistant's supported authentication.
5. Use an active administrator identity. Each call rechecks the user, the account's Assistant setting, current vehicle ownership and the requested operation's permission.

For each vehicle, select **Allowed reads** from **Vehicle status**, **Summary statistics**, **Individual trips** (including saved details) and **Sync health**. General help requires an authorized vehicle. The combined statistics explanation requires statistics, sync health and help access. A vehicle with summary statistics alone can still use `get_statistics`. Check **Edit another vehicle** to configure more vehicles before saving.

For a native Conversation/Assist host, select the **Ninebot** API if the host supports custom LLM APIs and preserves an authenticated user Context. Available selectors and tool support depend on that host. Ninebot does not configure a model, an API key or a separate listener. A generic Action tool or access to exposed entities is a separate capability; registering Ninebot Actions alone does not enable these dedicated tools.

MCP clients that support prompts can retrieve the **Ninebot** prompt through `prompts/list` and `prompts/get`. Apply it using the client's prompt interface; discovering tools alone does not ensure the client applies this guidance. Permissions are enforced on every tool call regardless of prompt support.

## Check MCP API selection

On HA 2026.11+, the main `/api/mcp` URL uses **Expose all AI tools** (`all_llm_apis`) first. When enabled, it selects every registered API, including APIs added later; a saved **AI tools** selection (`llm_hass_api`) does not restrict that list. Turn it off to use the selected collections. **Read-only** (`read_only`) filters tools on this main URL.

The individual `/api/mcp/ninebot` URL selects the registered `ninebot` API directly. It ignores the main URL's API selection and read-only settings. Set task access to **Off** or **Preview only** for a vehicle when that vehicle should be unable to accept applications through the dedicated URL or a conversation host. The original requester must approve every new application separately in Home Assistant. [Native MCP configuration options](https://www.home-assistant.io/integrations/mcp_server/#configuration-options)

## Choose a host and model

Choose a host that implements HA tool calls. The model can be local or hosted.

| Entry point | Configure | What the caller receives |
|---|---|---|
| Assist / Conversation | Select **Ninebot** in a tool-capable conversation agent. | The host uses the returned data to compose its own reply. |
| External MCP client | Connect to HA’s authenticated MCP Server. Select **Ninebot** on `/api/mcp`, or connect directly to `/api/mcp/ninebot`. | The same tool schemas and results under the signed-in HA user. |
| Automation | Pass an existing tool-capable `ai_task` entity to `ninebot.ask`. | Verified tool results and, where supported, a formatted evidence answer. |

Native conversation and MCP use the same **Ninebot** collection. With all read permissions and history tasks disabled, the collection has eight tools. Preview access adds four tools for a total of twelve. Application access adds a thirteenth tool when the review service is ready and the host permits writes. Per-vehicle permissions may further limit discovery and calls. [HA LLM APIs](https://contributors.home-assistant.io/core/internals/llm-api/) · [Native MCP Server](https://www.home-assistant.io/integrations/mcp_server/) · [AI Task](https://www.home-assistant.io/integrations/ai_task/)

## Query through an existing AI task

In **Developer tools → Actions** or an automation, select the model entity and the HA vehicle. The Action passes the original user context to both. It can use only the eight query tools, limited by the selected vehicle's data permissions. One selected vehicle can be reached directly, even beyond the first 20 vehicles in the account’s discovery results.

```yaml
action: ninebot.ask
data:
  ai_task_entity_id: ai_task.your_model
  device_id: your_ninebot_device_id
  question: What is the latest reported main battery charge?
response_variable: ninebot_answer
```

Read `outcome` first. With `evidence`, inspect `results` or display `{{ ninebot_answer.get('answer', {}).get('text', '') }}`. For one supported scalar metric the 2.2.8 preview includes its value, unit, source group, observation time and freshness. A stopped request can return `partial` with revalidated results. Access withdrawal clears private results. When a choice is necessary, `needs_input` can return `next_step.type: select_vehicle`. Supply a vehicle before retrying.

The Action builds the display from tool receipts. Help preserves the source link and corpus version. Dates, counts, units and coverage come from business code. The optional display uses `ninebot-evidence-answer-v1` with contributing request IDs. Complete structured results take precedence within the 32 KiB limit. A model-hosted conversation has its own presentation rules.

The deadline is 180 seconds, with up to four tool calls and one active Action turn. This requires a provider and HA Core supporting `ai_task.async_generate_data(..., llm_api=...)`. The native **Ninebot** collection is available independently of the Action.

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
| `ninebot__get_vehicle_status` | `vehicle_ref`, optional `metric` (2.2.8) | One cached reading and its observation group, or the full cached status and BMS overview when omitted. |
| `ninebot__get_statistics` | `vehicle_ref`, `period` | Local `today`, `yesterday`, `this_month`, `last_month` or one explicit business date/month, with per-metric coverage. |
| `ninebot__get_recorded_trips` | `vehicle_ref`, `start_date`, `end_date`; optional `limit`, `cursor` | One descending local page. Dates use `YYYY-MM-DD`, up to 31 inclusive days; default 10, maximum 20 records. |
| `ninebot__get_trip_detail` | `vehicle_ref`, `ride_id`, `query_month` | Saved scalar detail for a verified trip from that vehicle/month. Use the list's ID and `YYYYMM` month. |
| `ninebot__get_sync_health` | `vehicle_ref` | Whole-archive trip counts, discovery and detail progress, and current request pacing. Read-only. |
| `ninebot__explain_statistics` | `vehicle_ref`, `period`, `metric`; optional `language` | Three local steps: period statistics, synchronization health and cited help with the unknown-statistic checklist. |

These eight tools read local information. Missing details remain missing until acquired through the normal [history features](history.md). An AI query never starts a cloud fetch. Follow the returned next cursor when requesting another page. A page count describes the rows on that page.

From **2.1.10**, `get_statistics` also accepts a single `YYYY-MM-DD` value in `period`, such as `2026-11-05`. Dates use Asia/Shanghai, start at 2000 and cannot be in the future. This reads the existing local daily projection for that date. A verified empty day can return zero; an unobserved day remains unknown. The four relative periods continue to work.

From **2.1.17**, `period` also accepts `YYYY-MM`, such as `2026-10`. Past months use the complete calendar month; the current month ends on the current business date. Years start at 2000; future months are rejected. Monthly results use the existing server-month summary, independently of daily projections and trip-page totals.

To investigate a particular value, call `ninebot__explain_statistics` with a selected vehicle, period and one metric: `ride_count`, `distance_km`, `duration_s`, `energy_wh` or `energy_intensity_wh_per_km`. Its fixed workflow returns actual statistics, synchronization health, and applicable cited help with checklist SK05. `data.steps` preserves each tool result and its observation time. `quality.partial` identifies failed or unexecuted steps; the reported metric reason describes the available evidence and does not establish a root cause. The workflow does not repair data or start cloud work.

From **2.1.9**, recorded-trip pages retain each ride's `received_at` and up to three month observations in `quality.months`, including on an empty page. `query.as_of` is the current tool-call time; `quality.page_as_of` is the selection cutoff retained across cursor pages. These are distinct from the archive's observation times. A fixed cutoff does not freeze the entire database or prove cloud completeness. Missing observation metadata stays `null`.

## Read the evidence

**Single reading example.** The synthetic result `battery_soc_percent: 0` with `quality.metrics.battery_soc_percent.status: available` means the vehicle reported zero. The same reading marked `stale` is an older observation. A missing group or a null value stays unknown. Boolean readings retain their reported `false`. See the group’s `received_at` for the observation time.

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

From 2.2.6, `archive_counts` covers all saved trips for the selected vehicle, including imported trips and summary-only records. It reports the counts of saved details, confirmed simplified routes and details whose route classification is pending. `details` continues to cover the current discovery and preservation plan. `ninebot.ask` displays both scopes when available. See [reading sync progress](history.md#read-progress-correctly).

From 2.1.24, cached status can include a small `quality.groups.<group>.failure_context`: the current reviewed error kind, local request/normalization/archive stage, endpoint template, proxy HTTP/code, attempt time and retryability. It contains no raw response, log text or identity. The proxy describes the local backend response; it cannot establish a cloud region, hardware fault or cloud-side cause. Successful recovery clears this context. It uses the same vehicle consent and query authorization as the status values.

## Versioned help

Public help and task checklists are available from **2.1.1**.

Ask about unknown statistics, request budgets, coverage, missing details, stale readings, units, authentication, retention or Assistant access. `ninebot__search_help` searches bundled bilingual topics (thirteen in 2.1.2) without contacting a website or indexing private archives. Questions are limited to 240 characters; results include a fixed source commit, source/content hashes, language, applicable versions and corpus version. A help result explains documented behavior; current vehicle facts still require the corresponding data tool.

Corpus 1.2.15 provides 21 applicable bilingual topics and ten advisory checklists for the 2.2.9 preview. Ask how to replay a saved route for a link to the dashboard guide. General help checks consent without reading vehicle measurements or coordinates. Search for `get_recorded_trips`, `get_trip_detail` or `get_sync_health`, with or without the `ninebot__` prefix, to find the corresponding pinned guide. For installations from 2.1.26, `control_diagnostics` uses the pinned 2.1.26 troubleshooting guide to explain `control_routing`, `confirmation_deadline_reached`, `upstream_code` and `backend_error`. The operation matters when interpreting a proxy 502. A command response, the local confirmation window and the selected route each provide different evidence about the original attempt.

Topics on missing vehicle data, control receipts and battery cycle support apply from 2.1.24 and cite the pinned 2.1.23 guide. `sample_units` covers unverified raw samples. `measurement_units` covers maximum and overall-average speed and battery-score semantics. Login guidance covers 2.0.0 and the support records introduced in 2.0.1. The MCP configuration topic explains main and individual URL settings and recognizes exact configuration keys. Retrieval favors matching topic keywords and applies each topic's own version bounds and source.

From **2.1.13**, request-budget searches exclude generic task-application keywords. The exact `preview_detail_sync` and `request_detail_sync_approval` names retrieve their applicable task checklist. Approved tasks continue to share the account request budget.

Each returned match includes a ready-to-use `citation`: a Markdown link with the fixed label `Source`, the validated pinned source URL, and `KB` followed by the returned `corpus_version`. In native tool results the citation appears before the excerpt. Clients can display it directly. The native API asks the model to place the complete citation first and follow with a brief explanation grounded in the returned evidence. A client that displays the model's prose controls how that prose is generated and shown. The source commit/hash, content hash and applicability range remain available separately. Citation text shares the same 4 KiB response budget and normal topic/checklist trimming; it never adds a source supplied by the question or a free-form title.

General help questions need no vehicle selection or date. For example, “What does waiting for request budget mean?” can go directly to help search; “What is Vehicle A waiting for now?” also needs that vehicle's synchronization-health result.

For “the App has data but HA does not”, first identify the field and business period. Current values use cached status; a specific unknown statistic uses `explain_statistics`; history gaps use synchronization health and saved-trip queries. Use help for field meanings and capture instructions. A 100% progress value can describe a verified empty denominator. Ready controls, command acceptance and observed target state are separate facts. Read-only Assistant tools provide guidance; they do not send a control or collect fresh cloud data.

Search returns at most three topics in 4 KiB. It reports no match or no applicable knowledge rather than using another release’s rules. Other languages use English. A damaged help catalog leaves the six vehicle queries available; a statistics check can return its other steps with partial quality. Published source links stay pinned even when the main documentation changes.

## Task checklists

From **2.1.6**, `search_help` accepts the optional Boolean `include_procedures`, defaulting to `false`. Default help returns topics and citations with `procedures: []`. Match metadata, including `procedure_id`, remains available along with the top-level `procedure_contract`. General concepts need no vehicle selection or vehicle read. When guiding a specific task, explicitly set `include_procedures: true` to retrieve applicable, deduplicated advisory checklists within the same combined 4 KiB limit. Check their required inputs before following them.

An explicitly requested versioned checklist describes: when it applies, information to ask for, permitted read tools, a call budget, stopping conditions and a result check. These cover vehicle selection, current state, period statistics, saved trips, unknown statistics, synchronization gaps, authentication support and measurement semantics. For example, an unknown-statistic check reads the selected period’s quality, then synchronization health and matching help when needed; it does not fill a missing value with zero or start synchronization.

The host follows the checklist within its existing tool loop. Checklists are advisory, and every call still uses the program's permission checks. Missing inputs require clarification. Access denial stops the read, and unresolved cases end with the observed evidence and a specific next step. See the task workflow below for history applications and human approval.

## Reviewable history tasks (Beta)

With Assistant enabled, set the **History tasks** level for each vehicle and save it. The default is **Off**. **Preview only** permits local gap previews and reading the original requester's saved tasks. **Allow requests** also permits saving a local request for review. Use the **Ninebot Assistant** panel in the administrator sidebar to approve or cancel existing work.

Select **Ninebot** in the host to use these tools. When `/api/mcp` is read-only, the native server filters out the write tool. Discovery can include an application tool for one vehicle while another vehicle still allows previews or queries only. The server checks the requested vehicle on every call.

| Task tool | Required task level | Result |
|---|---|---|
| `ninebot__task_vehicles` | Preview only or Allow requests | Authorized vehicles for history tasks. |
| `ninebot__preview_detail_sync` | Preview only or Allow requests | Local gaps and request budget for up to 31 business days. |
| `ninebot__get_tasks` | Preview only or Allow requests | The original requester's saved tasks for the vehicle. |
| `ninebot__get_task` | Preview only or Allow requests | One local task checkpoint and result. |
| `ninebot__request_detail_sync_approval` | Allow requests, with review available | Save a request for human review or return an existing matching request. |

Previewing a range leaves the application unsubmitted. Submission is confirmed by `task_recorded: true` and a returned task ID. Human approval follows in the review panel; a promise to submit does not record a task.

The selected detail count and the attempt allowance are separate. From **2.1.12**, preview/application responses name the allowance `requests.maximum_detail_attempts`, matching task metadata. `attempts_include_retries: true` and `attempt_scope: entire_selection` describe one shared allowance for the selected details. For example, one selected detail with two attempts allows one retry; it does not select a second detail. The earlier `maximum_after_approval` key remains an equivalent alias for existing clients. An attempt is a scheduler-level detail request, not a count of HTTP transmissions or guaranteed saved records.

Sign in as the same Home Assistant administrator used by the AI client. Open **Ninebot Assistant**, choose the vehicle and review the dates, selected detail count, total request limit and expiry. Only **Approve and synchronize** starts the approved work. The plan includes at most twenty missing details and a total request allowance of twice the selected count, capped at twenty; retries share that allowance and the account's pacing. Existing saved details are reused. **Cancel task** stops that task's remaining work while other synchronization continues. To cancel, the original requester signs in as the same HA user, opens this panel and selects **Cancel task**. A read tool or chat confirmation cannot cancel it. An unapproved request expires without execution; creating a different date range leaves the earlier request in place.

The task view reports the last saved checkpoint, actual request consumption, retry eligibility and result. Approval is valid for ten minutes; approved work expires after 24 hours. Normal restarts resume permitted work. Restoring an official HA backup requires fresh human approval. The panel reads local metadata; it does not refresh vehicle data or fetch whole months when you review a task.

Native notifications provide a review or result link, coalesced into one generic notice per account. They contain no account, vehicle, trip identity, location or confirmation credential. Waiting, retry counters and nonce rotation do not produce repeated notices. Notifications confer no approval and do not prove completion; use the saved task state in the panel. After a restart, open the panel directly to inspect retained tasks.

The two task checklists each allow up to four tool calls. The application checklist confirms intent, vehicle, dates and application permission before submission. The result checklist reads matching metadata with preview or application access, preserves unknown/zero/partial distinctions and respects retry timing. Approval and cancellation belong in the human review panel.

If data or task access is withdrawn during a read, that read ends. Reopen the panel or issue a new query after granting access again. Explicitly withdrawing the required task permission invalidates protected requests from the previous grant.

## Privacy and limits

A statistics check consumes one admission for setup and one for each of its three child reads, without nesting active slots. All steps share the ten-second deadline. Verified Ninebot `busy` errors on idempotent local Actions are retried at most once within the same admission and deadline; cursors, access failures and unknown errors are not replayed. Successful results report `query.local_action_attempts`, separately from model tool calls and cloud budgets.

Enabling the option allows the chosen host to receive selected vehicle labels, scalar values, times, ride identifiers and quality information. Review names that you have assigned to vehicles. Ninebot excludes account credentials, account identifiers, serial numbers, precise coordinates, raw responses, logs, full tracks and sample arrays from these tools. An external model host may retain conversation and tool results under its own policy. Turning the option off revokes future calls; it cannot remove information already sent to the host. The saved vehicle selection is retained for later use.

The dedicated API admits **20 calls per authenticated user per rolling minute**, with **two calls in flight across the API**, a **10-second** call deadline, a **16 KiB** result limit and a **2 KiB** limit per listed ride. Vehicle discovery returns at most 20 allowed vehicles and explicitly reports when more are available; narrow the account selections in that case. These local limits are separate from the cloud history-request budget. When the host propagates cancellation to a tool, that call releases its admission slot. The selected host controls model-generation deadlines and retries; the ten-second limit applies to each Ninebot tool call.

The **Ninebot** collection excludes vehicle commands and archive import or deletion. Selecting other Home Assistant APIs grants their own capabilities. To prevent applications across all hosts, set the corresponding vehicle task level to **Off** or **Preview only**. Cloud work requires human approval in the review panel.

## Troubleshooting

| Symptom | Check |
|---|---|
| Ninebot is missing from the API selector | Confirm the running integration supports Ninebot Assistant, Core supports the native API, and at least one loaded account has Assistant enabled with vehicles selected. |
| `API ninebot_tasks not found` | Clear **Ninebot tasks** from the host's saved AI tools. Keep **Ninebot** and the other selections, then start a new session. |
| No vehicles or `not_authorized` | Check the authenticated active administrator, account consent and selected vehicle ownership. Existing remote-control permissions do not grant AI access. |
| The application tool is missing | Check that the selected vehicle allows applications, that the review service is available, and that the host allows the write tool. Complete the one-time permission confirmation if prompted. |
| A trip detail is missing | Use the recorded-trip list to select its exact `ride_id` and `query_month`. Acquire missing details through normal history synchronization. |
| Statistics are partial or unknown | Check the indicated source/coverage and history health. Read-only tools do not repair the archive automatically. |
| Rate-limited or timed out | Respect the retry interval and reduce concurrent requests. The integration does not add another polling loop for AI. |
| Need to share a failure | Use the integration's native debug logging and reviewed diagnostics described in the [everyday guide](guide.md#troubleshooting). Remove private conversation content before posting. |

[Developer entry points](development.md) · [Synthetic evaluation cases](../tests/fixtures/assistant/evaluation-tasks.json)
