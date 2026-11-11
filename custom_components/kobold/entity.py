"""Base entity for the Vorwerk Kobold integration."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import KoboldCoordinator, RobotData
from .protocol import Vk7Data


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


class KoboldSettingEntity(KoboldEntity):
    """An entity backed by one cloud setting of a robot (e.g. ``sounds``)."""

    _setting: str

    @property
    def setting(self) -> dict:
        return self.robot.settings.get(self._setting) or {}

    @property
    def available(self) -> bool:
        return super().available and self._setting in self.robot.settings


class KoboldVk7Entity(CoordinatorEntity[KoboldCoordinator]):
    """An entity of a VK7 stick vacuum (data synced by the MyKobold app)."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: KoboldCoordinator, product_id: str, key: str) -> None:
        super().__init__(coordinator)
        self.product_id = product_id
        device = coordinator.vk7[product_id]
        self._attr_unique_id = f"vk7_{device.serial}_{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"vk7_{device.serial}")},
            manufacturer="Vorwerk",
            model="Kobold VK7",
            name=device.name,
            serial_number=device.serial,
        )

    @property
    def device(self) -> Vk7Data:
        return self.coordinator.vk7[self.product_id]

    @property
    def available(self) -> bool:
        return super().available and self.product_id in self.coordinator.vk7
