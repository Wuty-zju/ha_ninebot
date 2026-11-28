# Ninebot Assistant

[简体中文](assistant_zh.md) · [Setup](#enable-it) · [Tool reference](#ask-about-local-data) · [Permissions](#privacy-and-limits) · [History tasks](#reviewable-history-tasks-beta)

**2.2.15 preview.** Ask about a vehicle’s latest reading, an archived ride or a statistic. Ninebot returns local evidence to the conversation host you already use. For earlier versions see the [stable 2.2.0 guide](https://github.com/Wuty-zju/ha_ninebot/blob/v2.2.0/docs/assistant.md) and the [single-API upgrade](#upgrade-to-the-single-ninebot-api).

<a id="ninebot-assistant--21-preview"></a>

## Enable it

1. On a compatible Home Assistant installation (native tools supported from **2026.11**), open **Settings → Devices & services → Ninebot → Configure** for the account. Select **Configure Ninebot Assistant**, then **Enable Assistant for this account**.
2. Choose a vehicle. Grant the reads you want to share. **Edit another vehicle** repeats this step before saving. A newly discovered vehicle has no Assistant grants.
3. Choose **Ninebot** in a tool-capable conversation agent or the native MCP Server. Sign in as an active HA administrator. Try “How many rides did I take yesterday?” with a vehicle granted **Summary statistics**.

| Vehicle setting | What a host can receive |
|---|---|
| Vehicle status | Cached scalar observations such as battery charge or lock state, with freshness. |
| Summary statistics | One vehicle’s day or month metrics, units, coverage and a guided check of one metric. |
| Individual trips | A local ride list and saved scalar details, including the ride references needed for the next query. |
| Sync health | Archive coverage, saved counts and the scheduler’s recorded state. |
| History tasks | A separate level for previewing gaps, reading your tasks or submitting a request for human approval. |

Each private call checks the signed-in user, current vehicle ownership and the grant for that operation. A settings change while a call is running can invalidate the entire result. Enabling vehicle controls on the device page grants no Assistant permissions.

## Choose a host and model

| Entry point | Setup | Response |
|---|---|---|
| Assist / Conversation | Select the **Ninebot** tool collection in your existing conversation agent. | The host composes its reply from tool results. |
| External MCP client | Connect to HA’s authenticated MCP Server. Use `/api/mcp/ninebot` for the dedicated collection. | Native MCP tool schemas and results under the signed-in HA user. |
| Automation / Developer tools | Pass a tool-capable `ai_task` entity to `ninebot.ask`. | A bounded set of verified tool results and a display answer for supported results. |

The model can run locally or with a provider. A host that offers MCP prompts can also load the Ninebot prompt using its prompt picker. The integration uses the host’s model and HA’s authentication. [Home Assistant LLM API](https://contributors.home-assistant.io/core/internals/llm-api/) · [MCP Server](https://www.home-assistant.io/integrations/mcp_server/) · [AI Task](https://www.home-assistant.io/integrations/ai_task/)

## Check MCP API selection

The main URL `/api/mcp` follows the native MCP Server options. Turn off **Expose all AI tools** to restrict it to the selected collections. **Read-only** on that URL filters out the application-writing tool. With **Expose all AI tools** enabled, newly registered collections also become available.

The dedicated `/api/mcp/ninebot` URL selects the Ninebot API directly. It uses the per-vehicle permissions even when the main endpoint’s selection or read-only filter differs. Set a vehicle’s task level to **Off** or **Preview only** to prevent applications across all hosts. Up to eight query tools, four task preview/status tools and one application tool can be advertised. Discovery is the union of permitted operations. Calling a listed tool for another vehicle still requires that vehicle’s grant. [MCP configuration](https://www.home-assistant.io/integrations/mcp_server/#configuration-options)

## Query through an existing AI task

```yaml
action: ninebot.ask
data:
  ai_task_entity_id: ai_task.your_model
  device_id: your_ninebot_device_id
  question: How many rides did I take yesterday?
response_variable: ninebot_answer
```

Read `outcome` first. With `evidence` or `partial`, read `results` or display `{{ ninebot_answer.get('answer', {}).get('text', '') }}`. The optional `answer.text` is assembled from locally validated receipts. Its `request_ids` identify the supporting calls. A host may word its own answer differently.

If multiple vehicles match, `needs_input` can return `next_step.choices`. Pick the intended HA device and repeat the question with its `device_id`. This also works for identically named vehicles. With an explicit target, the tool call is bound to that vehicle. The query Action is read-only and supports an explicit selection outside the first discovery page. A denied call discards private evidence.

<a id="daily-follow-ups"></a>
<a id="statistics-follow-ups"></a>
<a id="compound-statistics"></a>

## Follow up on a statistic

Start with a single measure and a selected vehicle. Each follow-up reads the archive again under the caller’s current permissions. Two vehicles can share a name, so the selection uses the Home Assistant device ID.

| Follow-up | What changes | What carries forward |
|---|---|---|
| “What about yesterday?” or “What about last month?” | The period, relative to the new question’s date in Asia/Shanghai | Vehicle and metric |
| “And energy use?” | The metric becomes total energy consumed in Wh | Vehicle and exact start and end dates |
| “And the other one?” | The vehicle, if there is exactly one other visible choice with statistics access | Exact dates and metric |
| “How much energy did the other vehicle use last month?” | Vehicle, period and metric together | The previous device ID identifies which vehicle to leave out |

For example, ask about last month in November and follow up on energy use in December. The answer still covers October 1–31. A month-to-date receipt records the last included day. If the archive now offers a longer monthly summary, the tool asks you to confirm the expanded period.

Assist and MCP clients use the latest single-metric receipt’s `query_context`. It contains `device_id`, `metric` and either `date` or `month` with `through_date`. Pass that selection to `get_statistics` as `vehicle_ref`, `period`, `metric` and, for months, `through_date`. For one change, use a short value such as `last_month` or `energy_wh`. A combined follow-up uses one object, for example `"change": {"vehicle": "other", "period": "last_month", "metric": "energy_wh"}`. For an object, choose `previous` or `other` explicitly. Ninebot resolves the vehicle and the preceding Shanghai calendar month, then reads the requested total once. If several vehicles could be meant, choose one first. A revoked statistics grant ends the query without returning a value.

In an automation, pass the complete selection back to `ninebot.ask`. An explicit `device_id` pins the Action to that vehicle.

```yaml
action: ninebot.ask
data:
  ai_task_entity_id: ai_task.your_model
  question: And energy use?
  query_context: "{{ ninebot_answer.query_context }}"
response_variable: next_answer
```

The new receipt identifies the source, observation time and coverage. If access changes during a read, Ninebot discards that result. Conversation hosts manage messages already displayed in chat.

## Ask about local data

| Native tool (`ninebot__` prefix) | Inputs | Reads |
|---|---|---|
| `get_vehicles` | None | Authorized HA references, labels, `allowed_reads` and local readiness flags. |
| `get_vehicle_status` | `vehicle_ref`, optional `metric` | One cached reading or an overview. The receipt identifies the observation group, time and freshness. |
| `get_statistics` | Vehicle, `period`, optional `metric`, `other_vehicle`, `through_date`, `change` | Business-day or monthly statistics. Use one metric for a numerical question. |
| `explain_statistics` | Vehicle, `period`, `metric`, optional `language` | One metric, its quality and any further evidence justified for that vehicle. |
| `get_recorded_trips` | Vehicle, `start_date`, `end_date`, optional `limit`, `cursor` | A descending page of rides overlapping up to 31 days. Default 10, maximum 20 per page. |
| `get_trip_detail` | Vehicle, `ride_id`, `query_month` | Locally saved scalar detail. Use the exact ID and `YYYYMM` month returned by the ride list. |
| `get_sync_health` | Vehicle | Distinct saved rides, verified/simplified details, plan progress and request pacing. |
| `search_help` | `query`, optional `language`, `include_procedures` | Versioned public help and, if requested, matching task checklists. |

Statistics accept `today`, `yesterday`, `this_month`, `last_month`, `YYYY-MM-DD` or `YYYY-MM` from 2000 through the current business date. Dates use **Asia/Shanghai**. A question submitted through the Action before midnight keeps that business date for the entire turn. In Assist or MCP, reuse the first receipt’s explicit dates for further reads within the same question. Current-month totals run through today.

<a id="statistics-check"></a>

## Read the evidence

**Example.** Two rides yesterday and zero rides on a complete, closed day are both valid observations. In 2.2.12, `explain_statistics` returns just the requested metric. Its `evidence.outcome` explains what the check did.

| Outcome | What was read | How to use it |
|---|---|---|
| `metric_verified` | One local statistical read. The period is closed, the value is available, it has an observation time, and the source confirms coverage. | Report the value and its date, unit, observation time and source. |
| `coverage_review` | The metric, applicable public guidance and sync health only if this vehicle permits it and the read budget allows it. | Cite the metric’s recorded reason. `data.steps` lists the calls that actually ran. A missing grant is reported as `sync_health_outside_grant`. |

A closed day uses its verified ride window. A closed month uses a server summary observed after the period ended. Today may have a useful value and still be open. `quality.partial` indicates incomplete diagnostic evidence. A null measurement stays unknown. When a source records a gap, the underlying cause may still be unresolved.

| Evidence | Interpretation |
|---|---|
| `query.as_of` and `received_at` | The first is the query time. The second is when the contributing measurement or archive receipt was observed. |
| `distance_basis` and business date | Daily distance can come from the server’s daily chart. Ride-based totals use the entire ride on its ending business date. A list page selects overlapping rides. |
| Page coverage | `quality.page_as_of` carries the selection cutoff through each cursor page. Month observations and `has_more` distinguish an empty page from a verified empty period. |
| `archive_counts` and `details` | Whole-vehicle archive counts include imports and summary-only rides. The synchronization plan has its own denominator. |
| Vehicle status | ACC reports accessory power. The seat latch reports its lock state. For a single reading, use the freshness and timestamp from its own source group. |
| Speed and energy | Trip maximum and distance divided by total duration describe different metrics. Point-sample units remain unverified. The battery health score is a reported score. |

The map reads precise geometry through the separate authorized dashboard path. Use the card to replay a saved route or compare whole rides. [Ride archive and map](history.md)

## Versioned help

Public instructions, field meanings and support cases are bundled with the integration. The search checks applicability to the installed release and returns up to three matches in 4 KiB. Each result carries the knowledge version, language, permanently pinned source and a ready-to-display `citation`. General questions such as “How does the map compare two rides?” need no private measurements. A question about a particular vehicle needs a corresponding data receipt.

## Task checklists

Set `include_procedures: true` when you want a guided task. A matching checklist gives the required inputs, permitted reads, maximum number of calls, stop conditions and the evidence to inspect. For example, **SK05** starts with the selected period and metric. Follow-up reads use the same vehicle and its own permissions. The default search returns topics and citations only.

## Reviewable history tasks (Beta)

1. For the selected vehicle choose **Preview only** or **Allow requests**. The default is **Off**. Preview up to 31 business days. The response identifies missing local details and the maximum number of attempts.
2. The host can submit a local application only with **Allow requests**. Submission returns `task_recorded: true` and a task ID. If a reply was interrupted, check the original user’s task list.
3. Sign in as the same HA administrator, open **Ninebot Assistant** in the sidebar, review the vehicle, dates and attempt limit, then choose **Approve and synchronize**. The normal history scheduler performs the work.
4. Check saved results or select **Cancel task** to stop its remaining work. The panel shows actual attempts, retries, expiry and durable progress. A native notification can link to the request.

| Task tools | Vehicle permission |
|---|---|
| `task_vehicles`, `preview_detail_sync`, `get_tasks`, `get_task` | Preview or request access. Saved tasks belong to the requester. |
| `request_detail_sync_approval` | Request access, review service ready and write tools permitted by the host. |

Each plan freezes up to 20 missing details. The shared attempt allowance is twice the selected count, capped at 20, including retries. `requests.maximum_detail_attempts` and its older alias `maximum_after_approval` describe this allowance. Human approval is valid for ten minutes. Approved work expires after 24 hours; restoring an official HA backup requires fresh approval.

## Privacy and limits

Vehicle labels, dates, scalar readings and ride references may reach the chosen model host. Precise positions, complete routes, credentials, raw responses and logs are excluded from Assistant results. Its tools have no vehicle-control or database-import operations. A generic Home Assistant tool collection has its own permissions.

Local query limits are 20 admissions per user per rolling minute, two concurrent reads across the API, ten seconds per tool result and 16 KiB per result. A listed ride is limited to 2 KiB. The diagnostic workflow uses one admission for setup plus one for each read actually taken. Idempotent local Actions can retry a confirmed busy response once; `query.local_action_attempts` records those attempts. A cursor or a denied read is never replayed automatically.

## Upgrade to the single Ninebot API

Since the 2.2.9 preview the dedicated URL is `/api/mcp/ninebot`. Hosts with an explicit API selection should remove **Ninebot tasks**, select **Ninebot**, save and open a new session. The retired `/api/mcp/ninebot_tasks` URL returns 404. If you used earlier task grants, open **Review existing task access**, choose each vehicle’s task level and apply. Existing requests retain their original review rules. [Version changes and rollback](migration.md#move-between-22-releases)

## Troubleshooting

| Symptom | First check |
|---|---|
| The tool collection is missing | Confirm Core supports native tools, an account has Assistant enabled and the host selected **Ninebot**. |
| `not_authorized` or a choice is requested | Check the authenticated administrator, selected HA device and that vehicle’s read grants. A denial establishes no fact about a trip. |
| A total is unknown | Request the exact metric and business date. Inspect its observation time, per-metric quality and coverage. |
| Only the endpoints appear | Check the saved detail’s geometry in the map. An endpoint pair does not contain an intermediate path. |
| The sync tool is absent or waiting | Check the vehicle’s task level, host read-only setting, review service and any `retry_after_seconds`. |
| A failure needs investigation | Capture one attempt with HA’s debug logging, then download the redacted diagnostic. [User guide](guide.md#troubleshooting) |

[Developer reference](development.md) · [Synthetic evaluation cases](../tests/fixtures/assistant/evaluation-tasks.json)
