"""Optional native Assistant lifecycle; importing this never loads an LLM backend."""

import logging
from asyncio import Lock
from collections.abc import Callable
from dataclasses import dataclass, field
from importlib.util import find_spec
from typing import TYPE_CHECKING
from uuid import uuid4

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.importlib import async_import_module

from .const import DOMAIN

if TYPE_CHECKING:
    from .runtime import NinebotConfigEntry

CONF_ASSISTANT = "enable_assistant"
CONF_ASSISTANT_VEHICLES = "assistant_vehicles"
CONF_ASSISTANT_TASKS = "enable_assistant_tasks"
TASK_POLICY_EPOCH = "assistant_policy_epoch"
ASSISTANT_OPTIONS = frozenset({CONF_ASSISTANT, CONF_ASSISTANT_VEHICLES, CONF_ASSISTANT_TASKS})
API_ID = "ninebot"
DATA_KEY = f"{DOMAIN}_assistant"
_LOGGER = logging.getLogger(__name__)


def valid_policy_epoch(value: object) -> bool:
    return isinstance(value, str) and len(value) == 32 and not set(value) - set("0123456789abcdef")


@callback
def async_revoke_task_policy(
    hass: HomeAssistant, entry: "NinebotConfigEntry", *, cleanup_owned: bool = False
) -> None:
    """Rotate before cleanup awaits, even when removing the authority file later fails."""
    if hass.config_entries.async_get_entry(entry.entry_id) is entry:
        epoch = uuid4().hex
        runtime = getattr(entry, "runtime_data", None)
        if cleanup_owned and runtime is not None and runtime.applied_data is not None:
            # This caller already owns cleanup; its data-only notification must not repeat it.
            runtime.applied_data = {**runtime.applied_data, TASK_POLICY_EPOCH: epoch}
        hass.config_entries.async_update_entry(entry, data={**entry.data, TASK_POLICY_EPOCH: epoch})


@callback
def async_sync_task_policy(hass: HomeAssistant, entry: "NinebotConfigEntry") -> None:
    """Observe every consent change before listener scheduling can coalesce transitions."""
    runtime = getattr(entry, "runtime_data", None)
    if runtime is None:
        return
    snapshot = {
        key: list(value) if isinstance(value := entry.options.get(key), list) else value
        for key in ASSISTANT_OPTIONS
    }
    previous = runtime.assistant_policy_options
    runtime.assistant_policy_options = snapshot  # Before updating data and reentering the listener.
    epoch = entry.data.get(TASK_POLICY_EPOCH)
    if (
        previous is not None
        and previous != snapshot
        and (
            valid_policy_epoch(epoch)
            or previous.get(CONF_ASSISTANT_TASKS) is True
            or snapshot[CONF_ASSISTANT_TASKS] is True
        )
    ):
        async_revoke_task_policy(hass, entry)
    elif snapshot[CONF_ASSISTANT_TASKS] is True and not valid_policy_epoch(epoch):
        async_revoke_task_policy(hass, entry)


def native_api_supported() -> bool:
    """The integration platform is absent on the legacy Core boundary.

    This check does not import helpers.llm or load a model/provider. The lazy
    adapter additionally checks the exact public classes it needs.
    """
    try:
        return find_spec("homeassistant.components.llm") is not None
    except (ImportError, ValueError):
        return False


@dataclass
class AssistantRegistration:
    lock: Lock = field(default_factory=Lock)
    unregister: Callable[[], None] | None = None
    unregister_tasks: Callable[[], None] | None = None
    unregister_panel: Callable[[], None] | None = None
    panel_error: str | None = None
    error: str | None = None
    review_error: str | None = None
    task_error: str | None = None


def _enabled(hass: HomeAssistant, *, tasks: bool = False) -> bool:
    return any(
        entry.options.get(CONF_ASSISTANT) is True
        and (not tasks or entry.options.get(CONF_ASSISTANT_TASKS) is True)
        and isinstance(vehicles := entry.options.get(CONF_ASSISTANT_VEHICLES), list)
        and any(isinstance(vehicle, str) and vehicle for vehicle in vehicles)
        and entry.state in {ConfigEntryState.LOADED, ConfigEntryState.SETUP_IN_PROGRESS}
        and (runtime := getattr(entry, "runtime_data", None)) is not None
        and not runtime.coordinator._stopping
        for entry in hass.config_entries.async_entries(DOMAIN)
    )


