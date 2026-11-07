"""Optional native Assistant lifecycle; importing this never loads an LLM backend."""

import logging
from asyncio import Lock
from collections.abc import Callable
from dataclasses import dataclass, field
from importlib.util import find_spec
from typing import TYPE_CHECKING

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.importlib import async_import_module

from .const import DOMAIN

if TYPE_CHECKING:
    from .runtime import NinebotConfigEntry

CONF_ASSISTANT = "enable_assistant"
CONF_ASSISTANT_VEHICLES = "assistant_vehicles"
CONF_ASSISTANT_TASKS = "enable_assistant_tasks"
ASSISTANT_OPTIONS = frozenset({CONF_ASSISTANT, CONF_ASSISTANT_VEHICLES, CONF_ASSISTANT_TASKS})
API_ID = "ninebot"
DATA_KEY = f"{DOMAIN}_assistant"
_LOGGER = logging.getLogger(__name__)


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


async def async_close_task_owner(
    hass: HomeAssistant,
    entry: "NinebotConfigEntry",
    *,
    remove: bool = False,
    permanent: bool = False,
) -> None:
    """Keep unused task implementation cold on ordinary unloads."""
    runtime = getattr(entry, "runtime_data", None)
    if not remove and getattr(runtime, "assistant_tasks", None) is None:
        if permanent and runtime is not None:
            runtime.assistant_task_blocks += 1
        return
    from .assistant_tasks import async_reset_tasks

    await async_reset_tasks(hass, entry, remove=remove, permanent=permanent)
