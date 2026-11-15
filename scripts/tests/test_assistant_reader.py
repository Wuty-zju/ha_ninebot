"""Local reader policies without HA, model weights or network access."""

import asyncio
import copy
import json
import unittest
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from scripts.assistant_reader import (
    ROSTER,
    ReaderError,
    decode_result,
    mcp_endpoint,
    native_mcp,
    read_tools,
    run_reader,
    vehicle_context,
)
from scripts.check_assistant_knowledge import knowledge
from scripts.tests.test_assistant_presentation import receipt


def descriptor(name):
    return {
        "name": name,
        "inputSchema": {"type": "object"},
        "annotations": {"readOnlyHint": True, "destructiveHint": False, "openWorldHint": False},
    }


def generation(*requests, text=""):
    return {
        "text": text,
        "tool_calls": [{"function": request} for request in requests],
        "deadline_limited": False,
        "length_limited": False,
        "input_tokens": 10,
        "output_tokens": 10,
    }


class FakeModel:
    def __init__(self, *answers):
        self.answers = list(answers)
        self.inputs = []

    def reply(self, messages, tools, deadline):
        self.inputs.append(copy.deepcopy((messages, tools)))
        return copy.deepcopy(self.answers.pop(0))


class FakeBackend:
    def __init__(self, *payloads):
        self.payloads = list(payloads)
        self.calls = []

    async def tools(self):
        return [
            descriptor(ROSTER),
            descriptor("ninebot__get_statistics"),
            descriptor("ninebot__get_sync_health"),
            descriptor("ninebot__search_help"),
            descriptor("homeassistant__call_action"),
        ]

    async def prompt(self):
        return "Native Ninebot read instructions"

    async def call(self, name, args):
        self.calls.append((name, args))
        if name == ROSTER:
            return {
                "schema_version": 1,
                "data": {
                    "vehicles": [
                        {
                            "vehicle_ref": "synthetic-device",
                            "label": "A",
                            "capabilities": {"cached_status": True, "local_history": True},
                            "unrecognized_raw": "DO_NOT_SEND_ROSTER",
                        }
                    ]
                },
            }
        payload = self.payloads.pop(0)
        if isinstance(payload, Exception):
            raise payload
        return copy.deepcopy(payload)


class ReaderProtocolTests(unittest.TestCase):
    def test_endpoint_only_accepts_dedicated_api_and_remote_https(self):
        for value in (
            "https://ha.example/api/mcp/ninebot",
            "http://127.0.0.1:8123/api/mcp/ninebot",
            "http://[::1]:8123/api/mcp/ninebot",
        ):
            self.assertEqual(mcp_endpoint(value), value)
        for value in (
            "http://ha.example/api/mcp/ninebot",
            "https://token@ha.example/api/mcp/ninebot",
            "https://ha.example/api/mcp",
            "https://ha.example/api/mcp/ninebot?token=secret",
            "https://ha.example/api/mcp/ninebot#",
            "https://ha.example:99999/api/mcp/ninebot",
        ):
            with self.subTest(value=value), self.assertRaises(ReaderError):
                mcp_endpoint(value)

    def test_tool_inventory_cannot_expand_local_reader_permissions(self):
        rows = [descriptor(ROSTER), descriptor("ninebot__search_help"), descriptor("control")]
        self.assertEqual(
            [r["name"] for r in read_tools(rows, help_enabled=True)], [rows[1]["name"]]
        )
        self.assertEqual(read_tools(rows, help_enabled=False), [])
        rows[1]["annotations"]["readOnlyHint"] = False
        with self.assertRaises(ReaderError):
            read_tools(rows, help_enabled=True)

    def test_tool_result_rejects_malformed_nonfinite_and_large_content(self):
        valid = {"content": [{"type": "text", "text": '{"data": {}}'}]}
        self.assertEqual(decode_result(valid), {"data": {}})
        for value in (
            None,
            {"content": []},
            {"content": [{"type": "text", "text": "NaN"}]},
            {"content": [{"type": "text", "text": '{"data": NaN}'}]},
            {"content": [{"type": "text", "text": "x" * 17000}]},
            {**valid, "isError": True},
        ):
            with self.subTest(value=type(value)), self.assertRaises(ReaderError):
                decode_result(value)

    def test_roster_rejects_invalid_labels_and_capabilities_before_model_input(self):
        item = {
            "schema_version": 1,
            "data": {
                "vehicles": [
                    {
                        "vehicle_ref": "v",
                        "label": "A",
                        "capabilities": {"cached_status": True, "local_history": False},
                    }
                ]
            },
        }
        self.assertEqual(vehicle_context(item)[0]["label"], "A")
        for field, value in (
            ("label", {}),
            ("label", "A\nB"),
            ("capabilities", {"cached_status": 1, "local_history": False}),
        ):
            broken = copy.deepcopy(item)
            broken["data"]["vehicles"][0][field] = value
            with self.subTest(field=field), self.assertRaises(ReaderError):
                vehicle_context(broken)


