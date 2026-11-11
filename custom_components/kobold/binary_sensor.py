"""Binary sensor platform for the Vorwerk Kobold integration."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import dt as dt_util

from .ble import bluetooth_loaded
from .coordinator import KoboldConfigEntry, KoboldCoordinator, RobotData
from .entity import KoboldEntity, KoboldVk7Entity

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
    entities: list[BinarySensorEntity] = [
        KoboldBinarySensor(coordinator, rid, description)
        for rid in coordinator.robots
        for description in BINARY_SENSORS
    ]
    if bluetooth_loaded(hass):
        entities.extend(
            KoboldVk7Presence(coordinator, pid, device.mac)
            for pid, device in coordinator.vk7.items()
            if device.mac
        )
    async_add_entities(entities)


class KoboldBinarySensor(KoboldEntity, BinarySensorEntity):
    entity_description: KoboldBinaryDescription

    def __init__(self, coordinator, robot_id: str, description: KoboldBinaryDescription) -> None:
        super().__init__(coordinator, robot_id, description.key)
        self.entity_description = description

    @property
    def is_on(self) -> bool | None:
        return self.entity_description.value(self.robot)


class KoboldVk7Presence(KoboldVk7Entity, BinarySensorEntity):
    """VK7 seen by a Bluetooth adapter or proxy (it advertises while charging, too)."""

    _attr_device_class = BinarySensorDeviceClass.PRESENCE
    _attr_translation_key = "vk7_in_range"

    def __init__(self, coordinator: KoboldCoordinator, product_id: str, mac: str) -> None:
        super().__init__(coordinator, product_id, "in_range")
        self._mac = mac
        self._attr_is_on = False
        self._details: dict[str, Any] = {}

    @property
    def available(self) -> bool:
        return self.product_id in self.coordinator.vk7

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return self._details

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        from homeassistant.components import bluetooth  # noqa: PLC0415

        self._attr_is_on = bluetooth.async_address_present(self.hass, self._mac, connectable=False)
        if (info := bluetooth.async_last_service_info(self.hass, self._mac, connectable=False)):
            self._remember(info)
        self.async_on_remove(
            bluetooth.async_register_callback(
                self.hass,
                self._async_seen,
                bluetooth.BluetoothCallbackMatcher(address=self._mac, connectable=False),
                bluetooth.BluetoothScanningMode.PASSIVE,
            )
        )
        self.async_on_remove(
            bluetooth.async_track_unavailable(
                self.hass, self._async_gone, self._mac, connectable=False
            )
        )

    def _remember(self, info: Any) -> None:
        self._details = {"source": info.source, "rssi": info.rssi}

    @callback
    def _async_seen(self, info: Any, _change: Any) -> None:
        if self._attr_is_on:
            return  # only write on changes – advertisements arrive every few seconds
        self._attr_is_on = True
        self._remember(info)
        self._details["since"] = dt_util.utcnow().isoformat()
        self.async_write_ha_state()

    @callback
    def _async_gone(self, _info: Any) -> None:
        self._attr_is_on = False
        self._details["since"] = dt_util.utcnow().isoformat()
        self.async_write_ha_state()
