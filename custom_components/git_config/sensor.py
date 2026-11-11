"""Sensors: commits behind/ahead, pending changes, last fetch, last error."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import GitConfigEntry, GitCoordinator
from .entity import GitEntity


@dataclass(frozen=True, kw_only=True)
class GitSensorDescription(SensorEntityDescription):
    """Sensor reading a value from the coordinator."""

    value_fn: Callable[[GitCoordinator], int | str | datetime | None]


SENSORS = (
    GitSensorDescription(
        key="behind",
        translation_key="behind",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda c: c.data.behind,
    ),
    GitSensorDescription(
        key="ahead",
        translation_key="ahead",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda c: c.data.ahead,
    ),
    GitSensorDescription(
        key="pending",
        translation_key="pending",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda c: c.data.pending,
    ),
    GitSensorDescription(
        key="last_fetch",
        translation_key="last_fetch",
        device_class=SensorDeviceClass.TIMESTAMP,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda c: c.last_fetch,
    ),
    GitSensorDescription(
        key="last_error",
        translation_key="last_error",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda c: c.last_error,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: GitConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up sensors."""
    async_add_entities(GitSensor(entry.runtime_data, d) for d in SENSORS)


class GitSensor(GitEntity, SensorEntity):
    """A Config Repo sensor."""

    entity_description: GitSensorDescription

    def __init__(self, coordinator: GitCoordinator, description: GitSensorDescription) -> None:
        """Initialize."""
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def native_value(self) -> int | str | datetime | None:
        """Return the value."""
        return self.entity_description.value_fn(self.coordinator)
