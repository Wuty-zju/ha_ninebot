"""Model protocol and safe artifact boundaries without a model or HA install."""

import json
import tempfile
import unittest
from pathlib import Path

from scripts.evaluate_assistant import (
    MAX_REPORT_BYTES,
    InvalidReply,
    available_tools,
    diagnostic_health,
    parse_calls,
    parse_native_calls,
    prompt_text,
    safe_result,
    select_cases,
    statistics_observations,
    tool_message,
    visible_text,
    write_report,
)


class ModelProtocolTests(unittest.TestCase):
    def test_native_prompt_is_text_from_the_selected_api_without_an_envelope(self):
        message = {"role": "assistant", "content": {"type": "text", "text": "Native API guidance"}}
        response = {"jsonrpc": "2.0", "id": 1, "result": {"messages": [message]}}
        self.assertEqual(prompt_text(response), "Native API guidance")
        self.assertEqual(response["result"]["messages"], [message])

    def test_prompt_errors_multiple_blocks_and_non_text_content_stop_before_inference(self):
        for messages in (
            None,
            [],
            [None],
            [{"role": "user", "content": {"type": "text", "text": "unexpected role"}}],
            [{"role": "assistant", "content": {"type": "image", "text": "attachment"}}],
            [{"role": "assistant", "content": {"type": "text", "text": ""}}],
            [{"role": "assistant", "content": {"type": "text", "text": "车" * 6000}}],
            [{"role": "assistant", "content": {"type": "text", "text": "one"}}] * 2,
        ):
            with self.subTest(messages=messages), self.assertRaises(InvalidReply):
                prompt_text({"result": {"messages": messages}})
        with self.assertRaises(InvalidReply):
            prompt_text({"error": {"code": -32603}})

    def test_profile_selects_original_unique_cases_without_changing_expectations(self):
        path = (
            Path(__file__).resolve().parents[2] / "tests/fixtures/assistant/evaluation-tasks.json"
        )
        catalog = json.loads(path.read_text())["cases"]
        cases = select_cases(catalog, "retrieval")
        self.assertEqual([case["id"] for case in select_cases(catalog, "host_probe")], ["Q04"])
        self.assertEqual({case["id"] for case in cases}, {"R02", "R04"})
        lid_cases = select_cases(catalog, "lid_probe")
        self.assertEqual([case["id"] for case in lid_cases], ["D05"])
        diagnostics = select_cases(catalog, "diagnostics")
        self.assertEqual([case["id"] for case in diagnostics], ["D01", "F03"])
        statistics = select_cases(catalog, "statistics")
        self.assertEqual([case["id"] for case in statistics], ["Q01", "Q02", "Q09", "Q10"])
        self.assertIs(select_cases(catalog, "statistics_probe")[0], statistics[0])
        self.assertTrue(all(any(case is original for original in catalog) for case in statistics))
        self.assertTrue(all(any(case is original for original in catalog) for case in diagnostics))
        self.assertIs(lid_cases[0], next(case for case in catalog if case["id"] == "D05"))
        self.assertTrue(all(any(case is original for original in catalog) for case in cases))
        for incomplete in (cases[:1], [cases[0], cases[0]]):
            with self.assertRaises(ValueError):
                select_cases(incomplete, "retrieval")

    def test_statistics_observations_preserve_period_facts_and_keep_empty_separate(self):
        path = (
            Path(__file__).resolve().parents[2]
            / "tests/fixtures/assistant/evaluation-fixtures.json"
        )
        fixture = json.loads(path.read_text())
        before = json.dumps(fixture, sort_keys=True)
        for empty in (False, True):
            observations = statistics_observations(fixture, empty=empty)
            for (month, raw), key in zip(observations, ("yesterday", "last_month"), strict=True):
                period = fixture["periods"][key]
                facts = (
                    fixture["periods"]["verified_empty"] if empty and key == "yesterday" else period
                )
                self.assertEqual(month, period["start_date"][:7].replace("-", ""))
                self.assertEqual(len(raw["list"]), facts["ride_count"])
                for field, total in (("ec", "energy_wh"), ("duration", "duration_s")):
                    self.assertEqual(sum(row[field] for row in raw["list"]), facts[total])
                self.assertEqual(
                    sum(row["mileages"] for row in raw["list"]), facts["distance_m"] / 1000
                )
                self.assertEqual(len({row["travel_id"] for row in raw["list"]}), len(raw["list"]))
                self.assertTrue(
                    all(
                        row["end_time"] - row["start_time"] == row["duration"]
                        for row in raw["list"]
                    )
                )
        self.assertEqual(json.dumps(fixture, sort_keys=True), before)

    def test_diagnostic_health_keeps_observed_schedule_without_inventing_remaining_budget(self):
        path = (
            Path(__file__).resolve().parents[2]
            / "tests/fixtures/assistant/evaluation-fixtures.json"
        )
        fixture = json.loads(path.read_text())
        before = json.dumps(fixture, sort_keys=True)
        response = diagnostic_health(fixture)
        self.assertEqual(response["sync_state"], "waiting_budget")
        self.assertEqual(response["next_request_at"], fixture["health"]["next_request_at"])
        self.assertFalse(response["operation_performed"])
        self.assertNotIn("remaining", json.dumps(response))
        self.assertEqual(json.dumps(fixture, sort_keys=True), before)

    def test_retrieval_off_keeps_other_tools_and_rejects_hidden_help_before_rpc(self):
        tools = [{"name": name} for name in ("ninebot__search_help", "ninebot__get_statistics")]
        self.assertEqual(available_tools(tools, True), tools)
        exposed = available_tools(tools, False)
        self.assertEqual(exposed, [tools[1]])
        allowed = {tool["name"] for tool in exposed}
        body = '<tool_call>{"name":"ninebot__search_help","arguments":{}}</tool_call>'
        with self.assertRaisesRegex(InvalidReply, "tool_not_exposed"):
            parse_calls(body, 4, allowed_names=allowed)
        self.assertEqual(parse_calls("no call", 4, allowed_names=allowed), [])

    def test_mcp_payload_is_delivered_without_the_transport_envelope(self):
        for error in (False, True):
            payload = {"error": {"code": "not_authorized"}} if error else {"data": {"soc": 0}}
            response = {
                "jsonrpc": "2.0",
                "id": 7,
                "result": {
                    "content": [{"type": "text", "text": json.dumps(payload)}],
                    "isError": error,
                },
            }
            message = tool_message("ninebot__get_vehicle_status", "call_1", response)
            self.assertEqual(message["role"], "tool")
            self.assertEqual(message["name"], "ninebot__get_vehicle_status")
            self.assertEqual(message["tool_call_id"], "call_1")
            self.assertEqual(json.loads(message["content"]), payload)
            self.assertNotIn("jsonrpc", message["content"])

    def test_malformed_or_ambiguous_tool_results_stop_the_case(self):
        for result in (
            {},
            {"content": []},
            {"content": [{"type": "text", "text": "{}"}], "isError": True},
            {"content": [{"type": "text", "text": "[]"}]},
            {"content": [{"type": "image", "text": "{}"}]},
            {"content": [{"type": "text", "text": "x" * 16385}]},
            {"content": [{"type": "text", "text": "{}"}] * 2},
        ):
            with self.subTest(result=str(result)[:100]), self.assertRaises(InvalidReply):
                tool_message("tool", "call_1", {"result": result})

    def test_decoded_mcp_text_cannot_hide_guarded_payload_keys(self):
        for key, value in (
            ("latitude", 1.25),
            ("longitude", 2.5),
            ("raw_payload", {"unexpected": "synthetic"}),
            ("access_token", "synthetic-secret"),
        ):
            response = {
                "result": {
                    "content": [{"type": "text", "text": json.dumps({key: value})}],
                    "isError": False,
                }
            }
            with self.subTest(key=key), self.assertRaises(AssertionError):
                tool_message("tool", "call_1", response)

    def test_only_the_visible_final_channel_is_retained(self):
        self.assertEqual(visible_text("<think>private</think>answer"), "answer")
        self.assertEqual(visible_text("<think>unfinished"), "")
        self.assertEqual(visible_text("hidden<think>nested</think>hidden</think>answer"), "answer")

    def test_native_calls_use_the_common_shape_name_argument_and_budget_gate(self):
        arguments = {"vehicle_ref": "fixture", "include_daily": False}
        call = {"function": {"index": 0, "name": "ninebot__get_statistics", "arguments": arguments}}
        allowed = {"ninebot__get_statistics"}
        result = parse_native_calls([call], 1, allowed_names=allowed)
        self.assertEqual(result, [{"name": "ninebot__get_statistics", "arguments": arguments}])
        self.assertIs(result[0]["arguments"], arguments)
        for calls, remaining, names, kind in (
            ([call, call], 1, allowed, "tool_budget"),
            ([call], 1, set(), "tool_not_exposed"),
            (
                [{"function": {"name": "tool", "arguments": "{}"}}],
                1,
                {"tool"},
                "invalid_tool_shape",
            ),
            (
                [{"function": {"name": "tool", "arguments": {"x": float("nan")}}}],
                1,
                {"tool"},
                "invalid_tool_arguments",
            ),
            (
                [{"function": {"name": "tool", "arguments": {"x": "车" * 6000}}}],
                1,
                {"tool"},
                "model_reply_too_large",
            ),
            (
                [{"function": {"name": "tool", "arguments": {}, "index": True}}],
                1,
                {"tool"},
                "invalid_tool_shape",
            ),
            (
                [{"function": {"name": "tool", "arguments": {}}, "unexpected": True}],
                1,
                {"tool"},
                "invalid_tool_shape",
            ),
            ({}, 1, allowed, "invalid_tool_shape"),
        ):
            with self.subTest(kind=kind), self.assertRaisesRegex(InvalidReply, kind):
                parse_native_calls(calls, remaining, allowed_names=names)
        self.assertEqual(parse_native_calls([], 0, allowed_names=set()), [])

    def test_recorded_ollama_call_id_and_optional_function_type_are_validated(self):
        call = {
            "id": "call_pt5p187g",
            "function": {"index": 0, "name": "synthetic_add", "arguments": {"a": 17, "b": 25}},
        }
        expected = [{"name": "synthetic_add", "arguments": {"a": 17, "b": 25}}]
        for extra in ({}, {"type": "function"}):
            result = parse_native_calls([call | extra], 1, allowed_names={"synthetic_add"})
            self.assertEqual(result, expected)
            self.assertNotIn("id", result[0])
            self.assertNotIn("type", result[0])
        for extra in (
            {"id": ""},
            {"id": "x" * 129},
            {"id": "车" * 43},
            {"id": 42},
            {"id": None},
            {"type": "other"},
            {"type": None},
            {"unknown": True},
        ):
            with (
                self.subTest(extra=extra),
                self.assertRaisesRegex(InvalidReply, "invalid_tool_shape"),
            ):
                parse_native_calls([call | extra], 1, allowed_names={"synthetic_add"})

    def test_multiple_calls_respect_the_remaining_budget(self):
        call = {"name": "ninebot__get_vehicle_status", "arguments": {"vehicle_ref": "fixture"}}
        text = "<tool_call>" + json.dumps(call) + "</tool_call>"
        self.assertEqual(parse_calls(text, 1), [call])
        with self.assertRaisesRegex(InvalidReply, "tool_budget"):
            parse_calls(text * 2, 1)

    def test_partial_second_call_is_not_silently_discarded(self):
        text = '<tool_call>{"name":"fixture","arguments":{}}</tool_call><tool_call>{'
        with self.assertRaisesRegex(InvalidReply, "unfinished_tool_call"):
            parse_calls(text, 3)

    def test_call_shape_and_json_are_validated_before_transport(self):
        for body in ("[1]", '{"name":"x","arguments":"{}"}', '{"name":"x"}', "{"):
            with self.subTest(body=body), self.assertRaises(InvalidReply):
                parse_calls(f"<tool_call>{body}</tool_call>", 1)
        self.assertEqual(parse_calls("Please choose a vehicle.", 0), [])

    def test_multibyte_reply_has_a_byte_limit(self):
        with self.assertRaisesRegex(InvalidReply, "model_reply_too_large"):
            parse_calls("车" * 6000, 1)

    def test_synthetic_guarded_values_cannot_enter_results(self):
        for key, value in (("latitude", 1), ("account", "private-fixture-account")):
            with self.subTest(key=key), self.assertRaises(AssertionError):
                safe_result({key: value})
        safe_result({"battery_soc_percent": 0, "charging": False, "missing": None})

    def test_rejected_report_preserves_previous_completed_measurements(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "results.json"
            write_report(output, {"completed": 1})
            before = output.read_bytes()
            with self.assertRaises(ValueError):
                write_report(output, {"oversized": "x" * MAX_REPORT_BYTES})
            self.assertEqual(output.read_bytes(), before)
            self.assertEqual(list(Path(directory).iterdir()), [output])


if __name__ == "__main__":
    unittest.main()
