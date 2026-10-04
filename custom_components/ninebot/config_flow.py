"""Explicit credential entry, isolated validation and account-safe reauth."""

import uuid
from typing import Any

from homeassistant import config_entries
from homeassistant.config_entries import ConfigEntry, ConfigEntryState, ConfigFlowResult
from homeassistant.const import CONF_PASSWORD
from homeassistant.core import callback
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.selector import (
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from .compat import validation as vol
from .const import (
    CONF_ACCOUNT,
    CONF_BUSINESS_UID,
    CONF_CONTROL_VEHICLES,
    CONF_CONTROLS,
    CONF_COORDINATES,
    CONF_DEBUG,
    CONF_ESTIMATION,
    CONF_IDENTITY_SCHEME,
    CONF_POLL_INTERVAL,
    CONF_SESSION_KEY,
    DEFAULT_POLL_INTERVAL,
    DOMAIN,
)
from .exceptions import ErrorKind, NinebotError
from .parsing import number
from .session import SessionManager

ERRORS = {
    ErrorKind.AUTH: "invalid_auth",
    ErrorKind.CONNECTION: "cannot_connect",
    ErrorKind.SERVICE: "upstream_error",
    ErrorKind.PROTOCOL: "invalid_response",
    ErrorKind.PLATFORM: "unsupported_platform",
    ErrorKind.BUSY: "busy",
    ErrorKind.CLOSED: "cannot_connect",
}


class NinebotConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 2
    MINOR_VERSION = 1

    def __init__(self) -> None:
        self._entry: ConfigEntry | None = None

    def _manager(self) -> SessionManager:
        from . import manager_for

        return manager_for(self.hass)

    async def _credentials(self, step: str, user_input: dict[str, Any] | None) -> ConfigFlowResult:
        errors = {}
        if user_input is not None:
            account = str(user_input[CONF_ACCOUNT]).strip()
            manager = self._manager()
            key = self._entry.data[CONF_SESSION_KEY] if self._entry else uuid.uuid4().hex
            async with manager.transaction(key):
                candidate = None
                committed = False
                finished = False
                metadata_updated = False
                old_data = dict(self._entry.data) if self._entry else None
                old_uid = self._entry.unique_id if self._entry else None
                unloaded = False
                recovery_pending = False
                try:
                    if self._entry and self._entry.state == ConfigEntryState.FAILED_UNLOAD:
                        # The old runtime may still own this directory. Do not
                        # recover or replace it until HA can unload it safely.
                        raise NinebotError(ErrorKind.BUSY)
                    candidate = await manager.async_prepare(account, str(user_input[CONF_PASSWORD]))
                    if self._entry:
                        expected = self._entry.data.get(CONF_BUSINESS_UID)
                        if (expected and candidate.uid != expected) or (
                            not expected and account != self._entry.data.get(CONF_ACCOUNT)
                        ):
                            return self.async_abort(reason="wrong_account")
                    await self.async_set_unique_id(
                        candidate.uid, raise_on_progress=self._entry is None
                    )
                    for entry in self._async_current_entries():
                        if entry is not self._entry and (
                            entry.unique_id == candidate.uid
                            or entry.data.get(CONF_BUSINESS_UID) == candidate.uid
                        ):
                            return self.async_abort(reason="already_configured")
                    if self._entry and self._entry.state == ConfigEntryState.LOADED:
                        unloaded = await self.hass.config_entries.async_unload(self._entry.entry_id)
                        if not unloaded:
                            raise NinebotError(ErrorKind.BUSY)
                    await manager.async_commit(candidate, key)
                    committed = True
                    data = {
                        CONF_ACCOUNT: account,
                        CONF_BUSINESS_UID: candidate.uid,
                        CONF_SESSION_KEY: key,
                        CONF_IDENTITY_SCHEME: self._entry.data.get(CONF_IDENTITY_SCHEME, "v2")
                        if self._entry
                        else "v2",
                    }
                    if self._entry:
                        self.hass.config_entries.async_update_entry(
                            self._entry, data=data, unique_id=candidate.uid
                        )
                        metadata_updated = True
                        if not await self.hass.config_entries.async_reload(self._entry.entry_id):
                            raise NinebotError(ErrorKind.CONNECTION)
                        await manager.async_finalize(key)
                        committed = False
                        finished = True
                        return self.async_abort(
                            reason="reauth_successful"
                            if step == "reauth_confirm"
                            else "reconfigure_successful"
                        )
                    result = self.async_create_entry(
                        title="Ninebot",
                        data=data,
                        options={
                            key: bool(user_input.get(key, False))
                            for key in (CONF_DEBUG, CONF_ESTIMATION, CONF_COORDINATES)
                        },
                    )
                    await manager.async_finalize(key)
                    committed = False
                    finished = True
                    return result
                except NinebotError as err:
                    errors["base"] = ERRORS[err.kind]
                except OSError:
                    errors["base"] = "storage_error"
                finally:
                    if committed and await manager.async_is_pending(key):
                        can_rollback = True
                        if self._entry and self._entry.state == ConfigEntryState.LOADED:
                            can_rollback = await self.hass.config_entries.async_unload(
                                self._entry.entry_id
                            )
                        if not can_rollback and self._entry:
                            # Never replace files under a live runtime. Retain
                            # the journal and backup for recovery after restart.
                            recovery_pending = True
                            ir.async_create_issue(
                                self.hass,
                                DOMAIN,
                                f"session_recovery_{self._entry.entry_id}",
                                is_fixable=False,
                                severity=ir.IssueSeverity.ERROR,
                                translation_key="session_recovery_pending",
                            )
                        else:
                            rolled_back = await manager.async_rollback(key)
                            if (
                                rolled_back
                                and self._entry
                                and metadata_updated
                                and old_data is not None
                            ):
                                self.hass.config_entries.async_update_entry(
                                    self._entry, data=old_data, unique_id=old_uid
                                )
                            if not rolled_back:
                                # A cancelled finalization may have passed its
                                # commit point. Keep both accepted metadata/files.
                                finished = True
                    elif committed:
                        # The finalization worker crossed the commit point before
                        # cancellation. The replacement runtime and metadata agree.
                        finished = True
                    if candidate:
                        await manager.async_discard(candidate)
                    if (
                        self._entry
                        and not finished
                        and not recovery_pending
                        and (metadata_updated or unloaded)
                    ):
                        await self.hass.config_entries.async_reload(self._entry.entry_id)
            if recovery_pending:
                return self.async_abort(reason="session_recovery_pending")
            if (
                errors
                and self._entry
                and metadata_updated
                and not any(
                    flow["flow_id"] == self.flow_id
                    for flow in self.hass.config_entries.flow.async_progress()
                )
            ):
                # HA async_reload aborts reauth flows, even if setup subsequently
                # fails. Returning a form for that removed flow raises UnknownFlow.
                return self.async_abort(reason="session_update_failed")
        default = self._entry.data.get(CONF_ACCOUNT, "") if self._entry else ""
        return self.async_show_form(
            step_id=step,
            errors=errors,
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_ACCOUNT, default=default): str,
                    vol.Required(CONF_PASSWORD): TextSelector(
                        TextSelectorConfig(type=TextSelectorType.PASSWORD)
                    ),
                    **(
                        {
                            vol.Optional(key, default=False): bool
                            for key in (CONF_DEBUG, CONF_ESTIMATION, CONF_COORDINATES)
                        }
                        if self._entry is None
                        else {}
                    ),
                }
            ),
        )

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        return await self._credentials("user", user_input)

    async def async_step_reauth(self, entry_data: dict[str, Any]) -> ConfigFlowResult:
        self._entry = self._get_reauth_entry()
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        return await self._credentials("reauth_confirm", user_input)

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        self._entry = self._get_reconfigure_entry()
        return await self._credentials("reconfigure", user_input)

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> "NinebotOptionsFlow":
        return NinebotOptionsFlow()


