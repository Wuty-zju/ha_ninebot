"""Model protocol and safe artifact boundaries without a model or HA install."""

import json
import tempfile
import unittest
from pathlib import Path

from scripts.evaluate_assistant import (
    MAX_REPORT_BYTES,
    InvalidReply,
    parse_calls,
    safe_result,
    visible_text,
    write_report,
)


class ModelProtocolTests(unittest.TestCase):
    def test_only_the_visible_final_channel_is_retained(self):
        self.assertEqual(visible_text("<think>private</think>answer"), "answer")
        self.assertEqual(visible_text("<think>unfinished"), "")
        self.assertEqual(visible_text("hidden<think>nested</think>hidden</think>answer"), "answer")

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
