"""Explicit credential entry, isolated validation and account-safe reauth."""

import asyncio
import re
import uuid
from typing import Any

from homeassistant import config_entries
from homeassistant.config_entries import ConfigEntry, ConfigEntryState, ConfigFlowResult
from homeassistant.const import CONF_PASSWORD
from homeassistant.core import callback
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.event import async_call_later
from homeassistant.helpers.selector import (
    DeviceSelector,
    DeviceSelectorConfig,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from .account import ACCOUNT_METADATA, AUTOMATIC_TITLE, account_update
from .account_archive import CONF_RETAIN_ARCHIVE
from .archive_options import ArchiveOptionsMixin
from .bootstrap import CONF_AUTO_HISTORY
from .compat import post_create_options_supported
from .compat import validation as vol
from .const import (
    CONF_ACCOUNT,
    CONF_BUSINESS_UID,
    CONF_CALENDAR,
    CONF_CONTROL_VEHICLES,
    CONF_CONTROLS,
    CONF_COORDINATES,
    CONF_DEBUG,
    CONF_ESTIMATION,
    CONF_EVENTS,
    CONF_IDENTITY_SCHEME,
    CONF_POLL_INTERVAL,
    CONF_SESSION_KEY,
    DEFAULT_COORDINATES,
    DEFAULT_POLL_INTERVAL,
    DOMAIN,
)
from .exceptions import ErrorKind, NinebotError
from .history_budget import CONF_BUDGET, MAX_BUDGET, MIN_BUDGET, configured_budget
from .history_capacity import (
    CONF_HISTORY_CAPACITY,
    MAX_HISTORY_MIB,
    MIN_HISTORY_MIB,
    history_capacity_mib,
)
from .identity import IDENTITY_VERSION
from .parsing import number
from .preservation import CONF_PRESERVE
from .services import resolve_vehicle
from .session import SMS_LIFETIME, Candidate, SessionManager, SmsChallenge

ERRORS = {
    ErrorKind.AUTH: "invalid_auth",
    ErrorKind.CONNECTION: "cannot_connect",
    ErrorKind.SERVICE: "upstream_error",
    ErrorKind.PROTOCOL: "invalid_response",
    ErrorKind.PLATFORM: "unsupported_platform",
    ErrorKind.BUSY: "busy",
    ErrorKind.CLOSED: "cannot_connect",
}

# Keep the isolated implementation for offline protocol research. Re-enable the
# public flow only after actual delivery and consumption have been verified.
SMS_LOGIN_ENABLED = False
ONBOARDING_LIFETIME = 600


class NinebotConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 2
    MINOR_VERSION = 2

    def __init__(self) -> None:
        self._entry: ConfigEntry | None = None
        self._sms: SmsChallenge | None = None
        self._sms_origin = "user"
        self._sms_options: dict[str, Any] = {}
        self._sms_timer: Any = None
        self._sms_task: asyncio.Task[Any] | None = None
        self._reloading = False
        self._onboarding: Candidate | None = None
        self._onboarding_account: str | None = None
        self._onboarding_options: dict[str, Any] = {}
        self._onboarding_timer: Any = None

    def _manager(self) -> SessionManager:
        from . import manager_for

        return manager_for(self.hass)

    async def _credentials(
        self,
        step: str,
        user_input: dict[str, Any] | None,
        prepared: Candidate | None = None,
    ) -> ConfigFlowResult:
        errors = {}
        if user_input is not None and prepared is None:
            if user_input.get("login_method") == "sms":
                if not SMS_LOGIN_ENABLED:
                    return self.async_abort(reason="sms_unavailable")
                self._sms_origin = step
                return await self._send_sms(user_input)
            if not user_input.get(CONF_PASSWORD):
                errors[CONF_PASSWORD] = "password_required"
                user_input = None
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
                old_title = self._entry.title if self._entry else None
                unloaded = False
                recovery_pending = False
                try:
                    if self._entry and self._entry.state == ConfigEntryState.FAILED_UNLOAD:
                        # The old runtime may still own this directory. Do not
                        # recover or replace it until HA can unload it safely.
                        raise NinebotError(ErrorKind.BUSY)
                    candidate = prepared or await manager.async_prepare(
                        account, str(user_input[CONF_PASSWORD])
                    )
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
                    if (
                        self._entry is None
                        and prepared is None
                        and not post_create_options_supported()
                    ):
                        # Old HA has no post-create hook. Complete feature
                        # selection after verification, before committing; no
                        # credential or transaction lock survives the form.
                        self._onboarding = candidate
                        candidate = None
                        self._onboarding_account = account
                        self._onboarding_options = {
                            CONF_ESTIMATION: False,
                            CONF_COORDINATES: DEFAULT_COORDINATES,
                            CONF_CALENDAR: False,
                            CONF_DEBUG: False,
                        }

                        @callback
                        def expire_features(_: Any) -> None:
                            self.hass.config_entries.flow.async_abort(self.flow_id)

                        self._onboarding_timer = async_call_later(
                            self.hass, ONBOARDING_LIFETIME, expire_features
                        )
                        return await self.async_step_features()
                    if self._entry and self._entry.state == ConfigEntryState.LOADED:
                        unloaded = await self.hass.config_entries.async_unload(self._entry.entry_id)
                        if not unloaded:
                            raise NinebotError(ErrorKind.BUSY)
                    await manager.async_commit(candidate, key)
                    committed = True
                    data = {
                        **(old_data or {}),
                        IDENTITY_VERSION: old_data.get(IDENTITY_VERSION, 0) if old_data else 1,
                        CONF_ACCOUNT: account,
                        CONF_BUSINESS_UID: candidate.uid,
                        CONF_SESSION_KEY: key,
                        CONF_IDENTITY_SCHEME: self._entry.data.get(CONF_IDENTITY_SCHEME, "v2")
                        if self._entry
                        else "v2",
                    }
                    metadata, automatic, title = account_update(
                        old_data or {}, account, candidate.display, old_title
                    )
                    data.update({ACCOUNT_METADATA: metadata, AUTOMATIC_TITLE: automatic})
                    if self._entry:
                        self.hass.config_entries.async_update_entry(
                            self._entry, data=data, unique_id=candidate.uid, title=title
                        )
                        metadata_updated = True
                        if not await self._reload_entry():
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
                        title=title,
                        data=data,
                        options={
                            CONF_CONTROLS: True,
                            CONF_CONTROL_VEHICLES: list(candidate.vehicles),
                            CONF_POLL_INTERVAL: DEFAULT_POLL_INTERVAL,
                            CONF_RETAIN_ARCHIVE: True,
                            CONF_PRESERVE: False,
                            CONF_AUTO_HISTORY: False,
                            CONF_CALENDAR: bool(user_input.get(CONF_CALENDAR, False)),
                            **{
                                key: bool(
                                    user_input.get(
                                        key,
                                        DEFAULT_COORDINATES if key == CONF_COORDINATES else False,
                                    )
                                )
                                for key in (CONF_DEBUG, CONF_ESTIMATION, CONF_COORDINATES)
                            },
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
                                    self._entry,
                                    data=old_data,
                                    unique_id=old_uid,
                                    title=old_title or "",
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
                        await self._reload_entry()
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
                    (vol.Optional if SMS_LOGIN_ENABLED else vol.Required)(
                        CONF_PASSWORD
                    ): TextSelector(TextSelectorConfig(type=TextSelectorType.PASSWORD)),
                    **(
                        {
                            vol.Optional("login_method", default="password"): SelectSelector(
                                SelectSelectorConfig(
                                    options=["password", "sms"], translation_key="login_method"
                                )
                            )
                        }
                        if SMS_LOGIN_ENABLED
                        else {}
                    ),
                    **(
                        {
                            vol.Optional(
                                key,
                                default=DEFAULT_COORDINATES if key == CONF_COORDINATES else False,
                            ): bool
                            for key in (CONF_DEBUG, CONF_ESTIMATION, CONF_COORDINATES)
                        }
                        if SMS_LOGIN_ENABLED and self._entry is None
                        else {}
                    ),
                }
            ),
        )

    async def async_on_create_entry(self, result: ConfigFlowResult) -> ConfigFlowResult:
        """New Core opens the account settings after the account exists."""
        if post_create_options_supported():
            options = await self.hass.config_entries.options.async_init(result["result"].entry_id)
            result["next_flow"] = (
                config_entries.FlowType("options_flow"),
                options["flow_id"],
            )
        return result

    async def async_step_features(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Equivalent post-login feature setup on Core without flow chaining."""
        if self._onboarding is None or self._onboarding_account is None:
            return self.async_abort(reason="onboarding_expired")
        if user_input is not None:
            candidate, self._onboarding = self._onboarding, None
            if self._onboarding_timer:
                self._onboarding_timer()
                self._onboarding_timer = None
            return await self._credentials(
                "user", {CONF_ACCOUNT: self._onboarding_account, **user_input}, candidate
            )
        return self.async_show_form(
            step_id="features",
            data_schema=vol.Schema(
                {
                    vol.Optional(key, default=value): bool
                    for key, value in self._onboarding_options.items()
                }
            ),
        )

    async def _reload_entry(self) -> bool:
        assert self._entry is not None
        self._reloading = True
        try:
            return await self.hass.config_entries.async_reload(self._entry.entry_id)
        finally:
            self._reloading = False

    @callback
    def _arm_sms_timer(self) -> None:
        if self._sms_timer:
            self._sms_timer()

        @callback
        def expire(_: Any) -> None:
            if any(
                flow["flow_id"] == self.flow_id
                for flow in self.hass.config_entries.flow.async_progress()
            ):
                self.hass.config_entries.flow.async_abort(self.flow_id)

        self._sms_timer = async_call_later(self.hass, SMS_LIFETIME, expire)

    @callback
    def async_remove(self) -> None:
        if self._onboarding_timer:
            self._onboarding_timer()
            self._onboarding_timer = None
        if self._onboarding:
            candidate, self._onboarding = self._onboarding, None
            self.hass.async_create_task(self._manager().async_discard(candidate))
        if self._sms_timer:
            self._sms_timer()
            self._sms_timer = None
        if self._sms_task and not self._reloading:
            self._sms_task.cancel()
        if self._sms:
            challenge, self._sms = self._sms, None
            self.hass.async_create_task(self._manager().async_discard_sms(challenge))
        super().async_remove()

    async def _send_sms(self, user_input: dict[str, Any]) -> ConfigFlowResult:
        account = str(user_input[CONF_ACCOUNT]).strip()
        if re.fullmatch(r"1[0-9]{10}", account) is None:
            return self.async_show_form(
                step_id="sms_account",
                errors={"base": "invalid_account"},
                data_schema=vol.Schema({vol.Required(CONF_ACCOUNT): str}),
            )
        self._sms_options = {
            key: bool(
                user_input.get(
                    key,
                    self._sms_options.get(
                        key, DEFAULT_COORDINATES if key == CONF_COORDINATES else False
                    ),
                )
            )
            for key in (CONF_DEBUG, CONF_ESTIMATION, CONF_COORDINATES)
        }
        self._sms_task = asyncio.current_task()
        try:
            self._sms = await self._manager().async_send_sms(account)
        except NinebotError as err:
            return self.async_show_form(
                step_id="sms_account",
                errors={"base": "sms_cooldown" if err.kind is ErrorKind.BUSY else ERRORS[err.kind]},
                data_schema=vol.Schema({vol.Required(CONF_ACCOUNT, default=account): str}),
            )
        except OSError:
            return self.async_abort(reason="storage_error")
        finally:
            self._sms_task = None
        self._arm_sms_timer()
        return await self.async_step_sms_code()

    async def async_step_sms_account(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if not SMS_LOGIN_ENABLED:
            return self.async_abort(reason="sms_unavailable")
        if user_input is None:
            return self.async_show_form(
                step_id="sms_account", data_schema=vol.Schema({vol.Required(CONF_ACCOUNT): str})
            )
        return await self._send_sms(user_input)

    async def async_step_sms_code(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if not SMS_LOGIN_ENABLED:
            return self.async_abort(reason="sms_unavailable")
        if self._sms is None:
            return self.async_abort(reason="sms_expired")
        errors = {}
        if user_input is not None:
            if user_input.get("resend"):
                try:
                    await self._manager().async_send_sms(self._sms.account, self._sms)
                    self._arm_sms_timer()
                except NinebotError as err:
                    errors["base"] = (
                        "sms_cooldown" if err.kind is ErrorKind.BUSY else ERRORS[err.kind]
                    )
                except OSError:
                    errors["base"] = "storage_error"
            elif re.fullmatch(r"[0-9]{4,8}", str(user_input.get("code", ""))) is None:
                errors["code"] = "invalid_code"
            else:
                challenge = self._sms
                self._sms_task = asyncio.current_task()
                candidate = None
                try:
                    candidate = await self._manager().async_consume_sms(
                        challenge, str(user_input["code"])
                    )
                    self._sms = None
                    if self._sms_timer:
                        self._sms_timer()
                        self._sms_timer = None
                    return await self._credentials(
                        self._sms_origin,
                        {CONF_ACCOUNT: challenge.account, **self._sms_options},
                        candidate,
                    )
                except NinebotError as err:
                    errors["base"] = (
                        "invalid_code" if err.kind is ErrorKind.AUTH else ERRORS[err.kind]
                    )
                except OSError:
                    errors["base"] = "storage_error"
                finally:
                    self._sms_task = None
                    if candidate is not None:
                        await self._manager().async_discard(candidate)
        return self.async_show_form(
            step_id="sms_code",
            errors=errors,
            data_schema=vol.Schema(
                {
                    vol.Optional("code"): TextSelector(
                        TextSelectorConfig(type=TextSelectorType.PASSWORD)
                    ),
                    vol.Optional("resend", default=False): bool,
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


class NinebotOptionsFlow(ArchiveOptionsMixin):
    def __init__(self) -> None:
        self._pending_options: dict[str, Any] = {}
        self._model_vehicle: str | None = None

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            self._pending_options = {**self.config_entry.options, **user_input}
            if self._pending_options.get(CONF_CONTROLS) and not self._pending_options.get(
                CONF_CONTROL_VEHICLES
            ):
                errors[CONF_CONTROL_VEHICLES] = "control_vehicles_required"
            else:
                if self._pending_options.pop("manage_archive", False):
                    return await self.async_step_archive()
                configure_model = self._pending_options.pop("configure_model", False)
                if configure_model:
                    return await self.async_step_model_vehicle()
                return self.async_create_entry(title="", data=self._pending_options)
        options = {**self.config_entry.options, **(user_input or {})}
        runtime = getattr(self.config_entry, "runtime_data", None)
        vehicles = runtime.coordinator.data if runtime else {}
        choices = [
            SelectOptionDict(value=sn, label=f"{snapshot.profile.name} · {sn[-6:]}")
            for sn, snapshot in vehicles.items()
            if snapshot.present
        ]
        # Keep previously saved choices selectable when validation redisplays
        # an empty selection and vehicle discovery is temporarily unavailable.
        for sn in self.config_entry.options.get(CONF_CONTROL_VEHICLES, []):
            if sn not in {choice["value"] for choice in choices}:
                snapshot = vehicles.get(sn)
                choices.append(
                    SelectOptionDict(value=sn, label=snapshot.profile.name if snapshot else sn)
                )
        return self.async_show_form(
            step_id="init",
            errors=errors,
            data_schema=vol.Schema(
                {
                    vol.Optional(CONF_CONTROLS, default=options.get(CONF_CONTROLS, False)): bool,
                    vol.Optional(
                        CONF_CONTROL_VEHICLES, default=options.get(CONF_CONTROL_VEHICLES, [])
                    ): SelectSelector(
                        SelectSelectorConfig(
                            options=choices, multiple=True, mode=SelectSelectorMode.DROPDOWN
                        )
                    ),
                    vol.Required(
                        CONF_POLL_INTERVAL,
                        default=options.get(CONF_POLL_INTERVAL, DEFAULT_POLL_INTERVAL),
                    ): vol.All(vol.Coerce(int), vol.Range(min=30, max=3600)),
                    vol.Optional(
                        CONF_COORDINATES, default=options.get(CONF_COORDINATES, DEFAULT_COORDINATES)
                    ): bool,
                    vol.Optional(CONF_EVENTS, default=options.get(CONF_EVENTS, True)): bool,
                    vol.Optional(CONF_CALENDAR, default=options.get(CONF_CALENDAR, True)): bool,
                    vol.Optional(CONF_PRESERVE, default=options.get(CONF_PRESERVE, False)): bool,
                    vol.Optional(
                        CONF_AUTO_HISTORY,
                        default=options.get(CONF_AUTO_HISTORY, options.get(CONF_PRESERVE, False)),
                    ): bool,
                    vol.Optional(
                        CONF_HISTORY_CAPACITY, default=history_capacity_mib(options)
                    ): vol.All(
                        vol.Coerce(int), vol.Range(min=MIN_HISTORY_MIB, max=MAX_HISTORY_MIB)
                    ),
                    vol.Required(
                        CONF_BUDGET, default=configured_budget(options)
                    ): HistoryBudgetSelector(),
                    vol.Optional(
                        CONF_RETAIN_ARCHIVE, default=options.get(CONF_RETAIN_ARCHIVE, True)
                    ): bool,
                    vol.Optional("manage_archive", default=False): bool,
                    vol.Optional("configure_model", default=False): bool,
                    vol.Optional(CONF_DEBUG, default=options.get(CONF_DEBUG, False)): bool,
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
        if not runtime or not choices:
            return self.async_abort(reason="model_unavailable")
        errors: dict[str, str] = {}
        if user_input is not None:
            selection = user_input["model_vehicle"]
            # Accept strictly known legacy SN submissions, never a display name.
            sn = selection if selection in {choice["value"] for choice in choices} else None
            if sn is None:
                try:
                    owner, resolved = resolve_vehicle(self.hass, selection)
                    if owner.entry_id == self.config_entry.entry_id:
                        sn = resolved
                except ServiceValidationError:
                    pass
            if sn is not None and runtime.coordinator.fresh(sn, "profile"):
                self._model_vehicle = sn
                return await self.async_step_model_parameters()
            errors["model_vehicle"] = "model_unavailable"
        return self.async_show_form(
            step_id="model_vehicle",
            errors=errors,
            data_schema=vol.Schema(
                {
                    vol.Required("model_vehicle"): DeviceSelector(
                        DeviceSelectorConfig(filter={"integration": DOMAIN})
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
        errors = {}
        if user_input is not None:
            try:
                runtime.models.configure(
                    sn, {key: user_input.get(key) for key in ("voltage", "capacity")}
                )
            except ValueError:
                errors["base"] = "model_storage_invalid"
            else:
                self._pending_options[CONF_ESTIMATION] = True
                runtime.coordinator.async_set_updated_data(dict(runtime.coordinator.data))
                return self.async_create_entry(title="", data=self._pending_options)
        schema = {}
        for key, maximum, unit in (("voltage", 300, "V"), ("capacity", 500, "Ah")):
            value = getattr(model, key)
            field = vol.Optional(
                key, description={"suggested_value": value} if value is not None else None
            )
            schema[field] = parameter_validator(maximum, unit)
        return self.async_show_form(
            step_id="model_parameters", data_schema=vol.Schema(schema), errors=errors
        )


class HistoryBudgetSelector(NumberSelector):
    """A native input box, retaining integers and explicit unlimited zero."""

    def __init__(self) -> None:
        super().__init__(
            NumberSelectorConfig(
                min=MIN_BUDGET, max=MAX_BUDGET, step=1, mode=NumberSelectorMode.BOX
            )
        )

    def __call__(self, value: Any) -> int:
        result = number(value, MIN_BUDGET, MAX_BUDGET)
        if result is None or not result.is_integer():
            raise vol.Invalid("History budget must be an integer from 0 to 120")
        return int(result)


class RatedParameterSelector(NumberSelector):
    """Serialize a standard number field while retaining strict finite validation."""

    def __init__(self, maximum: float, unit: str) -> None:
        self._maximum = maximum
        super().__init__(
            NumberSelectorConfig(
                min=1,
                max=maximum,
                step="any",
                mode=NumberSelectorMode.BOX,
                unit_of_measurement=unit,
            )
        )

    def __call__(self, value: Any) -> float:
        result = number(value, 1, self._maximum)
        if result is None:
            raise vol.Invalid("Model parameter outside supported range")
        return result


def parameter_validator(maximum: float, unit: str = "") -> RatedParameterSelector:
    return RatedParameterSelector(maximum, unit)
