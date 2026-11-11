"""Button platform for the Vorwerk Kobold integration."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import KoboldConfigEntry
from .entity import KoboldEntity, KoboldVk7Entity
from .protocol import Vk7Tracker

PARALLEL_UPDATES = 1


async def async_setup_entry(
    hass: HomeAssistant,
    entry: KoboldConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data
    async_add_entities(KoboldLocateButton(coordinator, rid) for rid in coordinator.robots)

    added: set[tuple[str, str]] = set()

    @callback
    def _add_trackers() -> None:
        new = []
        for pid, device in coordinator.vk7.items():
            for tracker in device.trackers:
                if (pid, tracker.tracker_id) not in added:
                    added.add((pid, tracker.tracker_id))
                    new.append(KoboldVk7TrackerReset(coordinator, pid, tracker))
        if new:
            async_add_entities(new)

    _add_trackers()
    entry.async_on_unload(coordinator.async_add_listener(_add_trackers))


class KoboldLocateButton(KoboldEntity, ButtonEntity):
    """Let the robot beep so it can be found."""

    _attr_translation_key = "locate"

    def __init__(self, coordinator, robot_id: str) -> None:
        super().__init__(coordinator, robot_id, "locate")

    async def async_press(self) -> None:
        await self.coordinator.async_send(self.robot_id, "utilities.find_me")


class KoboldVk7TrackerReset(KoboldVk7Entity, ButtonEntity):
    """Mark a maintenance task as done (counts again from zero in Home Assistant)."""

    _attr_translation_key = "vk7_tracker_reset"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator, product_id: str, tracker: Vk7Tracker) -> None:
        super().__init__(coordinator, product_id, f"tracker_reset_{tracker.tracker_id}")
        self.tracker_id = tracker.tracker_id
        self._attr_translation_placeholders = {"name": tracker.name}

    @property
    def available(self) -> bool:
        return self.product_id in self.coordinator.vk7 and any(
            t.tracker_id == self.tracker_id for t in self.device.trackers
        )

    async def async_press(self) -> None:
        device = self.device
        tracker = next(t for t in device.trackers if t.tracker_id == self.tracker_id)
        self.coordinator.vk7_maintenance.reset(device, tracker)
        self.coordinator.async_update_listeners()