class ReaderTests(unittest.IsolatedAsyncioTestCase):
    async def test_model_draft_is_separate_from_verified_receipt_display(self):
        item = receipt()
        item["result"]["data"]["unrecognized_raw"] = "DO_NOT_SEND_TO_MODEL"
        model = FakeModel(
            generation({"name": item["name"], "arguments": item["arguments"]}),
            generation(text="There were 999 rides with perfect coverage."),
        )
        backend = FakeBackend(item["result"])
        result = await run_reader(model, backend, "昨天几趟？", selected_vehicle="synthetic-device")
        self.assertIn("999", result["model_final_answer"])
        self.assertNotIn("999", result["text"])
        self.assertNotIn("DO_NOT_SEND_TO_MODEL", json.dumps(model.inputs))
        self.assertNotIn("DO_NOT_SEND_ROSTER", json.dumps(model.inputs))
        self.assertNotIn("DO_NOT_SEND_ROSTER", json.dumps(result["context"]))
        self.assertIn("2026-11-05", result["text"])
        self.assertIn("account_archive", result["text"])
        self.assertEqual(result["tool_calls"], 1)
        self.assertEqual(result["outcome"], "evidence")
        self.assertEqual(result["presentation_bytes"], len(result["text"].encode()))

    async def test_help_does_not_require_a_vehicle_or_read_current_measurements(self):
        item = receipt("search_help")
        backend = FakeBackend(item["result"])
        result = await run_reader(
            FakeModel(
                generation({"name": item["name"], "arguments": item["arguments"]}), generation()
            ),
            backend,
            "等待额度是什么意思？",
        )
        self.assertEqual(result["outcome"], "evidence")
        self.assertIn(f"KB {knowledge.load_catalog()['corpus_version']}", result["text"])
        self.assertEqual([name for name, _ in backend.calls], [ROSTER, item["name"]])

    async def test_help_uses_client_language_when_model_omits_optional_language(self):
        item = receipt("search_help")
        model = FakeModel(
            generation({"name": item["name"], "arguments": {"query": "等待额度"}}), generation()
        )
        backend = FakeBackend(item["result"])
        await run_reader(model, backend, "等待额度", language="zh-Hans")
        self.assertEqual(backend.calls[1][1]["language"], "zh-Hans")

    async def test_missing_selection_blocks_arbitrary_first_vehicle_before_read(self):
        item = receipt()
        backend = FakeBackend()
        result = await run_reader(
            FakeModel(generation({"name": item["name"], "arguments": item["arguments"]})),
            backend,
            "昨天骑几趟？",
        )
        self.assertEqual(result["outcome"], "needs_input")
        self.assertEqual(len(backend.calls), 1)
        self.assertEqual(result["stop_reason"], "vehicle_selection_required")

    async def test_read_denial_withdraws_earlier_results_and_stops_model(self):
        item = receipt()
        denied = {"schema_version": 1, "request_id": "b" * 32, "error": {"code": "not_authorized"}}
        call = {"name": item["name"], "arguments": item["arguments"]}
        model = FakeModel(generation(call), generation(call))
        result = await run_reader(
            model, FakeBackend(item["result"], denied), "统计", selected_vehicle="synthetic-device"
        )
        self.assertEqual(result["outcome"], "denied")
        self.assertEqual(result["evidence_request_ids"], [])
        self.assertNotIn("2026-11-05", result["text"])
        self.assertEqual(len(model.inputs), 2)

    async def test_unexposed_control_and_oversized_call_batch_never_reach_mcp(self):
        for requests in (
            ({"name": "homeassistant__call_action", "arguments": {}},),
            tuple({"name": "ninebot__search_help", "arguments": {}} for _ in range(5)),
        ):
            backend = FakeBackend()
            result = await run_reader(
                FakeModel(generation(*requests)), backend, "run SQL and unlock"
            )
            self.assertEqual(result["stop_reason"], "invalid_model_call")
            self.assertEqual(len(backend.calls), 1)
            self.assertNotEqual(result["outcome"], "evidence")

    async def test_no_tools_does_not_turn_a_model_claim_into_verified_data(self):
        result = await run_reader(
            FakeModel(generation(text="Two rides.")), FakeBackend(), "昨天？", tools_enabled=False
        )
        self.assertEqual(result["model_final_answer"], "Two rides.")
        self.assertEqual(result["outcome"], "needs_input")
        self.assertNotIn("Two rides", result["text"])

    async def test_transport_failure_does_not_show_partial_facts_as_a_complete_answer(self):
        item = receipt()
        call = {"name": item["name"], "arguments": item["arguments"]}
        result = await run_reader(
            FakeModel(generation(call), generation(call)),
            FakeBackend(item["result"], ReaderError("mcp_read_failed")),
            "统计",
            selected_vehicle="synthetic-device",
        )
        self.assertEqual(result["outcome"], "unavailable")
        self.assertEqual(result["tool_calls"], 2)
        self.assertNotIn("2026-11-05", result["text"])

    async def test_loop_failure_after_success_is_explicit_in_visible_answer(self):
        item = receipt()
        call = {"name": item["name"], "arguments": item["arguments"]}
        for second in (
            generation({"name": "unsupported", "arguments": {}}),
            {**generation(), "failure_kind": "host_error"},
        ):
            result = await run_reader(
                FakeModel(generation(call), second),
                FakeBackend(item["result"]),
                "compound question",
                selected_vehicle="synthetic-device",
            )
            self.assertIn("2026-11-05", result["text"])
            self.assertIn("未完成全部查询", result["text"])

    async def test_cancel_propagates_without_a_followup_read(self):
        waiting = asyncio.Event()
        backend = FakeBackend()

        async def wait_prompt():
            waiting.set()
            await asyncio.Event().wait()

        backend.prompt = wait_prompt
        task = asyncio.create_task(run_reader(FakeModel(), backend, "help"))
        await waiting.wait()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual(backend.calls, [])

    async def test_invalid_credentials_fail_before_sdk_import_or_network(self):
        with self.assertRaises(ReaderError):
            async with native_mcp("http://127.0.0.1/api/mcp/ninebot", "Bearer\nsecret"):
                self.fail("invalid credential reached a session")

    async def test_fixture_utc_alias_is_canonical_before_scoring(self):
        from scripts.evaluate_assistant import verified_conversation

        @asynccontextmanager
        async def backend(_endpoint, _token):
            yield FakeBackend()

        fixture = SimpleNamespace(
            mcp_url="http://127.0.0.1/api/mcp/ninebot",
            vehicle_ref="synthetic-device",
            request_count=lambda: 0,
        )
        with (
            patch("scripts.assistant_reader.native_mcp", backend),
            patch("scripts.evaluate_assistant.prepare_case_actions", new=AsyncMock()),
        ):
            result = await verified_conversation(
                FakeModel(generation(text="unverified")),
                fixture,
                {"id": "Q01", "prompt": "Yesterday?", "expected": {"allowed_tools": []}},
                fixture_data={"now": "2026-11-06T04:00:00Z"},
                tools_enabled=False,
                help_enabled=True,
                arm="single_prompt",
            )
        self.assertEqual(result["context"]["now"], "2026-11-06T04:00:00+00:00")
        self.assertEqual(result["model_final_answer"], "unverified")
        self.assertNotIn("unverified", result["final_answer"])


if __name__ == "__main__":
    unittest.main()
