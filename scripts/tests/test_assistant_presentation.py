"""Receipt display contracts use synthetic facts and the real bundled help retriever."""

import copy
import json
import unittest

from scripts.assistant_presentation import PresentationError, render_answer
from scripts.check_assistant_knowledge import knowledge


def receipt(name="get_statistics"):
    payload = json.loads("""{
      "schema_version": 1, "request_id": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
      "vehicle_ref": "synthetic-device",
      "query": {"period": "yesterday", "start_date": "2026-11-05", "end_date": "2026-11-05",
                "business_timezone": "Asia/Shanghai", "as_of": "2026-11-06T04:00:00+00:00"},
      "data": {"distance_km": 20.0, "ride_count": 2, "duration_s": 3600.0, "energy_wh": 600.0,
               "energy_intensity_wh_per_km": 30.0,
               "units": {"distance_km": "km", "ride_count": null, "duration_s": "s",
                         "energy_wh": "Wh", "energy_intensity_wh_per_km": "Wh/km"}},
      "quality": {"metrics": {}, "received_at": "2026-11-06T04:00:00+00:00", "period_closed": true,
                  "ride_window_complete": true, "observed_after_period_end": null,
                  "adjacent_received_at": null, "completeness_basis": "upstream_complete"},
      "evidence": {"adapter_contract": "ninebot-assistant-v1", "access_mode": "local_only",
                   "source_kind": "account_archive", "coverage_basis": "business_day_projection",
                   "distance_basis": "server_daily_chart",
                   "ride_date_assignment": "whole_ride_end_business_date",
                   "energy_intensity_basis": "returned_unique_rides"}, "warnings": []
    }""")
    payload["quality"]["metrics"] = {
        key: {"status": "available", "reason": "available"}
        for key in payload["data"]
        if key != "units"
    }
    args = {"vehicle_ref": "synthetic-device", "period": "yesterday"}
    if name == "search_help":
        args = {"query": "等待额度", "language": "zh-Hans"}
        payload["data"] = copy.deepcopy(knowledge.search_help("等待额度", "zh-Hans", "2.1.6"))
        payload["quality"] = {}
        payload["evidence"]["source_kind"] = "bundled_public_help"
    return {"name": "ninebot__" + name, "arguments": args, "result": payload}


