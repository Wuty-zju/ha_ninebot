"""Native account and vehicle consent forms for Ninebot Assistant."""

from dataclasses import replace
from html import escape
from typing import Any, cast

from homeassistant import config_entries
from homeassistant.config_entries import ConfigFlowResult
from homeassistant.helpers.selector import (
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
)

from .assistant_access import (
    READ_SCOPES,
    TASK_LEVELS,
    TASK_OFF,
    AssistantAccess,
    TaskAccess,
    VehicleAccess,
    assistant_access_editable,
    parse_assistant_access,
    with_assistant_access,
)
from .compat import validation as vol


class AssistantOptionsMixin(config_entries.OptionsFlow):
    """Stage per-vehicle settings before the administrator saves the account."""

    _original_access: AssistantAccess | None = None
    _draft_access: AssistantAccess | None = None
    _assistant_vehicle: str | None = None
    _review_tasks: bool = False

    def _updated_options(self) -> dict[str, Any]:
        raise NotImplementedError

    def _assistant_vehicle_choices(self, access: AssistantAccess) -> dict[str, str]:
        runtime = getattr(self.config_entry, "runtime_data", None)
        snapshots = runtime.coordinator.data if runtime else {}
        serials = dict.fromkeys(sn for sn, snapshot in snapshots.items() if snapshot.present)
        serials.update(dict.fromkeys(access.vehicles))
        if self._original_access:
            serials.update(dict.fromkeys(self._original_access.vehicles))
        return {
            sn: f"{snapshot.profile.name} · {sn[-6:]}" if (snapshot := snapshots.get(sn)) else sn
            for sn in serials
        }

    async def async_step_assistant_access(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if self._draft_access is None:
            options = self.config_entry.options
            if not assistant_access_editable(options):
                return self.async_abort(reason="assistant_access_version")
            self._original_access = self._draft_access = parse_assistant_access(options)
        draft = self._draft_access
        choices = self._assistant_vehicle_choices(draft)
        errors = {}
        if user_input is not None:
            sn = user_input.get("assistant_vehicle")
            if sn is not None and sn not in choices:
                errors["assistant_vehicle"] = "assistant_vehicle_unavailable"
            else:
                self._draft_access = replace(draft, enabled=user_input["assistant_enabled"])
                self._review_tasks = user_input.get("review_assistant_tasks") is True
                if sn is not None:
                    self._assistant_vehicle = sn
                    return await self.async_step_assistant_vehicle()
                return await self._async_finish_assistant()
        schema: dict[Any, Any] = {vol.Required("assistant_enabled", default=draft.enabled): bool}
        if choices:
            schema[vol.Optional("assistant_vehicle")] = SelectSelector(
                SelectSelectorConfig(
                    options=[
                        SelectOptionDict(value=sn, label=label) for sn, label in choices.items()
                    ],
                    mode=SelectSelectorMode.DROPDOWN,
                )
            )
        if draft.pending_confirmation:
            schema[vol.Optional("review_assistant_tasks", default=self._review_tasks)] = bool
        return self.async_show_form(
            step_id="assistant_access", errors=errors, data_schema=vol.Schema(schema)
        )

    async def async_step_assistant_vehicle(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        draft = self._draft_access
        sn = self._assistant_vehicle
        assert draft is not None and sn is not None
        choices = self._assistant_vehicle_choices(draft)
        if sn not in choices:
            return self.async_abort(reason="assistant_vehicle_unavailable")
        if user_input is not None:
            reads = frozenset(
                scope for scope in READ_SCOPES if scope in user_input["assistant_reads"]
            )
            tasks = cast(TaskAccess, user_input["assistant_task_access"])
            vehicle = VehicleAccess(reads, tasks)
            vehicles = dict(draft.vehicles)
            if vehicle != VehicleAccess() or sn in draft.legacy_task_vehicles:
                vehicles[sn] = vehicle
            else:
                vehicles.pop(sn, None)
            self._draft_access = replace(draft, vehicles=vehicles)
            if user_input.get("edit_another_assistant_vehicle") is True:
                return await self.async_step_assistant_access()
            return await self._async_finish_assistant()
        selected = draft.vehicles.get(sn, VehicleAccess())
        schema: dict[Any, Any] = {
            vol.Required(
                "assistant_reads",
                default=[scope for scope in READ_SCOPES if scope in selected.reads],
            ): SelectSelector(
                SelectSelectorConfig(
                    options=list(READ_SCOPES),
                    translation_key="assistant_reads",
                    mode=SelectSelectorMode.LIST,
                    multiple=True,
                )
            ),
            vol.Required("assistant_task_access", default=selected.tasks): SelectSelector(
                SelectSelectorConfig(
                    options=list(TASK_LEVELS),
                    translation_key="assistant_task_access",
                    mode=SelectSelectorMode.LIST,
                )
            ),
        }
        if len(choices) > 1:
            schema[vol.Optional("edit_another_assistant_vehicle", default=False)] = bool
        return self.async_show_form(
            step_id="assistant_vehicle",
            description_placeholders={"vehicle": escape(choices[sn])},
            data_schema=vol.Schema(schema),
        )

    async def _async_finish_assistant(self) -> ConfigFlowResult:
        draft = self._draft_access
        assert draft is not None
        if (
            draft.enabled
            and draft.pending_confirmation
            and (
                self._review_tasks
                or any(vehicle.tasks != TASK_OFF for vehicle in draft.vehicles.values())
            )
        ):
            return await self.async_step_assistant_confirm_tasks()
        return self._save_assistant()

    async def async_step_assistant_confirm_tasks(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            return self._save_assistant(
                confirm_tasks=user_input["assistant_confirmation"] == "apply"
            )
        return self.async_show_form(
            step_id="assistant_confirm_tasks",
            data_schema=vol.Schema(
                {
                    vol.Required("assistant_confirmation", default="later"): SelectSelector(
                        SelectSelectorConfig(
                            options=["later", "apply"],
                            translation_key="assistant_confirmation",
                            mode=SelectSelectorMode.LIST,
                        )
                    )
                }
            ),
        )

    def _save_assistant(self, *, confirm_tasks: bool = False) -> ConfigFlowResult:
        draft = self._draft_access
        assert draft is not None
        current = self.config_entry.options
        if (
            not assistant_access_editable(current)
            or parse_assistant_access(current) != self._original_access
        ):
            return self.async_abort(reason="assistant_access_changed")
        if draft.pending_confirmation:
            if not draft.enabled or not confirm_tasks:
                draft = replace(
                    draft,
                    vehicles={
                        sn: replace(vehicle, tasks=TASK_OFF)
                        for sn, vehicle in draft.vehicles.items()
                        if vehicle.reads or sn in draft.legacy_task_vehicles
                    },
                )
            if not draft.enabled or confirm_tasks:
                draft = replace(draft, pending_confirmation=False, legacy_task_vehicles=frozenset())
        return self.async_create_entry(
            title="", data=with_assistant_access(self._updated_options(), draft)
        )
