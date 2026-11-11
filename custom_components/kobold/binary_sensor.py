"""Binary sensor platform for the Vorwerk Kobold integration."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import KoboldConfigEntry, RobotData
from .entity import KoboldEntity

PARALLEL_UPDATES = 0


@dataclass(frozen=True, kw_only=True)
class KoboldBinaryDescription(BinarySensorEntityDescription):
    value: Callable[[RobotData], bool | None]


BINARY_SENSORS: tuple[KoboldBinaryDescription, ...] = (
    KoboldBinaryDescription(
        key="charging",
        device_class=BinarySensorDeviceClass.BATTERY_CHARGING,
        value=lambda r: r.state.is_charging if r.state else None,
    ),
    KoboldBinaryDescription(
        key="docked",
        translation_key="docked",
        value=lambda r: r.state.is_docked if r.state else None,
    ),
    KoboldBinaryDescription(
        key="problem",
        device_class=BinarySensorDeviceClass.PROBLEM,
        value=lambda r: bool(r.state.active_errors or r.state.base_error) if r.state else None,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: KoboldConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data
    async_add_entities(
        KoboldBinarySensor(coordinator, rid, description)
        for rid in coordinator.robots
        for description in BINARY_SENSORS
    )


class KoboldBinarySensor(KoboldEntity, BinarySensorEntity):
    entity_description: KoboldBinaryDescription

    def __init__(self, coordinator, robot_id: str, description: KoboldBinaryDescription) -> None:
        super().__init__(coordinator, robot_id, description.key)
        self.entity_description = description

    @property
    def is_on(self) -> bool | None:
        return self.entity_description.value(self.robot)