class NinebotOptionsFlow(config_entries.OptionsFlow):
    def __init__(self) -> None:
        self._pending_options: dict[str, Any] = {}
        self._model_vehicle: str | None = None

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            self._pending_options = dict(user_input)
            configure_model = self._pending_options.pop("configure_model", False)
            if configure_model:
                return await self.async_step_model_vehicle()
            return self.async_create_entry(title="", data=self._pending_options)
        options = self.config_entry.options
        runtime = getattr(self.config_entry, "runtime_data", None)
        vehicles = runtime.coordinator.data if runtime else {}
        choices = [
            SelectOptionDict(value=sn, label=snapshot.profile.name)
            for sn, snapshot in vehicles.items()
            if snapshot.present
        ]
        for sn in options.get(CONF_CONTROL_VEHICLES, []):
            if sn not in {choice["value"] for choice in choices}:
                snapshot = vehicles.get(sn)
                choices.append(
                    SelectOptionDict(value=sn, label=snapshot.profile.name if snapshot else sn)
                )
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_POLL_INTERVAL,
                        default=options.get(CONF_POLL_INTERVAL, DEFAULT_POLL_INTERVAL),
                    ): vol.All(vol.Coerce(int), vol.Range(min=30, max=3600)),
                    vol.Optional(
                        CONF_ESTIMATION, default=options.get(CONF_ESTIMATION, False)
                    ): bool,
                    vol.Optional(
                        CONF_COORDINATES, default=options.get(CONF_COORDINATES, False)
                    ): bool,
                    vol.Optional(CONF_DEBUG, default=options.get(CONF_DEBUG, False)): bool,
                    vol.Optional("configure_model", default=False): bool,
                    vol.Optional(CONF_CONTROLS, default=options.get(CONF_CONTROLS, False)): bool,
                    vol.Optional(
                        CONF_CONTROL_VEHICLES, default=options.get(CONF_CONTROL_VEHICLES, [])
                    ): SelectSelector(SelectSelectorConfig(options=choices, multiple=True)),
                }
            ),
        )

    async def async_step_model_vehicle(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        runtime = getattr(self.config_entry, "runtime_data", None)
        vehicles = runtime.coordinator.data if runtime else {}
        choices = [
            SelectOptionDict(value=sn, label=snapshot.profile.name)
            for sn, snapshot in vehicles.items()
            if snapshot.present
        ]
        if not choices:
            return self.async_abort(reason="model_unavailable")
        if user_input is not None:
            sn = user_input["model_vehicle"]
            if sn not in {choice["value"] for choice in choices}:
                return self.async_abort(reason="model_unavailable")
            self._model_vehicle = sn
            return await self.async_step_model_parameters()
        return self.async_show_form(
            step_id="model_vehicle",
            data_schema=vol.Schema(
                {
                    vol.Required("model_vehicle"): SelectSelector(
                        SelectSelectorConfig(options=choices)
                    )
                }
            ),
        )

    async def async_step_model_parameters(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        runtime = getattr(self.config_entry, "runtime_data", None)
        sn = self._model_vehicle
        if not runtime or sn is None or not runtime.coordinator.fresh(sn, "profile"):
            return self.async_abort(reason="model_unavailable")
        model = runtime.models.model(sn)
        if user_input is not None:
            runtime.models.configure(sn, user_input)
            runtime.coordinator.async_set_updated_data(dict(runtime.coordinator.data))
            return self.async_create_entry(title="", data=self._pending_options)
        schema = {}
        for key, maximum in (("voltage", 300), ("capacity", 500)):
            value = getattr(model, key)
            field = vol.Required(key) if value is None else vol.Required(key, default=value)
            schema[field] = parameter_validator(maximum)
        return self.async_show_form(step_id="model_parameters", data_schema=vol.Schema(schema))


def parameter_validator(maximum: float) -> Any:
    def validate(value: object) -> float:
        result = number(value, 1, maximum)
        if result is None:
            raise vol.Invalid("Model parameter outside supported range")
        return result

    return validate
