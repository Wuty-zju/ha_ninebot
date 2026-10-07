"""Registry-aware identities and shared memory-only entity behavior."""

import asyncio
import hashlib
from collections.abc import Callable, Iterable

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.entity import DeviceInfo, Entity
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, MANUFACTURER
from .coordinator import NinebotCoordinator
from .demand import entity_context
from .identity import device_sn, legacy_uids, scoped_uid
from .models import VehicleSnapshot
from .runtime import NinebotConfigEntry


class NinebotEntity(CoordinatorEntity[NinebotCoordinator]):
    _attr_has_entity_name = True

    def __init__(
        self,
        entry: NinebotConfigEntry,
        sn: str,
        key: str,
        platform: str,
        group: str,
        aliases: tuple[str, ...] = (),
        *,
        unique_id: str | None = None,
    ) -> None:
        super().__init__(
            entry.runtime_data.coordinator, context=entity_context(sn, platform, key, group)
        )
        self.entry = entry
        self.sn = sn
        self._data_group = group
        registry = er.async_get(self.coordinator.hass)
        candidates = [scoped_uid(entry, sn, key)]
        candidates.extend(uid for alias in (key, *aliases) for uid in legacy_uids(sn, alias))
        found = []
        ambiguous_owner = False
        for candidate in candidates:
            if entity_id := registry.async_get_entity_id(platform, DOMAIN, candidate):
                row = registry.async_get(entity_id)
                if row and row.config_entry_id == entry.entry_id:
                    device = (
                        dr.async_get(self.coordinator.hass).async_get(row.device_id)
                        if row.device_id
                        else None
                    )
                    if device is not None and device_sn(entry, device) != sn:
                        ambiguous_owner = True
                    found.append(candidate)
        # If both legacy identities exist, one is active and the other is a
        # compatibility entity. Never delete either to claim its ID.
        issue_id = (
            f"identity_{entry.entry_id}_{platform}_"
            f"{hashlib.sha256(sn.encode()).hexdigest()[:12]}_{key}"
        )
        if len(found) > 1 or ambiguous_owner:
            entry.runtime_data.identity_conflicts.add(issue_id)
            ir.async_create_issue(
                self.coordinator.hass,
                DOMAIN,
                issue_id,
                is_fixable=False,
                severity=ir.IssueSeverity.WARNING,
                translation_key="entity_identity_conflict",
            )
        else:
            entry.runtime_data.identity_conflicts.discard(issue_id)
            ir.async_delete_issue(self.coordinator.hass, DOMAIN, issue_id)
        identities = entry.runtime_data.identities
        if identities and sn not in identities.seeds:
            identities = None  # Unmatched legacy row remains unavailable, not a new identity.
        preferred = identities.unique_id(sn, key) if identities else f"{sn}_{key}"
        occupied = registry.async_get_entity_id(platform, DOMAIN, preferred)
        occupied_row = registry.async_get(occupied) if occupied else None
        if occupied_row and occupied_row.config_entry_id != entry.entry_id:
            preferred = scoped_uid(entry, sn, key)
        self._attr_unique_id = unique_id or (found[0] if found else preferred)
        self._identity_blocked = ambiguous_owner
        if identities:
            target = identities.seeds[sn].entity_id(platform, key)
            collision_issue = (
                f"entity_id_collision_{entry.entry_id}_{digest_key(sn, platform, key)}"
            )
            registered = registry.async_get_entity_id(platform, DOMAIN, self._attr_unique_id)
            if not registered and (
                registry.async_get(target) or self.coordinator.hass.states.get(target)
            ):
                self._identity_blocked = True
                entry.runtime_data.identity_conflicts.add(collision_issue)
                ir.async_create_issue(
                    self.coordinator.hass,
                    DOMAIN,
                    collision_issue,
                    is_fixable=False,
                    severity=ir.IssueSeverity.WARNING,
                    translation_key="entity_id_migration_conflict",
                )
            else:
                entry.runtime_data.identity_conflicts.discard(collision_issue)
                ir.async_delete_issue(self.coordinator.hass, DOMAIN, collision_issue)
                if not registered:
                    self.entity_id = target
        self._attr_translation_key = key

    @property
    def snapshot(self) -> VehicleSnapshot | None:
        return self.coordinator.data.get(self.sn)

    @property
    def available(self) -> bool:
        return self.coordinator.fresh(self.sn, self._data_group)

    @property
    def device_info(self) -> DeviceInfo:
        profile = self.snapshot.profile if self.snapshot else None
        return DeviceInfo(
            identifiers={
                (
                    DOMAIN,
                    self.entry.runtime_data.identities.seeds[self.sn].device_identifier
                    if self.entry.runtime_data.identities
                    and self.sn in self.entry.runtime_data.identities.seeds
                    else self.sn,
                )
            },
            manufacturer=MANUFACTURER,
            name=profile.name if profile else self.sn,
            model=profile.model if profile else "Ninebot",
            serial_number=self.sn,
        )


