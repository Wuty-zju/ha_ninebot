"""Pure finite-window and local-admission contracts; all inputs synthetic."""

import unittest

from custom_components.ninebot.polling_activity import PollBudget, VehicleActivity
from custom_components.ninebot.polling_policy import PollingPolicy


class ActivityTests(unittest.TestCase):
    def setUp(self):
        self.policy = PollingPolicy()
        self.scene = VehicleActivity()

    def observe(self, now, revision, *, charging=False, active=False, locked=True, **kwargs):
        return self.scene.observe(
            self.policy,
            now=now,
            observed_at=now,
            revision=revision,
            charging=charging,
            active=active,
            locked=locked,
            **kwargs,
        )

    def test_charge_activity_and_combined_states_have_distinct_group_targets(self):
        self.observe(100, 1, charging=True)
        self.assertEqual(
            [self.scene.interval(self.policy, g, 100) for g in ("status", "battery", "travel")],
            [15, 30, 600],
        )
        self.observe(101, 2, charging=True, active=True)
        self.assertEqual(
            [self.scene.interval(self.policy, g, 101) for g in ("status", "battery", "travel")],
            [10, 30, 120],
        )
        self.assertEqual(self.policy.max_age("battery"), 1800)

    def test_charge_edges_open_one_non_extending_window_and_request_one_battery_read(self):
        self.observe(100, 1)
        self.assertEqual(self.observe(110, 2, charging=True), {"battery"})
        self.observe(120, 3, charging=True)
        self.assertEqual(self.observe(130, 4, charging=False), frozenset())
        self.assertEqual(self.scene.leases["charge_transition"], 230)
        self.assertEqual(self.scene.interval(self.policy, "status", 229), 10)
        self.assertEqual(self.scene.interval(self.policy, "battery", 229), 15)
        self.assertEqual(self.scene.interval(self.policy, "status", 230), 60)

    def test_startup_unlock_is_baseline_and_only_a_real_edge_starts_a_window(self):
        self.observe(100, 1, locked=False)
        self.assertEqual(self.scene.reasons(self.policy, 100), ())
        self.observe(101, 2)
        self.assertEqual(self.observe(102, 3, locked=False), {"battery"})
        self.observe(110, 4, locked=False)
        self.assertEqual(self.scene.leases["recent_unlock"], 162)
        self.assertEqual(self.scene.interval(self.policy, "status", 161), 5)
        self.assertEqual(self.scene.interval(self.policy, "status", 162), 60)

    def test_own_control_does_not_create_a_second_unlock_observer(self):
        self.observe(100, 1)
        self.assertEqual(self.observe(101, 2, locked=False, own_control=True), frozenset())
        self.assertNotIn("recent_unlock", self.scene.leases)

    def test_replayed_or_late_status_does_not_renew_activity(self):
        self.observe(100, 3, active=True)
        self.observe(200, 3, active=True)
        self.scene.observe(
            self.policy,
            now=250,
            observed_at=90,
            revision=4,
            charging=False,
            active=True,
            locked=False,
        )
        self.assertEqual(self.scene.observed_at, 100)
        self.assertEqual(self.scene.interval(self.policy, "status", 281), 60)
        self.assertEqual(self.scene.revision, 3)

    def test_gaps_and_missing_flags_do_not_synthesize_a_stop(self):
        self.observe(100, 1, active=True)
        self.observe(281, 2)
        self.assertEqual(self.scene.closing, ())
        self.observe(290, 3, active=True)
        self.observe(291, 4, active=None)
        self.observe(292, 5)
        self.assertEqual(self.scene.closing, ())

    def test_stop_followup_is_bounded_deduplicated_and_consumes_overdue_slots_once(self):
        self.observe(100, 1, active=True)
        self.observe(110, 2)
        self.assertEqual(self.scene.closing, (125, 155, 200, 290, 410, 710))
        self.assertEqual(self.scene.interval(self.policy, "status", 111), 10)
        self.observe(120, 3, active=True)
        self.observe(130, 4)
        self.assertEqual(self.scene.stopped_at, 110)
        self.scene.travel_observed(100, 160)  # An older cached payload proves no follow-up.
        self.assertEqual(len(self.scene.closing), 6)
        self.scene.travel_observed(160, 160)
        self.assertEqual(self.scene.closing, (200, 290, 410, 710))
        self.scene.travel_observed(711, 711)
        self.assertIsNone(self.scene.closing_deadline(711))

    def test_numeric_options_use_balanced_activity(self):
        policy = PollingPolicy.from_options({"poll_interval": 37})
        self.scene.observe(
            policy, now=100, observed_at=100, revision=1, charging=True, active=True, locked=False
        )
        self.assertEqual(self.scene.interval(policy, "status", 100), 10)
        self.assertEqual(self.scene.interval(policy, "battery", 100), 30)

    def test_diagnostics_contains_only_reasons_and_bounded_counts(self):
        self.observe(100, 1)
        self.observe(101, 2, locked=False)
        self.assertEqual(
            set(self.scene.diagnostics(self.policy, 102)),
            {"reasons", "leases_remaining_seconds", "closing_reads_remaining"},
        )


class AdmissionTests(unittest.TestCase):
    def test_burst_refill_and_no_catchup_credit(self):
        budget = PollBudget(1, 3)
        for _ in range(3):
            budget.take(100)
        self.assertEqual(budget.delay(100), 1)
        with self.assertRaises(ValueError):
            budget.take(100)
        self.assertEqual(budget.delay(100.5), 0.5)
        budget.take(101)
        self.assertEqual(budget.delay(1000), 0)
        self.assertEqual(budget.tokens, 3)

    def test_two_accounts_share_a_global_periodic_budget(self):
        shared, one, two = PollBudget(2, 6), PollBudget(1, 3), PollBudget(1, 3)
        for account in (one, two):
            for _ in range(3):
                self.assertEqual(max(account.delay(100), shared.delay(100)), 0)
                account.take(100)
                shared.take(100)
        self.assertEqual(shared.delay(100), 0.5)
        self.assertEqual(one.delay(100), 1)
        self.assertEqual(two.delay(100), 1)


if __name__ == "__main__":
    unittest.main()
