"""Number platform for the Vorwerk Kobold integration (robot volume)."""

from __future__ import annotations

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.const import PERCENTAGE, EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import KoboldConfigEntry
from .entity import KoboldSettingEntity

PARALLEL_UPDATES = 1


async def async_setup_entry(
    hass: HomeAssistant,
    entry: KoboldConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data
    added: set[str] = set()

    @callback
    def _add_new() -> None:
        new = []
        for robot_id, robot in coordinator.robots.items():
            if robot_id not in added and "volume" in robot.settings.get("sounds", {}):
                added.add(robot_id)
                new.append(KoboldVolume(coordinator, robot_id))
        if new:
            async_add_entities(new)

    _add_new()
    entry.async_on_unload(coordinator.async_add_listener(_add_new))


class KoboldVolume(KoboldSettingEntity, NumberEntity):
    """Loudness of the robot's sounds and voice."""

    _setting = "sounds"
    _attr_translation_key = "volume"
    _attr_entity_category = EntityCategory.CONFIG
    _attr_native_min_value = 0
    _attr_native_max_value = 100
    _attr_native_step = 1
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_mode = NumberMode.SLIDER

    def __init__(self, coordinator, robot_id: str) -> None:
        super().__init__(coordinator, robot_id, "volume")

    @property
    def native_value(self) -> float | None:
        value = self.setting.get("volume")
        return float(value) if isinstance(value, (int, float)) else None

    async def async_set_native_value(self, value: float) -> None:
        await self.coordinator.async_set_setting(self.robot_id, "sounds", volume=int(round(value)))