def digest_key(sn: str, platform: str, key: str) -> str:
    return hashlib.sha256(f"{sn}:{platform}:{key}".encode()).hexdigest()[:16]


def async_setup_dynamic(
    hass: HomeAssistant,
    entry: NinebotConfigEntry,
    add: AddEntitiesCallback,
    factory: Callable[[str], Iterable[Entity]],
    *,
    signature: Callable[[VehicleSnapshot], object] | None = None,
) -> set[str]:
    """Construct only when vehicle/entity topology changes, not every poll."""
    seen: set[str] = set()
    versions: dict[str, object] = {}
    preparation: asyncio.Task[None] | None = None

    async def prepare() -> None:
        from .registry import async_migrate_entity_ids

        identities = entry.runtime_data.identities
        if identities:
            await identities.async_prepare(
                s.profile for s in entry.runtime_data.coordinator.data.values()
            )
            await async_migrate_entity_ids(hass, entry)
        if not entry.runtime_data.coordinator._stopping:
            discover()

    @callback
    def cancel_preparation() -> None:
        if preparation:
            preparation.cancel()

    entry.async_on_unload(cancel_preparation)

    @callback
    def discover() -> None:
        nonlocal preparation
        identities = entry.runtime_data.identities
        if identities and any(
            sn not in identities.seeds for sn in entry.runtime_data.coordinator.data
        ):
            if preparation is None or preparation.done():
                preparation = hass.async_create_task(prepare(), "ninebot identity discovery")
            return
        entities = []
        for sn in entry.runtime_data.coordinator.data:
            snapshot = entry.runtime_data.coordinator.data[sn]
            version = signature(snapshot) if signature else sn
            if sn in versions and versions[sn] == version:
                continue
            blocked = False
            for entity in factory(sn):
                if getattr(entity, "_identity_blocked", False):
                    blocked = True
                    continue
                if entity.unique_id and entity.unique_id not in seen:
                    seen.add(entity.unique_id)
                    entities.append(entity)
            if not blocked:
                versions[sn] = version
        if entities:
            add(entities)

    discover()
    entry.async_on_unload(entry.runtime_data.coordinator.async_add_listener(discover))
    return seen


def legacy_rows(
    hass: HomeAssistant, entry: NinebotConfigEntry, platform: str, known_keys: set[str]
) -> Iterable[tuple[str, str, str]]:
    """Only recognized keys on this entry's known device identifiers."""
    devices = dr.async_get(hass)
    registry = er.async_get(hass)
    for row in er.async_entries_for_config_entry(registry, entry.entry_id):
        if row.domain != platform or not row.device_id:
            continue
        device = devices.async_get(row.device_id)
        if not device:
            continue
        sn = device_sn(entry, device)
        if sn:
            for key in known_keys:
                if row.unique_id in {*legacy_uids(sn, key), scoped_uid(entry, sn, key)}:
                    yield sn, key, row.unique_id


@callback
def async_audit_device_identities(hass: HomeAssistant, entry: NinebotConfigEntry) -> None:
    """Warn about unmatched legacy identities without guessing or deleting."""
    prefix = f"legacy_device_{entry.entry_id}_"
    expected = set()
    for device in dr.async_entries_for_config_entry(dr.async_get(hass), entry.entry_id):
        for sn in (device_sn(entry, device),):
            if sn is None or sn in entry.runtime_data.coordinator.data:
                continue
            issue_id = f"{prefix}{hashlib.sha256(sn.encode()).hexdigest()[:12]}"
            expected.add(issue_id)
            entry.runtime_data.identity_conflicts.add(issue_id)
            ir.async_create_issue(
                hass,
                DOMAIN,
                issue_id,
                is_fixable=False,
                severity=ir.IssueSeverity.WARNING,
                translation_key="legacy_device_unmatched",
            )
    # Repair issues persist across reload; the new runtime set starts empty.
    # Reconcile the persisted issues as well as this runtime's bookkeeping.
    previous = {
        issue_id
        for domain, issue_id in ir.async_get(hass).issues
        if domain == DOMAIN and issue_id.startswith(prefix)
    }
    for issue_id in previous - expected:
        entry.runtime_data.identity_conflicts.discard(issue_id)
        ir.async_delete_issue(hass, DOMAIN, issue_id)
