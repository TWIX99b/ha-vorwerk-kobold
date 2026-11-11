"""Base entity for the Vorwerk Kobold integration."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import KoboldCoordinator, RobotData


class KoboldEntity(CoordinatorEntity[KoboldCoordinator]):
    """An entity belonging to one Kobold robot."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: KoboldCoordinator, robot_id: str, key: str) -> None:
        super().__init__(coordinator)
        self.robot_id = robot_id
        robot = coordinator.robots[robot_id]
        self._attr_unique_id = f"{robot.serial}_{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, robot.serial)},
            manufacturer="Vorwerk",
            model=robot.model or "Kobold",
            name=robot.name,
            serial_number=robot.serial,
            sw_version=robot.firmware,
        )

    @property
    def robot(self) -> RobotData:
        return self.coordinator.robots[self.robot_id]

    @property
    def available(self) -> bool:
        return (
            super().available
            and self.robot_id in self.coordinator.robots
            and self.coordinator.robot_available(self.robot_id)
        )
