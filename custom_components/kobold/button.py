"""Button platform for the Vorwerk Kobold integration."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import KoboldConfigEntry
from .entity import KoboldEntity

PARALLEL_UPDATES = 1


async def async_setup_entry(
    hass: HomeAssistant,
    entry: KoboldConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data
    async_add_entities(KoboldLocateButton(coordinator, rid) for rid in coordinator.robots)


class KoboldLocateButton(KoboldEntity, ButtonEntity):
    """Let the robot beep so it can be found."""

    _attr_translation_key = "locate"

    def __init__(self, coordinator, robot_id: str) -> None:
        super().__init__(coordinator, robot_id, "locate")

    async def async_press(self) -> None:
        await self.coordinator.async_send(self.robot_id, "utilities.find_me")
