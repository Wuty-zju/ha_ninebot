"""Typed per-entry runtime, separate from durable configuration."""

from dataclasses import dataclass, field

from homeassistant.config_entries import ConfigEntry

from .client import NinecliClient
from .coordinator import NinebotCoordinator
from .event_store import RideEventPipeline
from .session import SessionManager
from .storage import ModelStorage


@dataclass
class RuntimeData:
    client: NinecliClient
    coordinator: NinebotCoordinator
    session: SessionManager
    models: ModelStorage
    identity_conflicts: set[str] = field(default_factory=set)
    events: RideEventPipeline | None = None


type NinebotConfigEntry = ConfigEntry[RuntimeData]