class PresentationTests(unittest.TestCase):
    def test_all_bundled_checklists_fit_the_reader_without_granting_tools(self):
        seen = set()
        for entry in knowledge.load_catalog()["entries"]:
            for language in ("en", "zh-Hans"):
                item = receipt("search_help")
                args = {
                    "query": entry["translations"][language]["title"],
                    "language": language,
                    "include_procedures": True,
                }
                item["arguments"] = args
                item["result"]["data"] = knowledge.search_help(
                    args["query"], language, "2.1.6", include_procedures=True
                )
                answer = render_answer([item], language)
                self.assertLessEqual(len(json.dumps(answer, ensure_ascii=False).encode()), 16384)
                seen.update(row["id"] for row in item["result"]["data"]["procedures"])
        self.assertEqual(seen, {f"SK{i:02}" for i in range(1, 11)})

    def test_statistics_date_value_coverage_observation_source_in_both_languages(self):
        for language in ("zh-Hans", "en"):
            answer = render_answer([receipt()], language)
            for text in (
                "2026-11-05",
                "Asia/Shanghai",
                "2",
                "3600.0 s",
                "status=available",
                "ride_window_complete: true",
                "2026-11-06T04:00:00+00:00",
                "account_archive",
            ):
                self.assertIn(text, answer["text"])
            self.assertEqual(answer["outcome"], "evidence")
            self.assertEqual(answer["evidence_request_ids"], ["a" * 32])

    def test_null_zero_partial_and_missing_observation_are_not_replaced(self):
        item = receipt()
        payload = item["result"]
        payload["data"]["ride_count"] = 0
        payload["data"]["energy_wh"] = None
        payload["quality"]["metrics"]["energy_wh"] = {
            "status": "missing",
            "reason": "missing_metric",
        }
        payload["quality"]["ride_window_complete"] = False
        payload["quality"]["received_at"] = None
        text = render_answer([item], "en")["text"]
        for value in (
            "Ride count: 0",
            "Energy: null",
            "ride_window_complete: false",
            "not recorded",
        ):
            self.assertIn(value, text)
        self.assertNotIn(payload["query"]["as_of"], text)

    def test_help_uses_validated_source_not_peer_citation_and_does_not_require_vehicle(self):
        item = receipt("search_help")
        item["result"]["data"]["matches"][0]["citation"] = "https://evil.example"
        answer = render_answer([item])
        self.assertIn(f"KB {knowledge.load_catalog()['corpus_version']}", answer["text"])
        self.assertIn("github.com/Wuty-zju/ha_ninebot/blob/", answer["text"])
        self.assertNotIn("evil.example", answer["text"])
        item["arguments"]["include_procedures"] = True
        item["result"]["data"] = knowledge.search_help(
            "等待额度", "zh-Hans", "2.1.6", include_procedures=True
        )
        text = render_answer([item])["text"]
        self.assertIn("辅助清单，不执行操作", text)
        self.assertIn("required_inputs:", text)
        self.assertIn("steps:", text)
        self.assertIn("stop:", text)
        self.assertIn("max_calls: 3", text)

    def test_help_accepts_known_retrieval_revisions_and_rejects_unknown_ones(self):
        item = receipt("search_help")
        for revision in ("lexical-v1", "lexical-v2"):
            with self.subTest(revision=revision):
                item["result"]["data"]["retrieval_contract"] = revision
                self.assertIn(revision, render_answer([item])["text"])
        item["result"]["data"]["retrieval_contract"] = "lexical-v99"
        with self.assertRaises(PresentationError):
            render_answer([item])

    def test_denial_withdraws_all_success_and_mixed_failure_is_visible(self):
        failure = {
            "name": "ninebot__get_statistics",
            "arguments": {},
            "result": {"schema_version": 1, "request_id": "b" * 32, "error": {"code": "timeout"}},
        }
        self.assertIn("timeout", render_answer([receipt(), failure])["text"])
        failure["result"]["error"]["code"] = "not_authorized"
        result = render_answer([receipt(), failure])
        self.assertEqual(result["outcome"], "denied")
        self.assertEqual(result["evidence_request_ids"], [])
        self.assertNotIn("2026-11-05", result["text"])
        self.assertEqual(render_answer([])["outcome"], "needs_input")

    def test_unknown_private_fields_are_never_dumped(self):
        item = receipt()
        item["result"]["data"]["raw_payload"] = {"gps": "PRIVATE"}
        self.assertNotIn("PRIVATE", render_answer([item])["text"])

    def test_sync_pacing_and_unknown_coverage_do_not_invent_remaining_quota(self):
        item = receipt("get_sync_health")
        item["result"]["data"] = dict.fromkeys(
            "next_request_at retry_at last_success last_completed scanned_months total_months "
            "verified_months gap_months scan_progress detail_progress discovery_complete paused "
            "background_enabled automatic_sync_enabled".split()
        )
        item["result"]["data"].update(
            state="waiting_budget",
            phase="preserving",
            request_policy={
                "basis": "paced_account_requests",
                "configured_requests_per_hour": 60,
                "minimum_interval_s": 60,
            },
        )
        item["result"]["quality"] = {"upstream_history_complete": "unverified"}
        text = render_answer([item], "en")["text"]
        self.assertIn("waiting_budget", text)
        self.assertIn("configured_requests_per_hour: 60", text)
        self.assertIn("upstream_history_complete: unverified", text)
        self.assertNotIn("remaining", text)

    def test_no_match_and_output_limits_fail_without_free_prose(self):
        item = receipt("search_help")
        item["result"]["data"] = knowledge.search_help("zzzz_unrelated", "en", "2.1.6")
        self.assertEqual(render_answer([item])["outcome"], "unavailable")
        with self.assertRaises(PresentationError):
            render_answer([receipt()] * 5)
        item = receipt("search_help")
        item["result"]["data"]["matches"] = item["result"]["data"]["matches"][:1]
        item["result"]["data"]["matches"][0]["text"] = "&" * 1000
        with self.assertRaises(PresentationError):
            render_answer([item] * 4)

    def test_malformed_mismatched_unbounded_and_unsupported_inputs_fail_closed(self):
        changes = [
            lambda x: x.update(name="ninebot__get_vehicles"),
            lambda x: x["result"].update(schema_version=True),
            lambda x: x["result"].update(vehicle_ref="another"),
            lambda x: x["arguments"].update(period="today"),
            lambda x: x["result"]["data"].update(ride_count=True),
            lambda x: x["result"]["data"].update(energy_wh=float("nan")),
            lambda x: x["result"].update(raw="x" * 17000),
        ]
        for change in changes:
            item = copy.deepcopy(receipt())
            change(item)
            with (
                self.subTest(change=change),
                self.assertRaisesRegex(PresentationError, "^unsupported_receipt$"),
            ):
                render_answer([item])
        item = receipt("search_help")
        item["result"]["data"]["matches"][0]["source"]["url"] = "https://evil.example"
        with self.assertRaises(PresentationError):
            render_answer([item])


if __name__ == "__main__":
    unittest.main()
