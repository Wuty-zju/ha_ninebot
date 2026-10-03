"""Registry-aware identities and shared memory-only entity behavior."""

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
        super().__init__(entry.runtime_data.coordinator)
        self.entry = entry
        self.sn = sn
        self.group = group
        registry = er.async_get(self.coordinator.hass)
        candidates = [f"ninebot_{sn}_{alias}".lower() for alias in (key, *aliases)]
        candidates.extend(f"{sn}_{alias}" for alias in (key, *aliases))
        found = []
        for candidate in candidates:
            if entity_id := registry.async_get_entity_id(platform, DOMAIN, candidate):
                row = registry.async_get(entity_id)
                if row and row.config_entry_id == entry.entry_id:
                    found.append(candidate)
        # If both legacy identities exist, one is active and the other is a
        # compatibility entity. Never delete either to claim its ID.
        issue_id = (
            f"identity_{entry.entry_id}_{platform}_"
            f"{hashlib.sha256(sn.encode()).hexdigest()[:12]}_{key}"
        )
        if len(found) > 1:
            entry.runtime_data.identity_conflicts.add(issue_id)
            ir.async_create_issue(
                self.coordinator.hass,
                DOMAIN,
                issue_id,
                is_fixable=False,
                severity=ir.IssueSeverity.WARNING,
                translation_key="entity_identity_conflict",
            )
        elif issue_id in entry.runtime_data.identity_conflicts:
            entry.runtime_data.identity_conflicts.discard(issue_id)
            ir.async_delete_issue(self.coordinator.hass, DOMAIN, issue_id)
        self._attr_unique_id = unique_id or (found[0] if found else f"{sn}_{key}")
        self._attr_translation_key = key

    @property
    def snapshot(self) -> VehicleSnapshot | None:
        return self.coordinator.data.get(self.sn)

    @property
    def available(self) -> bool:
        return self.coordinator.fresh(self.sn, self.group)

    @property
    def device_info(self) -> DeviceInfo:
        profile = self.snapshot.profile if self.snapshot else None
        return DeviceInfo(
            identifiers={(DOMAIN, self.sn)},
            manufacturer=MANUFACTURER,
            name=profile.name if profile else self.sn,
            model=profile.model if profile else "Ninebot",
        )


def async_setup_dynamic(
    hass: HomeAssistant,
    entry: NinebotConfigEntry,
    add: AddEntitiesCallback,
    factory: Callable[[str], Iterable[Entity]],
) -> set[str]:
    """Discover entities for new vehicles/batteries/model generations."""
    seen: set[str] = set()

    @callback
    def discover() -> None:
        entities = []
        for sn in entry.runtime_data.coordinator.data:
            for entity in factory(sn):
                if entity.unique_id and entity.unique_id not in seen:
                    seen.add(entity.unique_id)
                    entities.append(entity)
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
        for domain, sn in device.identifiers:
            if domain != DOMAIN:
                continue
            for key in known_keys:
                if row.unique_id in {f"ninebot_{sn}_{key}".lower(), f"{sn}_{key}"}:
                    yield sn, key, row.unique_id