async def async_update_assistant_api(hass: HomeAssistant) -> None:
    """Register once across accounts, and revoke when no account enables it."""
    if not _enabled(hass) and DATA_KEY not in hass.data:
        return
    state: AssistantRegistration = hass.data.setdefault(DATA_KEY, AssistantRegistration())
    async with state.lock:
        if not _enabled(hass):
            if state.unregister_panel is not None:
                state.unregister_panel()
                state.unregister_panel = None
            state.panel_error = None
            if state.unregister_tasks is not None:
                state.unregister_tasks()
                state.unregister_tasks = None
            state.task_error = state.review_error = None
            if state.unregister is not None:
                state.unregister()
                state.unregister = None
            state.error = None
            return
        await _async_update_tasks(hass, state)
        if state.unregister is not None:
            return
        if not native_api_supported():
            error = "unsupported_native_api"
        else:
            try:
                adapter = await async_import_module(hass, f"{__package__}.assistant_api")
                state.unregister = adapter.async_register(hass)
                state.error = None
                return
            except (ImportError, AttributeError, HomeAssistantError):
                # AI is optional. Do not invalidate the vehicle runtime or log
                # exception text, which is not part of the support whitelist.
                error = "native_api_unavailable"
        if state.error != error:
            _LOGGER.warning("Ninebot Assistant is unavailable: %s", error)
        state.error = error


async def _async_update_tasks(hass: HomeAssistant, state: AssistantRegistration) -> None:
    enabled = _enabled(hass, tasks=True)
    await _async_update_panel(hass, state, enabled and native_api_supported())
    if enabled:
        try:
            review = await async_import_module(hass, f"{__package__}.assistant_review")
            review.async_register(hass)
            state.review_error = None
        except (ImportError, AttributeError, HomeAssistantError):
            if state.review_error is None:
                _LOGGER.warning("Ninebot task review is unavailable")
            state.review_error = "review_unavailable"
    if not enabled or state.review_error is not None or not native_api_supported():
        if state.unregister_tasks is not None:
            state.unregister_tasks()
            state.unregister_tasks = None
        if not enabled:
            state.task_error = state.review_error = None
        return
    if state.unregister_tasks is not None:
        return
    try:
        adapter = await async_import_module(hass, f"{__package__}.assistant_task_api")
        state.unregister_tasks = adapter.async_register(hass)
        state.task_error = None
    except (ImportError, AttributeError, HomeAssistantError):
        if state.task_error is None:
            _LOGGER.warning("Ninebot task tools are unavailable")
        state.task_error = "task_api_unavailable"


async def _async_update_panel(
    hass: HomeAssistant, state: AssistantRegistration, enabled: bool
) -> None:
    if not enabled:
        if state.unregister_panel is not None:
            state.unregister_panel()
            state.unregister_panel = None
        state.panel_error = None
        return
    if state.unregister_panel is not None or "frontend" not in hass.config.components:
        return
    try:
        panel = await async_import_module(hass, f"{__package__}.assistant_panel")
        state.unregister_panel = await panel.async_register(hass)
        state.panel_error = None
    except (ImportError, AttributeError, ValueError, HomeAssistantError):
        if state.panel_error is None:
            _LOGGER.warning("Ninebot task review panel is unavailable")
        state.panel_error = "panel_unavailable"


async def async_close_task_owner(
    hass: HomeAssistant,
    entry: "NinebotConfigEntry",
    *,
    remove: bool = False,
    permanent: bool = False,
    revoke_policy: bool = True,
) -> None:
    """Keep unused task implementation cold on ordinary unloads."""
    runtime = getattr(entry, "runtime_data", None)
    if not remove and getattr(runtime, "assistant_tasks", None) is None:
        if permanent and runtime is not None:
            runtime.assistant_task_blocks += 1
        return
    from .assistant_tasks import async_reset_tasks

    await async_reset_tasks(
        hass, entry, remove=remove, permanent=permanent, revoke_policy=revoke_policy
    )


async def async_resume_task_execution(hass: HomeAssistant, entry: "NinebotConfigEntry") -> None:
    """No task implementation or scheduler for accounts which have not opted in."""
    if (
        entry.options.get(CONF_ASSISTANT) is not True
        or entry.options.get(CONF_ASSISTANT_TASKS) is not True
        or not entry.options.get(CONF_ASSISTANT_VEHICLES)
    ):
        return
    from .assistant_execution import async_resume
    from .assistant_policy import AssistantError
    from .assistant_task_store import TaskStoreUnavailable
    from .ride_archive import ArchiveError

    try:
        await async_resume(hass, entry)
    except (AssistantError, TaskStoreUnavailable, ArchiveError):
        _LOGGER.warning("Ninebot approved tasks could not resume")
