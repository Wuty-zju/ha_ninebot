"""Typed per-entry runtime, separate from durable configuration."""

from asyncio import Lock
from dataclasses import dataclass, field

from homeassistant.config_entries import ConfigEntry

from .client import NinecliClient
from .coordinator import NinebotCoordinator
from .event_store import RideEventPipeline
from .identity import IdentityStore
from .session import SessionManager
from .storage import ModelStorage


@dataclass
class RuntimeData:
    client: NinecliClient
    coordinator: NinebotCoordinator
    session: SessionManager
    models: ModelStorage
    identity_conflicts: set[str] = field(default_factory=set)
    obsolete_entities_removed: int = 0
    standard_entities_enabled: int = 0
    configured_controls_enabled: int = 0
    events: RideEventPipeline | None = None
    statistics_import_lock: Lock = field(default_factory=Lock)
    statistics_import_pending: int = 0
    identities: IdentityStore | None = None


type NinebotConfigEntry = ConfigEntry[RuntimeData]
