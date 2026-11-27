"""Pure consent, migration and rollback checks."""

from dataclasses import replace

import pytest

from custom_components.ninebot.assistant_access import (
    ALL_READS,
    CONF_ASSISTANT,
    CONF_ASSISTANT_ACCESS,
    CONF_ASSISTANT_TASKS,
    CONF_ASSISTANT_VEHICLES,
    READ_STATISTICS,
    READ_STATUS,
    TASK_OFF,
    TASK_PREVIEW,
    TASK_REQUEST,
    AssistantAccess,
    VehicleAccess,
    normalize_assistant_options,
    parse_assistant_access,
    with_assistant_access,
)


def test_legacy_options_keep_selected_reads_and_durable_tasks_pending_confirmation():
    legacy = {
        CONF_ASSISTANT: True,
        CONF_ASSISTANT_VEHICLES: ["B", "A", "A", None, ""],
        CONF_ASSISTANT_TASKS: True,
        "unrelated": {"selection": ["value"]},
    }
    access = parse_assistant_access(legacy)
    assert access.enabled
    assert access.vehicles == {"A": VehicleAccess(ALL_READS), "B": VehicleAccess(ALL_READS)}
    assert access.pending_confirmation
    assert access.legacy_task_vehicles == access.durable_task_vehicles == {"A", "B"}

    options = normalize_assistant_options(legacy)
    assert normalize_assistant_options(options) == options
    assert parse_assistant_access(options) == access
    assert options[CONF_ASSISTANT_ACCESS]["version"] == 1
    assert options[CONF_ASSISTANT] is True
    assert options[CONF_ASSISTANT_VEHICLES] == ["A", "B"]
    assert options[CONF_ASSISTANT_TASKS] is False
    assert options["unrelated"] == legacy["unrelated"]


@pytest.mark.parametrize(
    "options",
    [
        {},
        {CONF_ASSISTANT_TASKS: True, CONF_ASSISTANT_VEHICLES: ["A"]},
        {CONF_ASSISTANT: 1, CONF_ASSISTANT_VEHICLES: ["A"], CONF_ASSISTANT_TASKS: True},
        {CONF_ASSISTANT: True, CONF_ASSISTANT_TASKS: True},
        {CONF_ASSISTANT: True, CONF_ASSISTANT_TASKS: True, CONF_ASSISTANT_VEHICLES: "A"},
        {CONF_ASSISTANT: True, CONF_ASSISTANT_TASKS: True, CONF_ASSISTANT_VEHICLES: [False, ""]},
    ],
)
def test_incomplete_legacy_options_do_not_grant_vehicle_access(options):
    access = parse_assistant_access(options)
    assert not access.vehicles
    assert not access.pending_confirmation
    assert not access.durable_task_vehicles


def test_legacy_reads_require_a_separate_explicit_task_opt_in():
    options = {CONF_ASSISTANT: True, CONF_ASSISTANT_VEHICLES: ["A"]}
    access = parse_assistant_access(options)
    assert access.vehicles["A"] == VehicleAccess(ALL_READS, TASK_OFF)
    assert not access.pending_confirmation
    assert not access.durable_task_vehicles

    disabled = parse_assistant_access(
        {**options, CONF_ASSISTANT: False, CONF_ASSISTANT_TASKS: True}
    )
    assert not disabled.enabled
    assert disabled.vehicles == access.vehicles
    assert not disabled.pending_confirmation
    assert not disabled.durable_task_vehicles


@pytest.mark.parametrize("future", (None, {"version": 2}, {"version": True}, {"version": "1"}))
def test_unknown_access_version_disables_legacy_projection_without_overwriting_data(future):
    options = {
        CONF_ASSISTANT_ACCESS: future,
        CONF_ASSISTANT: True,
        CONF_ASSISTANT_VEHICLES: ["A"],
        CONF_ASSISTANT_TASKS: True,
    }
    assert parse_assistant_access(options) == AssistantAccess()
    safe = normalize_assistant_options(options)
    assert safe[CONF_ASSISTANT_ACCESS] == future
    assert safe[CONF_ASSISTANT] is False
    assert safe[CONF_ASSISTANT_TASKS] is False
    assert safe[CONF_ASSISTANT_VEHICLES] == []
    assert normalize_assistant_options(safe) == safe


