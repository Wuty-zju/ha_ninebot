"""Per-call Assistant consent and identity, independent of model instructions."""

from dataclasses import dataclass

from homeassistant.core import Context, HomeAssistant, ServiceCall
from homeassistant.exceptions import HomeAssistantError, Unauthorized

from .assistant import current_access
from .assistant_access import AssistantAccess
from .assistant_catalog import BY_KEY
from .assistant_errors import AssistantError as AssistantError
from .backup import backup_active
from .const import DOMAIN
from .runtime import NinebotConfigEntry, RuntimeData
from .services import async_check_history_access, resolve_vehicle


async def async_require_admin(hass: HomeAssistant, context: Context | None) -> str:
    """Native API selection alone does not check active users or cached calls."""
    if (
        context is None
        or context.user_id is None
        or (user := await hass.auth.async_get_user(context.user_id)) is None
        or not user.is_active
        or not user.is_admin
    ):
        raise Unauthorized(context=context)
    return context.user_id


# Internal access to existing tickets under their original, durable grant.
REVIEW_TASKS = "review_existing_tasks"


def operation_allowed(access: AssistantAccess, sn: str, operation: str | None) -> bool:
    if not access.enabled or (grant := access.vehicles.get(sn)) is None:
        return False
    if operation == REVIEW_TASKS:
        return sn in access.durable_task_vehicles
    if operation is None:
        return bool(grant.reads) or grant.tasks != "off" or sn in access.durable_task_vehicles
    spec = BY_KEY.get(operation)
    return spec is not None and spec.operation.allowed(grant)


@dataclass(frozen=True)
class AssistantScope:
    """Captured ownership must still hold after every awaited read."""

    context: Context
    vehicle_ref: str
    entry: NinebotConfigEntry
    sn: str
    runtime: RuntimeData
    generation: int
    ownership: int
    revision: int = 0
    operation: str | None = None

    def assert_current(self, hass: HomeAssistant) -> None:
        try:
            entry, sn = resolve_vehicle(hass, self.vehicle_ref, require_live=False)
        except HomeAssistantError as err:
            raise AssistantError("not_authorized") from err
        co = self.runtime.coordinator
        if (
            entry is not self.entry
            or sn != self.sn
            or entry.runtime_data is not self.runtime
            or (
                self.operation != REVIEW_TASKS
                and self.runtime.assistant_policy_revision != self.revision
            )
            or not operation_allowed(current_access(entry), sn, self.operation)
            or co._generation != self.generation
            or co._ownership.get(sn, 0) != self.ownership
        ):
            raise AssistantError("not_authorized")

    def assert_task_current(self, hass: HomeAssistant) -> None:
        self.assert_current(hass)
        # Public read/submit scopes were checked above. Internal review and
        # execution require the still-valid authority for this vehicle's tickets.
        spec = BY_KEY.get(self.operation or "")
        if (
            (
                (spec is None or not spec.task)
                and self.sn not in current_access(self.entry).durable_task_vehicles
            )
            or self.runtime.assistant_task_blocks
            or self.runtime.coordinator._stopping
            or backup_active(hass)
        ):
            raise AssistantError("not_authorized")

    async def async_validate(self, hass: HomeAssistant) -> None:
        try:
            # Retain the existing HA history permission contract as well as
            # the separate per-account AI consent. Never create a new Context.
            await async_check_history_access(
                hass,
                ServiceCall(
                    hass,
                    DOMAIN,
                    "get_recorded_trips",
                    {"device_id": self.vehicle_ref},
                    context=self.context,
                ),
                self.entry.entry_id,
            )
            await async_require_admin(hass, self.context)
        except Unauthorized as err:
            raise AssistantError("not_authorized") from err
        self.assert_current(hass)


async def async_authorize(
    hass: HomeAssistant, context: Context | None, vehicle_ref: str, *, operation: str | None = None
) -> AssistantScope:
    """Resolve the authenticated target without exposing account identifiers."""
    try:
        await async_require_admin(hass, context)
        entry, sn = resolve_vehicle(hass, vehicle_ref, require_live=False)
    except HomeAssistantError as err:
        raise AssistantError("not_authorized") from err
    if context is None or not operation_allowed(current_access(entry), sn, operation):
        raise AssistantError("not_authorized")
    runtime = entry.runtime_data
    scope = AssistantScope(
        context,
        vehicle_ref,
        entry,
        sn,
        runtime,
        runtime.coordinator._generation,
        runtime.coordinator._ownership.get(sn, 0),
        runtime.assistant_policy_revision,
        operation,
    )
    await scope.async_validate(hass)
    return scope
