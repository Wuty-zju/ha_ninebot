"""Native options-flow archive management, with admin-only temporary downloads."""

import secrets
import shutil
import sqlite3
import tempfile
import zipfile
from datetime import timedelta
from pathlib import Path
from typing import Any

from aiohttp import web
from homeassistant import config_entries
from homeassistant.components.file_upload import process_uploaded_file
from homeassistant.components.http import KEY_HASS, HomeAssistantView
from homeassistant.components.http.auth import async_sign_path
from homeassistant.components.http.const import KEY_HASS_USER
from homeassistant.config_entries import ConfigFlowResult
from homeassistant.core import HomeAssistant
from homeassistant.helpers.http import current_request
from homeassistant.helpers.selector import (
    FileSelector,
    FileSelectorConfig,
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
)

from .account_archive import account_lock, owner_key
from .archive_transfer import pack, readonly, snapshot, unpack, validate
from .backup import backup_active
from .compat import validation as vol
from .const import CONF_BUSINESS_UID, DOMAIN
from .ride_archive import ArchiveError
from .runtime import RuntimeData

DOWNLOADS = f"{DOMAIN}_archive_downloads"
URL = "/api/ninebot/archive/"


def loaded_accounts(hass: HomeAssistant) -> dict[str, Any]:
    return {
        owner_key(str(entry.data[CONF_BUSINESS_UID])): entry
        for entry in hass.config_entries.async_entries(DOMAIN)
        if isinstance(getattr(entry, "runtime_data", None), RuntimeData)
        and entry.runtime_data.coordinator.statistics.archive_ready
        and not entry.runtime_data.coordinator._stopping
    }


class ArchiveDownload(HomeAssistantView):
    url = URL + "{token}"
    name = "api:ninebot:archive"

    async def get(self, request: web.Request, token: str) -> web.StreamResponse:
        user = request.get(KEY_HASS_USER)
        if user is None or not user.is_admin:
            raise web.HTTPForbidden
        hass = request.app[KEY_HASS]
        downloads = hass.data.get(DOWNLOADS, {})
        item = downloads.get(token)
        if item is None or item[0] != user.id:
            raise web.HTTPNotFound
        return web.FileResponse(
            item[1],
            headers={
                "Content-Disposition": 'attachment; filename="ninebot-history.zip"',
                "Cache-Control": "no-store",
                "Referrer-Policy": "no-referrer",
            },
        )


def register_download(hass: HomeAssistant) -> None:
    if DOWNLOADS not in hass.data and (http := getattr(hass, "http", None)):
        hass.data[DOWNLOADS] = {}
        http.register_view(ArchiveDownload())


