"""Switch platform for the Vorwerk Kobold integration (settings and schedules)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from homeassistant.components.switch import SwitchEntity, SwitchEntityDescription
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import KoboldConfigEntry, KoboldCoordinator
from .entity import KoboldEntity, KoboldSettingEntity
from .protocol import ScheduleEvent, days_label

PARALLEL_UPDATES = 1


@dataclass(frozen=True, kw_only=True)
class KoboldSettingSwitchDescription(SwitchEntityDescription):
    setting: str
    field: str
    on_value: Any = True
    off_value: Any = False


SETTING_SWITCHES: tuple[KoboldSettingSwitchDescription, ...] = (
    KoboldSettingSwitchDescription(
        key="sounds", translation_key="sounds", setting="sounds", field="enabled"
    ),
    KoboldSettingSwitchDescription(
        key="voice",
        translation_key="voice",
        setting="sounds",
        field="voice",
        on_value="on",
        off_value="off",
    ),
    KoboldSettingSwitchDescription(
        key="object_avoidance",
        translation_key="object_avoidance",
        setting="object_avoidance",
        field="enabled",
    ),
    KoboldSettingSwitchDescription(
        key="overhang_detection",
        translation_key="overhang_detection",
        setting="overhang_detection",
        field="enabled",
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: KoboldConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data
    added: set[tuple[str, str]] = set()
    schedule_entities: dict[tuple[str, str], KoboldScheduleSwitch] = {}

    @callback
    def _remove_deleted_schedules() -> None:
        """Drop switches of schedules that were deleted in the app."""
        registry = er.async_get(hass)
        for key, entity in list(schedule_entities.items()):
            robot_id, uuid = key
            robot = coordinator.robots.get(robot_id)
            if robot is None or not robot.schedules_loaded:
                continue
            if any(event.uuid == uuid for event in robot.schedules):
                continue
            schedule_entities.pop(key)
            added.discard(key)
            if entity.entity_id and registry.async_get(entity.entity_id):
                registry.async_remove(entity.entity_id)

    @callback
    def _add_new() -> None:
        _remove_deleted_schedules()
        new: list[SwitchEntity] = []
        for robot_id, robot in coordinator.robots.items():
            for description in SETTING_SWITCHES:
                if (robot_id, description.key) in added:
                    continue
                if description.field in robot.settings.get(description.setting, {}):
                    added.add((robot_id, description.key))
                    new.append(KoboldSettingSwitch(coordinator, robot_id, description))
            for event in robot.schedules:
                if (robot_id, event.uuid) not in added:
                    added.add((robot_id, event.uuid))
                    entity = KoboldScheduleSwitch(coordinator, robot_id, event)
                    schedule_entities[(robot_id, event.uuid)] = entity
                    new.append(entity)
        if new:
            async_add_entities(new)

    _add_new()
    entry.async_on_unload(coordinator.async_add_listener(_add_new))

    # schedules deleted while Home Assistant was not running
    registry = er.async_get(hass)
    current = {
        f"{robot.serial}_schedule_{event.uuid}"
        for robot in coordinator.robots.values()
        for event in robot.schedules
    }
    loaded_serials = {r.serial for r in coordinator.robots.values() if r.schedules_loaded}
    for reg_entry in er.async_entries_for_config_entry(registry, entry.entry_id):
        unique_id = reg_entry.unique_id
        if reg_entry.domain != "switch" or "_schedule_" not in unique_id:
            continue
        serial = unique_id.split("_schedule_", 1)[0]
        if serial in loaded_serials and unique_id not in current:
            registry.async_remove(reg_entry.entity_id)


class KoboldSettingSwitch(KoboldSettingEntity, SwitchEntity):
    entity_description: KoboldSettingSwitchDescription
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(
        self,
        coordinator: KoboldCoordinator,
        robot_id: str,
        description: KoboldSettingSwitchDescription,
    ) -> None:
        self._setting = description.setting
        super().__init__(coordinator, robot_id, description.key)
        self.entity_description = description

    @property
    def is_on(self) -> bool | None:
        value = self.setting.get(self.entity_description.field)
        if value is None:
            return None
        return value == self.entity_description.on_value

    async def _set(self, on: bool) -> None:
        description = self.entity_description
        await self.coordinator.async_set_setting(
            self.robot_id,
            description.setting,
            **{description.field: description.on_value if on else description.off_value},
        )

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._set(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._set(False)


class KoboldScheduleSwitch(KoboldEntity, SwitchEntity):
    """One schedule ("Zeitplan") of the MyKobold app."""

    _attr_translation_key = "schedule"

    def __init__(self, coordinator: KoboldCoordinator, robot_id: str, event: ScheduleEvent) -> None:
        super().__init__(coordinator, robot_id, f"schedule_{event.uuid}")
        self.schedule_uuid = event.uuid
        self._attr_translation_placeholders = {
            "time": event.time,
            "days": days_label(event.days, coordinator.hass.config.language or "en"),
        }

    @property
    def event(self) -> ScheduleEvent | None:
        return next((e for e in self.robot.schedules if e.uuid == self.schedule_uuid), None)

    @property
    def available(self) -> bool:
        return (
            self.coordinator.last_update_success
            and self.robot_id in self.coordinator.robots
            and self.event is not None
        )

    @property
    def is_on(self) -> bool | None:
        event = self.event
        return event.enabled if event else None

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        event = self.event
        if event is None:
            return None
        plans = {p.floorplan_uuid: p for p in self.robot.floorplans}
        plan = plans.get(event.floorplan_uuid or "")
        zones = {z.track_uuid: z.name for z in plan.zones} if plan else {}
        return {
            "time": event.time,
            "days": list(event.days),
            "mode": event.mode,
            "map": plan.name if plan else event.floorplan_uuid,
            "rooms": [zones.get(z, z) for z in event.zone_uuids],
        }

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self.coordinator.async_set_schedule_enabled(self.robot_id, self.schedule_uuid, True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.coordinator.async_set_schedule_enabled(self.robot_id, self.schedule_uuid, False)