def test_versioned_policy_reads_are_independent_and_unknown_values_cannot_grant_access():
    options = {
        CONF_ASSISTANT_ACCESS: {
            "version": 1,
            "enabled": True,
            "vehicles": {
                "A": {"reads": ["status", "raw", None], "tasks": "admin"},
                "B": {"reads": ["statistics"], "tasks": "preview"},
                "C": {"reads": "status", "tasks": "request"},
                "D": {"reads": ["trips", "sync"], "tasks": "off"},
                "E": [],
                "": {"reads": ["status"], "tasks": "request"},
            },
        }
    }
    access = parse_assistant_access(options)
    assert access.vehicles == {
        "A": VehicleAccess(frozenset({READ_STATUS})),
        "B": VehicleAccess(frozenset({READ_STATISTICS}), TASK_PREVIEW),
        "C": VehicleAccess(tasks=TASK_REQUEST),
        "D": VehicleAccess(frozenset({"trips", "sync"})),
    }
    assert access.durable_task_vehicles == {"C"}
    assert not normalize_assistant_options(options)[CONF_ASSISTANT]


def test_pending_tasks_use_only_explicit_legacy_vehicle_scope():
    raw = {
        "version": 1,
        "enabled": True,
        "pending_confirmation": True,
        "vehicles": {
            "A": {"reads": ["status"], "tasks": "request"},
            "B": {"reads": [], "tasks": "preview"},
        },
        "legacy_task_vehicles": ["A", "not-selected"],
    }
    options = {CONF_ASSISTANT_ACCESS: raw}
    access = parse_assistant_access(options)
    assert access.pending_confirmation
    assert {vehicle.tasks for vehicle in access.vehicles.values()} == {TASK_OFF}
    assert access.legacy_task_vehicles == access.durable_task_vehicles == {"A"}
    without_scope = parse_assistant_access(
        {CONF_ASSISTANT_ACCESS: {**raw, "legacy_task_vehicles": None}}
    )
    assert not without_scope.durable_task_vehicles
    disabled = parse_assistant_access({CONF_ASSISTANT_ACCESS: {**raw, "enabled": False}})
    assert not disabled.pending_confirmation
    assert not disabled.durable_task_vehicles
    assert {vehicle.tasks for vehicle in disabled.vehicles.values()} == {TASK_OFF}


def test_downgrade_projects_only_full_read_vehicles_and_unanimous_task_requests():
    access = AssistantAccess(
        enabled=True,
        vehicles={
            "Full": VehicleAccess(ALL_READS, TASK_REQUEST),
            "Preview": VehicleAccess(ALL_READS, TASK_PREVIEW),
            "Partial": VehicleAccess(frozenset({READ_STATISTICS}), TASK_REQUEST),
        },
    )
    options = with_assistant_access({}, access)
    assert options[CONF_ASSISTANT_VEHICLES] == ["Full", "Preview"]
    assert options[CONF_ASSISTANT_TASKS] is False
    assert parse_assistant_access(options) == access

    compatible = replace(
        access, vehicles={k: v for k, v in access.vehicles.items() if k != "Preview"}
    )
    projected = with_assistant_access({}, compatible)
    assert projected[CONF_ASSISTANT_VEHICLES] == ["Full"]
    assert projected[CONF_ASSISTANT_TASKS] is True
    assert parse_assistant_access(projected) == compatible
    assert (
        parse_assistant_access(
            {**options, CONF_ASSISTANT_VEHICLES: ["Preview", "Full"], "debug_mode": True}
        )
        == access
    )


def test_reupgrade_follows_edited_legacy_permissions_without_expanding_partial_reads():
    access = AssistantAccess(
        enabled=True,
        vehicles={
            "Full": VehicleAccess(ALL_READS, TASK_REQUEST),
            "Partial": VehicleAccess(frozenset({READ_STATISTICS}), TASK_REQUEST),
        },
    )
    options = with_assistant_access({}, access)

    rollback = {**options, CONF_ASSISTANT_TASKS: False}
    reupgraded = parse_assistant_access(normalize_assistant_options(rollback))
    assert reupgraded.vehicles == {"Full": VehicleAccess(ALL_READS)}
    assert not reupgraded.pending_confirmation
    assert not reupgraded.durable_task_vehicles

    # The old form could explicitly add a vehicle excluded by the projection.
    edited = {**options, CONF_ASSISTANT_VEHICLES: ["Partial"]}
    reupgraded = parse_assistant_access(normalize_assistant_options(edited))
    assert reupgraded.vehicles == {"Partial": VehicleAccess(ALL_READS)}
    assert reupgraded.pending_confirmation
    assert reupgraded.durable_task_vehicles == {"Partial"}

    del options[CONF_ASSISTANT]
    assert parse_assistant_access(normalize_assistant_options(options)) == AssistantAccess()
