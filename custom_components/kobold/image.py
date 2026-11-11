"""Image platform for the Vorwerk Kobold integration (floor plans and last run)."""

from __future__ import annotations

from datetime import datetime

from homeassistant.components.image import ImageEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import dt as dt_util

from .coordinator import KoboldConfigEntry, KoboldCoordinator
from .entity import KoboldEntity
from .protocol import FloorPlan

PARALLEL_UPDATES = 0


def _parse_time(value: str | None) -> datetime | None:
    return dt_util.parse_datetime(value) if value else None


async def async_setup_entry(
    hass: HomeAssistant,
    entry: KoboldConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data
    added: set[str] = set()

    @callback
    def _add_new() -> None:
        new: list[ImageEntity] = []
        for rid, robot in coordinator.robots.items():
            key = f"{rid}_last_cleaning"
            if key not in added and robot.last_cleaning_map is not None:
                added.add(key)
                new.append(KoboldLastCleaningImage(coordinator, rid))
            for plan in robot.floorplans:
                key = f"{rid}_{plan.floorplan_uuid}"
                if key not in added and plan.image:
                    added.add(key)
                    new.append(KoboldMapImage(coordinator, rid, plan))
        if new:
            async_add_entities(new)

    _add_new()
    entry.async_on_unload(coordinator.async_add_listener(_add_new))


class KoboldMapImage(KoboldEntity, ImageEntity):
    """The floor plan of one Kobold map."""

    _attr_content_type = "image/png"
    _attr_translation_key = "map"

    def __init__(self, coordinator: KoboldCoordinator, robot_id: str, plan: FloorPlan) -> None:
        KoboldEntity.__init__(self, coordinator, robot_id, f"map_{plan.floorplan_uuid}")
        ImageEntity.__init__(self, coordinator.hass)
        self._floorplan_uuid = plan.floorplan_uuid
        self._attr_translation_placeholders = {"name": plan.name}
        self._image: bytes | None = None
        self._sync()

    def _plan(self) -> FloorPlan | None:
        for plan in self.robot.floorplans:
            if plan.floorplan_uuid == self._floorplan_uuid:
                return plan
        return None

    def _sync(self) -> None:
        plan = self._plan()
        if plan is None or not plan.image:
            return
        if plan.image != self._image:
            self._image = plan.image
            self._attr_image_last_updated = _parse_time(plan.updated_at) or dt_util.utcnow()
        self._attr_extra_state_attributes = {
            "map": plan.name,
            "zones": [zone.name for zone in plan.zones],
        }

    @property
    def available(self) -> bool:
        return self.robot_id in self.coordinator.robots and self._plan() is not None

    @callback
    def _handle_coordinator_update(self) -> None:
        self._sync()
        super()._handle_coordinator_update()

    async def async_image(self) -> bytes | None:
        return self._image


class KoboldLastCleaningImage(KoboldEntity, ImageEntity):
    """Thumbnail of the most recent cleaning run."""

    _attr_content_type = "image/png"
    _attr_translation_key = "last_cleaning"

    def __init__(self, coordinator: KoboldCoordinator, robot_id: str) -> None:
        KoboldEntity.__init__(self, coordinator, robot_id, "last_cleaning")
        ImageEntity.__init__(self, coordinator.hass)
        self._image: bytes | None = None
        self._sync()

    def _sync(self) -> None:
        last = self.robot.last_cleaning_map
        if last is None or not last.image:
            return
        if last.image != self._image:
            self._image = last.image
            self._attr_image_last_updated = _parse_time(last.start) or dt_util.utcnow()
        self._attr_extra_state_attributes = {
            "start": last.start,
            "started_by": last.started_by,
        }

    @property
    def available(self) -> bool:
        return self.robot_id in self.coordinator.robots and self._image is not None

    @callback
    def _handle_coordinator_update(self) -> None:
        self._sync()
        super()._handle_coordinator_update()

    async def async_image(self) -> bytes | None:
        return self._image