class ArchiveOptionsMixin(config_entries.OptionsFlow):
    """Selecting a file only stages it; no import before explicit confirmation."""

    _archive_directory: Path | None = None
    _archive_sources: list[tuple[str, Path]] | None = None

    _archive_user_id: str | None = None

    async def _archive_admin(self) -> bool:
        request = current_request.get()
        user = request.get(KEY_HASS_USER) if request is not None else None
        if user is None or not user.is_admin:
            return False
        if self._archive_user_id is not None and self._archive_user_id != user.id:
            return False
        self._archive_user_id = user.id
        return True

    async def async_step_archive(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if not await self._archive_admin():
            return self.async_abort(reason="archive_admin_required")
        return self.async_show_menu(
            step_id="archive", menu_options=["archive_export", "archive_import"]
        )

    async def async_step_archive_export(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if not await self._archive_admin():
            return self.async_abort(reason="archive_admin_required")
        accounts = loaded_accounts(self.hass)
        choices: list[SelectOptionDict] = []
        for owner, entry in accounts.items():
            profiles = await entry.runtime_data.coordinator.statistics.archive.async_profiles()
            choices.extend(
                SelectOptionDict(
                    value=f"{owner}:{profile.sn}", label=f"{entry.title} · {profile.name}"
                )
                for profile in profiles
            )
        errors = {}
        if user_input is not None:
            selections = user_input.get("vehicles", [])
            allowed = {choice["value"] for choice in choices}
            if not selections or not set(selections) <= allowed:
                errors["base"] = "archive_invalid"
            elif backup_active(self.hass):
                errors["base"] = "archive_busy"
            else:
                directory = Path(await self.hass.async_add_executor_job(tempfile.mkdtemp))
                try:
                    snapshots = []
                    evidence: dict[str, list[Path]] = {}
                    for owner, entry in accounts.items():
                        vehicles = tuple(
                            value.split(":", 1)[1]
                            for value in selections
                            if value.startswith(owner + ":")
                        )
                        if not vehicles:
                            continue
                        path = directory / f"{owner}.sqlite3"
                        from .ride_archive import vehicle_key

                        async with account_lock(self.hass, str(entry.data[CONF_BUSINESS_UID])):
                            archive = entry.runtime_data.coordinator.statistics.archive
                            await archive.async_export_archive(
                                path, tuple(vehicle_key(sn) for sn in vehicles)
                            )

                        def source_snapshots(
                            archive: Any = archive,
                            owner: str = owner,
                            vehicles: tuple[str, ...] = vehicles,
                        ) -> list[Path]:
                            sources = []
                            from .ride_archive import vehicle_key

                            for index, original in enumerate(
                                sorted((archive.path.parent / "imports").glob("*.sqlite3"))
                            ):
                                db = readonly(original)
                                try:
                                    available = {
                                        r[0] for r in db.execute("SELECT vehicle FROM vehicles")
                                    }
                                    keys = tuple(
                                        vehicle_key(sn)
                                        for sn in vehicles
                                        if vehicle_key(sn) in available
                                    )
                                    if not keys:
                                        continue
                                    output = directory / f"{owner}-source-{index}.sqlite3"
                                    snapshot(db, output, keys)
                                    sources.append(output)
                                finally:
                                    db.close()
                            return sources

                        evidence[owner] = await self.hass.async_add_executor_job(source_snapshots)
                        snapshots.append((owner, path))
                    package = directory / "ninebot-history.zip"
                    await self.hass.async_add_executor_job(pack, package, snapshots, evidence)
                    token = secrets.token_urlsafe(32)
                    register_download(self.hass)
                    self.hass.data[DOWNLOADS][token] = (self._archive_user_id, package)

                    async def cleanup() -> None:
                        self.hass.data[DOWNLOADS].pop(token, None)
                        await self.hass.async_add_executor_job(shutil.rmtree, directory, True)

                    self.hass.loop.call_later(600, lambda: self.hass.async_create_task(cleanup()))
                    url = async_sign_path(self.hass, URL + token, timedelta(minutes=10))
                    return self.async_show_form(
                        step_id="archive_download",
                        description_placeholders={"url": url},
                        data_schema=vol.Schema({}),
                    )
                except (ArchiveError, OSError, ValueError):
                    await self.hass.async_add_executor_job(shutil.rmtree, directory, True)
                    errors["base"] = "archive_invalid"
        return self.async_show_form(
            step_id="archive_export",
            errors=errors,
            data_schema=vol.Schema(
                {
                    vol.Required("vehicles"): SelectSelector(
                        SelectSelectorConfig(
                            options=choices, multiple=True, mode=SelectSelectorMode.DROPDOWN
                        )
                    )
                }
            ),
        )

    async def async_step_archive_download(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        return self.async_create_entry(title="", data=self._pending_archive_options())

    def _pending_archive_options(self) -> dict[str, Any]:
        return dict(getattr(self, "_pending_options", None) or self.config_entry.options)

    async def _archive_cleanup(self) -> None:
        directory, self._archive_directory = self._archive_directory, None
        self._archive_sources = None
        if directory:
            await self.hass.async_add_executor_job(shutil.rmtree, directory, True)

    async def async_step_archive_import(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if not await self._archive_admin():
            return self.async_abort(reason="archive_admin_required")
        errors = {}
        if user_input is not None:
            await self._archive_cleanup()
            self._archive_directory = Path(await self.hass.async_add_executor_job(tempfile.mkdtemp))
            try:

                def staged() -> list[tuple[str, Path]]:
                    assert self._archive_directory is not None
                    with process_uploaded_file(self.hass, user_input["file"]) as uploaded:
                        return unpack(uploaded, self._archive_directory / "accounts")

                self._archive_sources = await self.hass.async_add_executor_job(staged)
                return await self.async_step_archive_confirm()
            except (
                ArchiveError,
                OSError,
                ValueError,
                KeyError,
                TypeError,
                sqlite3.Error,
                zipfile.BadZipFile,
            ):
                await self._archive_cleanup()
                errors["base"] = "archive_invalid"
        return self.async_show_form(
            step_id="archive_import",
            errors=errors,
            data_schema=vol.Schema(
                {vol.Required("file"): FileSelector(FileSelectorConfig(accept=".zip"))}
            ),
        )

    async def async_step_archive_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if not await self._archive_admin():
            return self.async_abort(reason="archive_admin_required")
        if not self._archive_sources:
            return await self.async_step_archive_import()
        accounts = loaded_accounts(self.hass)
        choices: list[SelectOptionDict] = []
        errors = {}
        try:
            for owner, path in self._archive_sources:
                if owner not in accounts:
                    raise ValueError("Sign in to every package account before importing")
                actor = accounts[owner].runtime_data.coordinator.statistics.archive

                def inspect(actor: Any = actor, path: Path = path) -> list[tuple[str, str]]:
                    db = readonly(path)
                    try:
                        validate(actor, db)
                        from .archive_codec import decoded, restored_profile

                        return [
                            (
                                row[0],
                                restored_profile(decoded(row[1])).name if row[1] else "Vehicle",
                            )
                            for row in db.execute("SELECT vehicle,profile FROM vehicles")
                        ]
                    finally:
                        db.close()

                profiles = await self.hass.async_add_executor_job(inspect)
                choices.extend(
                    SelectOptionDict(
                        value=f"{owner}:{vehicle}", label=f"{accounts[owner].title} · {name}"
                    )
                    for vehicle, name in profiles
                )
            choices = list({choice["value"]: choice for choice in choices}.values())
            if user_input is not None:
                selections = user_input.get("vehicles", [])
                if (
                    not user_input.get("confirm")
                    or not selections
                    or not set(selections) <= {c["value"] for c in choices}
                ):
                    raise ValueError("Invalid import confirmation")
                if backup_active(self.hass):
                    return self.async_show_form(
                        step_id="archive_confirm",
                        errors={"base": "archive_busy"},
                        data_schema=self._archive_confirmation_schema(choices),
                    )
                results = []
                completed: set[str] = set()
                for owner, path in self._archive_sources:
                    if owner in completed:
                        continue
                    completed.add(owner)
                    selected = tuple(
                        value.split(":", 1)[1]
                        for value in selections
                        if value.startswith(owner + ":")
                    )
                    if not selected:
                        continue
                    entry = accounts[owner]
                    async with account_lock(self.hass, str(entry.data[CONF_BUSINESS_UID])):
                        stats = entry.runtime_data.coordinator.statistics
                        sources = tuple(
                            p for o, p in self._archive_sources if o == owner and p != path
                        )
                        result = await stats.archive.async_merge_archive(path, selected, sources)
                        profiles = await stats.archive.async_profiles()
                        stats._vehicles.update(profile.sn for profile in profiles)
                        await stats.async_refresh_projection()
                        for profile in await stats.archive.async_profiles():
                            stats._notify_change(profile.sn, None)
                        entry.runtime_data.coordinator.async_set_updated_data(
                            dict(entry.runtime_data.coordinator.data)
                        )
                    results.append(f"{entry.title}: +{result['rides']} / +{result['details']}")
                await self._archive_cleanup()
                return self.async_show_form(
                    step_id="archive_result",
                    description_placeholders={"result": "\n".join(results)},
                    data_schema=vol.Schema({}),
                )
        except (
            ArchiveError,
            OSError,
            ValueError,
            KeyError,
            TypeError,
            sqlite3.Error,
            zipfile.BadZipFile,
        ):
            errors["base"] = "archive_invalid"
        return self.async_show_form(
            step_id="archive_confirm",
            errors=errors,
            data_schema=self._archive_confirmation_schema(choices),
        )

    @staticmethod
    def _archive_confirmation_schema(choices: list[SelectOptionDict]) -> Any:
        return vol.Schema(
            {
                vol.Required("vehicles"): SelectSelector(
                    SelectSelectorConfig(
                        options=choices, multiple=True, mode=SelectSelectorMode.DROPDOWN
                    )
                ),
                vol.Required("confirm", default=False): bool,
            }
        )

    async def async_step_archive_result(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        return self.async_create_entry(title="", data=self._pending_archive_options())

    def async_remove(self) -> None:
        if self._archive_directory:
            self.hass.async_create_task(self._archive_cleanup())
        super().async_remove()
