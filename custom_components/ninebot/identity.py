"""Private frozen naming seeds and recoverable public-registry rename plans."""

import asyncio
import hashlib
import re
from collections import Counter
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import CoreState, HomeAssistant
from homeassistant.exceptions import ConfigEntryError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.storage import Store
from homeassistant.util import json as json_util
from homeassistant.util import slugify
from homeassistant.util.file import WriteError

from .compat import device_by_identifier, device_entry_ids, is_child_device
from .const import CONF_ACCOUNT, CONF_BUSINESS_UID, DOMAIN
from .models import VehicleProfile

IDENTITY_VERSION = "entity_identity_version"
IDENTITY_INITIALIZED = "entity_identity_initialized"
MAX_VEHICLES = 1000
MAX_MIGRATIONS = 10000
STATUSES = {"planned", "renamed", "unchanged", "protected", "conflict", "removed"}


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()[:16]


def ascii_slug(value: str, fallback: str, limit: int = 64) -> str:
    """No transliteration: retain ASCII content and disambiguate lossy inputs."""
    result = re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")
    if not result:
        return f"{fallback}_{digest(value)}"
    if not value.isascii() or len(result) > limit or not re.fullmatch(r"[A-Za-z0-9_]+", value):
        return f"{result[: limit - 17].rstrip('_')}_{digest(value)}"
    return result


def account_scope(entry: ConfigEntry) -> str:
    return digest(str(entry.data.get(CONF_BUSINESS_UID) or entry.entry_id))


def scoped_uid(entry: ConfigEntry, sn: str, key: str) -> str:
    return f"account_{account_scope(entry)}_{sn}_{key}"


def semantic_key(key: str) -> str:
    return key.removesuffix("_raw")


def legacy_uids(sn: str, key: str) -> tuple[str, str]:
    return f"ninebot_{sn}_{key}".lower(), f"{sn}_{key}"


def device_sn(entry: ConfigEntry, device: object) -> str | None:
    """Decode only this account's namespace or a single unambiguous legacy SN."""
    if device_entry_ids(device) != frozenset({entry.entry_id}) or is_child_device(device):
        return None
    identifiers = [
        value for domain, value in getattr(device, "identifiers", ()) if domain == DOMAIN
    ]
    if len(identifiers) != 1:
        return None
    value = identifiers[0]
    prefix = f"account:{account_scope(entry)}:"
    if value.startswith(prefix):
        return value[len(prefix) :] or None
    return value if not value.startswith("account:") else None


@dataclass(frozen=True)
class IdentitySeed:
    object_prefix: str
    device_identifier: str
    legacy_uids: bool

    def entity_id(self, platform: str, key: str) -> str:
        suffix = ascii_slug(semantic_key(key), "field", 80)
        return f"{platform}.{self.object_prefix}_{suffix}"


class IdentityStorage(Store[dict[str, Any]]):
    """A failed journal write must abort registry mutation, never log and return."""

    async def _async_write_data(self, data: dict) -> None:
        try:
            await super()._async_write_data(data)
        except (WriteError, json_util.SerializationError) as err:
            # Store catches these two exceptions. Convert them to an explicit
            # failure so callers cannot mistake a failed write for durable intent.
            raise OSError("identity storage write failed") from err


