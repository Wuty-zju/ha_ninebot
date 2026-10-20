"""Local synchronization entities backed exclusively by immutable cached views."""

from datetime import datetime
from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.const import PERCENTAGE
from homeassistant.core import callback
from homeassistant.helpers.entity import EntityCategory

from .const import CONF_DEBUG
from .debug_entities import DEBUG_SENSOR_KEYS
from .entity import NinebotEntity
from .runtime import NinebotConfigEntry

HISTORY_STATES = (
    "disabled",
    "paused",
    "storage_full",
    "storage_error",
    "needs_login",
    "waiting_connection",
    "vehicle_not_present",
    "not_started",
    "waiting_retry",
    "waiting_budget",
    "discovering",
    "preserving",
    "verifying",
    "incremental",
    "with_gaps",
    "synced",
)
HISTORY_SENSORS = (
    "history_sync_state",
    "history_scan_progress",
    "history_detail_progress",
    "history_sync_last_success",
)


class HistoryProgressSensor(NinebotEntity, SensorEntity):
    def __init__(self, entry: NinebotConfigEntry, sn: str, key: str) -> None:
        super().__init__(entry, sn, key, "sensor", "profile")
        self.key = key
        self._attr_entity_category = EntityCategory.DIAGNOSTIC
        if key in DEBUG_SENSOR_KEYS:
            self._attr_entity_registry_enabled_default = entry.options.get(CONF_DEBUG) is True
            self._attr_entity_registry_visible_default = entry.options.get(CONF_DEBUG) is True
        if key == "history_sync_state":
            self._attr_device_class = SensorDeviceClass.ENUM
            self._attr_options = list(HISTORY_STATES)
        elif key == "history_sync_last_success":
            self._attr_device_class = SensorDeviceClass.TIMESTAMP
        else:
            self._attr_native_unit_of_measurement = PERCENTAGE
            self._attr_suggested_display_precision = 0

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(
            self.coordinator.statistics.async_add_archive_listener(self.sn, self._archive_updated)
        )

    @callback
    def _archive_updated(self) -> None:
        self.async_write_ha_state()

    @property
    def available(self) -> bool:
        # A retained identity may be absent from an accepted account list. Only
        # the reason remains visible; archived data and actions stay guarded.
        if self.coordinator._stopping or self._identity_blocked or self.snapshot is None:
            return False
        if self.key == "history_sync_state" and not self.snapshot.present:
            return True
        return (
            self.coordinator.local_vehicle_available(self.sn)
            and self.sn in self.coordinator.preservation.progress
        )

    @property
    def native_value(self) -> str | int | datetime | None:
        if self.snapshot is not None and not self.snapshot.present:
            return "vehicle_not_present" if self.key == "history_sync_state" else None
        value = self.coordinator.preservation.progress.get(self.sn)
        if value is None:
            return None
        if (
            self.key == "history_sync_state"
            and self.sn in self.coordinator._suspect
            and value.state
            not in {"disabled", "paused", "needs_login", "storage_full", "storage_error"}
        ):
            return "waiting_connection"
        values: dict[str, str | int | datetime | None] = {
            "history_sync_state": value.state,
            "history_scan_progress": value.scan_percent,
            "history_detail_progress": value.detail_percent,
            "history_sync_last_success": value.last_success,
        }
        return values[self.key]

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        if self.snapshot is not None and not self.snapshot.present:
            if self.key != "history_sync_state":
                return {}
            discovery = self.coordinator.discovery_diagnostics()
            return {
                "vehicle_present": False,
                "vehicle_list_complete": discovery["complete"],
                "vehicle_list_last_success": discovery["succeeded_at"],
            }
        value = self.coordinator.preservation.progress.get(self.sn)
        if value is None:
            return {}
        audit = self.coordinator.preservation.audit.views.get(self.sn)
        reconstruction = self.coordinator.preservation.reconstruction.views.get(self.sn)
        return {
            "local_reconstruction_state": reconstruction.state if reconstruction else None,
            "local_reconstruction_total": reconstruction.total if reconstruction else None,
            "local_reconstruction_checked": reconstruction.checked if reconstruction else None,
            "local_reconstruction_repaired": reconstruction.repaired if reconstruction else None,
            "local_reconstruction_partially_repaired": reconstruction.partially_repaired
            if reconstruction
            else None,
            "local_reconstruction_blocked": reconstruction.blocked if reconstruction else None,
            "local_reconstruction_corrupt": reconstruction.corrupt if reconstruction else None,
            "local_reconstruction_searching_sources": reconstruction.searching
            if reconstruction
            else None,
            "local_reconstruction_source_copy_checks": reconstruction.source_copy_checks
            if reconstruction
            else None,
            "local_reconstruction_changed": reconstruction.changed if reconstruction else None,
            "local_audit_phase": audit.phase if audit else None,
            "local_audit_scope": audit.scope if audit else None,
            "local_audit_checked": audit.checked if audit else None,
            "local_audit_total": audit.total if audit else None,
            "local_audit_invalid": audit.invalid if audit else None,
            "local_audit_observed_at": audit.observed_at if audit else None,
            "scanned_months": value.scanned_months,
            "verified_months": value.verified_months,
            "total_months": value.total_months,
            "gap_months": value.gap_months,
            "discovered_rides": value.discovered,
            "saved_details": value.saved,
            "missing_details": value.missing,
            "unmapped_details": value.unmapped,
            "quality_pending": value.quality_pending,
            "reference_issues": value.reference_issues,
            "simplified_tracks": value.simplified,
            "discovery_complete": value.discovery_complete,
            "active_months": value.active_months,
            "next_request_at": value.next_request_at.isoformat() if value.next_request_at else None,
            "retry_at": value.retry_at.isoformat() if value.retry_at else None,
            "sync_phase": value.phase,
            "last_completed": value.last_completed.isoformat() if value.last_completed else None,
            "initial_gap_months": value.initial_gap_months,
            "resolved_gap_months": value.resolved_gap_months,
            "recorded_multi_page_months": value.multi_page_months,
            "real_cloud_pagination_verified": None,
        }
