"""Pure policy boundaries; no HA setup or network requests."""

import unittest

from custom_components.ninebot.polling_policy import (
    DAY_OBSERVATION_MAX_AGE,
    MONTH_CACHE_SECONDS,
    PRESETS,
    RIDE_COMPLETION_QUIET_SECONDS,
    PollingPolicy,
)


class PolicyTests(unittest.TestCase):
    def test_balanced_default_groups_and_validity(self):
        policy = PollingPolicy.from_options({})
        self.assertEqual(
            [policy.interval(g) for g in ("status", "battery", "travel")], [60, 60, 600]
        )
        self.assertEqual(policy.max_age("status"), 180)
        self.assertEqual(policy.max_age("battery"), 1800)
        self.assertEqual(policy.max_age("travel"), 1800)

    def test_custom_values_keep_their_original_targets(self):
        for value in (30, 37, 120, 300, 3600):
            policy = PollingPolicy.from_options({"poll_interval": value})
            self.assertEqual(policy.name, "legacy")
            self.assertEqual((policy.status, policy.battery, policy.travel), (value, 600, 600))

    def test_explicit_profiles_override_legacy_values_and_bound_every_scene(self):
        previous = None
        for name, policy in PRESETS.items():
            self.assertEqual(
                PollingPolicy.from_options({"polling_profile": name, "poll_interval": 37}), policy
            )
            intervals = tuple(policy.interval(g) for g in ("status", "battery", "travel"))
            if previous:
                self.assertTrue(all(a <= b for a, b in zip(previous, intervals, strict=True)))
            previous = intervals
            for reason in (
                "charging",
                "active",
                "charge_transition",
                "recent_unlock",
                "recent_stop",
            ):
                for group in ("status", "battery", "travel", "profile"):
                    self.assertGreaterEqual(policy.scene_interval(group, reason), 2)
                    self.assertLessEqual(
                        policy.scene_interval(group, reason), policy.interval(group)
                    )
            self.assertEqual(policy.profile, 3600)
        for invalid in ("unknown", None, [], {}):
            self.assertEqual(
                PollingPolicy.from_options({"polling_profile": invalid, "poll_interval": 37}).name,
                "legacy",
            )

    def test_invalid_values_have_a_bounded_fallback(self):
        for value in (None, {}, True, "invalid", float("inf"), float("nan")):
            self.assertEqual(PollingPolicy.from_options({"poll_interval": value}), PollingPolicy())
        self.assertEqual(PollingPolicy.from_options({"poll_interval": 999999}).status, 3600)
        self.assertEqual(PollingPolicy.from_options({"poll_interval": 0}).status, 30)

    def test_faster_targets_do_not_change_cache_or_completion_semantics(self):
        policy = PollingPolicy(status=2, battery=10, travel=30)
        self.assertEqual(policy.max_age("status"), 180)
        self.assertEqual(policy.max_age("battery"), 1800)
        self.assertEqual(policy.max_age("travel"), 1800)
        self.assertEqual(RIDE_COMPLETION_QUIET_SECONDS, 600)
        self.assertEqual(MONTH_CACHE_SECONDS, 600)
        self.assertEqual(DAY_OBSERVATION_MAX_AGE, 1800)


if __name__ == "__main__":
    unittest.main()