class IdentityStore:
    """Seeds/rename metadata only; never credentials, telemetry or raw JSON.

    Save the intent before renaming. HA itself persists registry changes and
    migrates Recorder metadata; restart reconciles by registry ID, not old name.
    """

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.hass = hass
        self.entry = entry
        self.store = IdentityStorage(hass, 1, f"ninebot.{entry.entry_id}.identity", private=True)
        self.seeds: dict[str, IdentitySeed] = {}
        self.migrations: dict[str, dict[str, str]] = {}
        self._lock = asyncio.Lock()
        self._issue = f"entity_id_migration_{entry.entry_id}"

    async def async_load(self) -> None:
        try:
            existed = await self.hass.async_add_executor_job(Path(self.store.path).exists)
            raw = await self.store.async_load()
            if raw is None:
                if existed or self.entry.data.get(IDENTITY_INITIALIZED) is True:
                    raise ValueError("missing established identity storage")
                return
            if (
                not isinstance(raw, dict)
                or type(raw.get("version")) is not int
                or raw["version"] != 1
            ):
                raise ValueError("identity schema")
            seeds, migrations = raw["seeds"], raw["migrations"]
            if not isinstance(seeds, dict) or len(seeds) > MAX_VEHICLES:
                raise ValueError("identity seeds")
            if not isinstance(migrations, dict) or len(migrations) > MAX_MIGRATIONS:
                raise ValueError("identity migrations")
            for sn, value in seeds.items():
                if not isinstance(sn, str) or not isinstance(value, dict):
                    raise ValueError("identity seed")
                prefix, identifier, legacy = (
                    value["object_prefix"],
                    value["device_identifier"],
                    value["legacy_uids"],
                )
                if (
                    not isinstance(prefix, str)
                    or not re.fullmatch(r"[a-z0-9_]{1,155}", prefix)
                    or not isinstance(identifier, str)
                    or identifier not in {sn, f"account:{account_scope(self.entry)}:{sn}"}
                    or type(legacy) is not bool
                ):
                    raise ValueError("identity seed fields")
                self.seeds[sn] = IdentitySeed(prefix, identifier, legacy)
            for row_id, value in migrations.items():
                if not isinstance(row_id, str) or not isinstance(value, dict):
                    raise ValueError("identity migration")
                keys = {
                    "old",
                    "new",
                    "uid",
                    "status",
                    "reason",
                    "before_modified_at",
                    "renamed_modified_at",
                }
                if (
                    set(value) != keys
                    or not all(isinstance(v, str) and len(v) <= 1024 for v in value.values())
                    or value["status"] not in STATUSES
                ):
                    raise ValueError("identity migration fields")
                self.migrations[row_id] = dict(value)
        except (OSError, ValueError, KeyError, TypeError, NotImplementedError) as err:
            self._repair("entity_id_storage_invalid")
            raise ConfigEntryError(
                translation_domain=DOMAIN, translation_key="entity_id_storage_invalid"
            ) from err

    async def _save(self) -> None:
        if self.hass.state is CoreState.stopping:
            raise OSError("identity storage cannot be committed during shutdown")
        task = asyncio.create_task(
            self.store.async_save(
                {
                    "version": 1,
                    "seeds": {sn: asdict(seed) for sn, seed in self.seeds.items()},
                    "migrations": {key: dict(row) for key, row in self.migrations.items()},
                }
            )
        )
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            await task
            raise
        except OSError:
            self._repair("entity_id_storage_invalid")
            raise

    def _repair(self, key: str = "entity_id_migration_conflict") -> None:
        ir.async_create_issue(
            self.hass,
            DOMAIN,
            self._issue,
            is_fixable=False,
            severity=ir.IssueSeverity.WARNING,
            translation_key=key,
        )

    async def async_prepare(self, profiles: Iterable[VehicleProfile]) -> None:
        async with self._lock:
            added = []
            for profile in profiles:
                if profile.sn in self.seeds:
                    continue
                if len(self.seeds) >= MAX_VEHICLES:
                    self._repair("entity_id_storage_invalid")
                    raise ConfigEntryError(
                        translation_domain=DOMAIN, translation_key="entity_id_storage_invalid"
                    )
                model = "_".join(re.findall(r"[A-Za-z0-9]+", profile.model))
                vehicle = model or (profile.name if profile.name.isascii() else "vehicle")
                prefix = "_".join(
                    (
                        ascii_slug(str(self.entry.data.get(CONF_ACCOUNT, "")), "account", 40),
                        ascii_slug(vehicle, "vehicle", 40),
                        ascii_slug(profile.sn, "serial", 64),
                    )
                )
                legacy = self.entry.data.get(IDENTITY_VERSION) != 1
                old_device = device_by_identifier(
                    dr.async_get(self.hass), self.entry.entry_id, profile.sn
                )
                owned = old_device is not None and device_sn(self.entry, old_device) == profile.sn
                if old_device is not None and not owned:
                    legacy = False
                identifier = (
                    profile.sn
                    if owned or (legacy and old_device is None)
                    else f"account:{account_scope(self.entry)}:{profile.sn}"
                )
                self.seeds[profile.sn] = IdentitySeed(prefix, identifier, legacy)
                added.append(profile.sn)
            if added:
                try:
                    await self._save()
                except OSError:
                    for sn in added:
                        self.seeds.pop(sn, None)
                    raise
                if self.entry.data.get(IDENTITY_INITIALIZED) is not True:
                    self.hass.config_entries.async_update_entry(
                        self.entry, data={**self.entry.data, IDENTITY_INITIALIZED: True}
                    )

    def unique_id(self, sn: str, key: str) -> str:
        seed = self.seeds[sn]
        return f"{sn}_{key}" if seed.legacy_uids else scoped_uid(self.entry, sn, key)

    def export(self, sn: str) -> dict[str, Any]:
        seed = self.seeds[sn]
        registry = er.async_get(self.hass)
        row_ids = {
            row.id
            for row in er.async_entries_for_config_entry(registry, self.entry.entry_id)
            if row.device_id
            and (device := dr.async_get(self.hass).async_get(row.device_id))
            and device_sn(self.entry, device) == sn
        }
        return {
            "version": 1,
            "object_prefix": seed.object_prefix,
            "migrations": [
                {"registry_id": key, **row}
                for key, row in self.migrations.items()
                if key in row_ids
            ],
            "reference_warning": (
                "Update YAML, templates and external dashboards using renamed "
                "entity IDs; no automatic rewrite is promised."
            ),
        }

    def diagnostics(self) -> dict[str, Any]:
        return {
            "seed_count": len(self.seeds),
            "migration_count": len(self.migrations),
            "statuses": dict(Counter(row["status"] for row in self.migrations.values())),
        }

    async def async_migrate(self, candidates: Iterable[tuple[er.RegistryEntry, str, str]]) -> None:
        """Preflight the full set and journal before touching the registry."""
        async with self._lock:
            registry = er.async_get(self.hass)
            existing = {
                row.id: row
                for row in er.async_entries_for_config_entry(registry, self.entry.entry_id)
            }
            plans: dict[str, dict[str, str]] = {}
            candidates = list(candidates)
            eligible = {row.id for row, _, _ in candidates}
            unrecorded = sum(row.id not in self.migrations for row, _, _ in candidates)
            if len(self.migrations) + unrecorded > MAX_MIGRATIONS:
                self._repair("entity_id_storage_invalid")
                raise ConfigEntryError(
                    translation_domain=DOMAIN, translation_key="entity_id_storage_invalid"
                )
            for row, sn, key in candidates:
                if row.id in self.migrations:
                    continue
                new = self.seeds[sn].entity_id(row.domain, key)
                device = dr.async_get(self.hass).async_get(row.device_id) if row.device_id else None
                generated = False
                if (
                    device
                    and row.has_entity_name
                    and row.original_name
                    and row.suggested_object_id is None
                ):
                    original_object = slugify(
                        f"{device.name_by_user or device.name} {row.original_name}"
                    )
                    expected = f"{row.domain}.{original_object}"
                    generated = row.entity_id == expected
                status = (
                    "unchanged" if row.entity_id == new else "planned" if generated else "protected"
                )
                plans[row.id] = {
                    "old": row.entity_id,
                    "new": new,
                    "uid": row.unique_id,
                    "status": status,
                    "reason": "generated" if generated else "custom_or_unverifiable",
                    "before_modified_at": row.modified_at.isoformat(),
                    "renamed_modified_at": "",
                }
            targets = Counter(row["new"] for row in plans.values())
            for planned in plans.values():
                if planned["status"] == "planned" and (
                    targets[planned["new"]] > 1
                    or registry.async_get(planned["new"]) is not None
                    or self.hass.states.get(planned["new"]) is not None
                ):
                    planned.update(status="conflict", reason="occupied_target")
            self.migrations.update(plans)
            if plans or any(plan["status"] == "planned" for plan in self.migrations.values()):
                await self._save()
            changed = False
            for row_id, plan in self.migrations.items():
                current = existing.get(row_id)
                # Registry uses delayed persistence. If it restored the exact
                # pre-rename row, redo our persisted intent. A later user rename
                # has a different modified_at and must never be overwritten.
                if (
                    plan["status"] == "renamed"
                    and current
                    and current.entity_id == plan["old"]
                    and current.modified_at.isoformat() == plan["before_modified_at"]
                ):
                    plan["status"] = "planned"
                if plan["status"] != "planned":
                    continue
                if current is None:
                    plan.update(status="removed", reason="registry_row_missing")
                elif (
                    row_id not in eligible
                    or current.unique_id != plan["uid"]
                    or current.entity_id
                    not in (
                        plan["old"],
                        plan["new"],
                    )
                ):
                    plan.update(status="protected", reason="registry_changed_after_plan")
                elif current.entity_id == plan["new"]:
                    plan.update(status="renamed", reason="recovered")
                elif registry.async_get(plan["new"]) or self.hass.states.get(plan["new"]):
                    plan.update(status="conflict", reason="occupied_target")
                else:
                    updated = registry.async_update_entity(
                        current.entity_id, new_entity_id=plan["new"]
                    )
                    plan["renamed_modified_at"] = updated.modified_at.isoformat()
                    plan.update(status="renamed", reason="public_registry_api")
                changed = True
            if changed:
                await self._save()
            if any(plan["status"] == "conflict" for plan in self.migrations.values()):
                self._repair()
            else:
                ir.async_delete_issue(self.hass, DOMAIN, self._issue)

    async def async_remove(self) -> None:
        await self.store.async_remove()
