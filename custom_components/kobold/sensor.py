"""Sensor platform for the Vorwerk Kobold integration."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import PERCENTAGE, EntityCategory, UnitOfArea, UnitOfTime
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import dt as dt_util

from .coordinator import KoboldConfigEntry, RobotData
from .entity import KoboldEntity, KoboldVk7Entity
from .protocol import (
    BAG_STATES,
    CLEANING_ACTIONS,
    ERROR_OPTIONS,
    RETURNING_ACTIONS,
    VK7_BAG_STATES,
    VK7_BATTERY_STATES,
    Vk7Data,
    Vk7Tracker,
    next_schedule_run,
)

PARALLEL_UPDATES = 0

STATUS_OPTIONS = [
    "cleaning",
    "mapping",
    "spot_cleaning",
    "returning",
    "charging",
    "docked",
    "idle",
    "paused",
    "error",
]


def robot_status(robot: RobotData) -> str | None:
    state = robot.state
    if state is None:
        return None
    if state.state == "error" or state.active_errors:
        return "error"
    if state.state == "paused":
        return "paused"
    if state.action in ("mapping", "fast_mapping", "map_creation"):
        return "mapping"
    if state.action == "spot_cleaning":
        return "spot_cleaning"
    if state.action in CLEANING_ACTIONS:
        return "cleaning"
    if state.action in RETURNING_ACTIONS:
        return "returning"
    if state.is_charging:
        return "charging"
    if state.is_docked:
        return "docked"
    return "idle"


def _error(robot: RobotData) -> str | None:
    return robot.state.error_option if robot.state else None


def _error_attrs(robot: RobotData) -> dict[str, Any]:
    state = robot.state
    if state is None or not (state.errors or state.base_error):
        return {}
    attrs: dict[str, Any] = {
        "errors": [{"code": err.code, "severity": err.severity} for err in state.errors]
    }
    if state.errors:
        attrs["code"] = state.errors[0].code
    if state.base_error:
        attrs["base_error"] = state.base_error
    return attrs


def _cleaning_attrs(robot: RobotData) -> dict[str, Any]:
    run = robot.cleaning
    if run is None:
        return {}
    return {
        "cleaning_type": run.cleaning_type,
        "started_by": run.started_by,
        "start": run.start,
        "end": run.end,
        "zones": [r.track_name for r in run.runs if r.track_name],
    }


@dataclass(frozen=True, kw_only=True)
class KoboldSensorDescription(SensorEntityDescription):
    value: Callable[[RobotData], Any]
    attrs: Callable[[RobotData], dict[str, Any]] = lambda _robot: {}
    needs_cleaning_center: bool = False
    needs_schedules: bool = False


SENSORS: tuple[KoboldSensorDescription, ...] = (
    KoboldSensorDescription(
        key="battery",
        device_class=SensorDeviceClass.BATTERY,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=PERCENTAGE,
        value=lambda r: r.state.charge if r.state else None,
    ),
    KoboldSensorDescription(
        key="status",
        translation_key="status",
        device_class=SensorDeviceClass.ENUM,
        options=STATUS_OPTIONS,
        value=robot_status,
    ),
    KoboldSensorDescription(
        key="error",
        translation_key="error",
        device_class=SensorDeviceClass.ENUM,
        options=list(ERROR_OPTIONS),
        value=_error,
        attrs=_error_attrs,
    ),
    KoboldSensorDescription(
        key="bag",
        translation_key="bag",
        device_class=SensorDeviceClass.ENUM,
        options=list(BAG_STATES),
        value=lambda r: r.state.bag if r.state else None,
        needs_cleaning_center=True,
    ),
    KoboldSensorDescription(
        key="cleaned_area",
        translation_key="cleaned_area",
        native_unit_of_measurement=UnitOfArea.SQUARE_METERS,
        device_class=SensorDeviceClass.AREA,
        suggested_display_precision=1,
        value=lambda r: r.cleaning.area if r.cleaning else None,
        attrs=_cleaning_attrs,
    ),
    KoboldSensorDescription(
        key="base_state",
        translation_key="base_state",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value=lambda r: r.state.base_state if r.state else None,
        needs_cleaning_center=True,
    ),
    KoboldSensorDescription(
        key="next_cleaning",
        translation_key="next_cleaning",
        device_class=SensorDeviceClass.TIMESTAMP,
        value=lambda r: next_schedule_run(r.schedules, dt_util.now()),
        needs_schedules=True,
    ),
)


def _parse_time(value: str | None) -> Any:
    return dt_util.parse_datetime(value) if value else None


@dataclass(frozen=True, kw_only=True)
class KoboldVk7SensorDescription(SensorEntityDescription):
    value: Callable[[Vk7Data], Any]
    attrs: Callable[[Vk7Data], dict[str, Any]] = lambda _device: {}


VK7_SENSORS: tuple[KoboldVk7SensorDescription, ...] = (
    KoboldVk7SensorDescription(
        key="battery_health",
        translation_key="vk7_battery",
        device_class=SensorDeviceClass.ENUM,
        options=list(VK7_BATTERY_STATES),
        value=lambda d: d.battery,
    ),
    KoboldVk7SensorDescription(
        key="filter_bag",
        translation_key="vk7_bag",
        device_class=SensorDeviceClass.ENUM,
        options=list(VK7_BAG_STATES),
        value=lambda d: d.bag,
    ),
    KoboldVk7SensorDescription(
        key="total_time",
        translation_key="vk7_total_time",
        device_class=SensorDeviceClass.DURATION,
        state_class=SensorStateClass.TOTAL_INCREASING,
        native_unit_of_measurement=UnitOfTime.MINUTES,
        suggested_unit_of_measurement=UnitOfTime.HOURS,
        suggested_display_precision=1,
        value=lambda d: d.total_minutes,
        attrs=lambda d: {f"minutes_{mode}": v for mode, v in d.minutes_by_mode.items()},
    ),
    KoboldVk7SensorDescription(
        key="total_area",
        translation_key="vk7_total_area",
        device_class=SensorDeviceClass.AREA,
        state_class=SensorStateClass.TOTAL_INCREASING,
        native_unit_of_measurement=UnitOfArea.SQUARE_METERS,
        value=lambda d: d.total_area,
    ),
    KoboldVk7SensorDescription(
        key="last_sync",
        translation_key="vk7_last_sync",
        device_class=SensorDeviceClass.TIMESTAMP,
        entity_category=EntityCategory.DIAGNOSTIC,
        value=lambda d: _parse_time(d.last_sync),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: KoboldConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data
    added: set[tuple[str, str]] = set()

    @callback
    def _add_new() -> None:
        new = []
        for rid, robot in coordinator.robots.items():
            for description in SENSORS:
                if (rid, description.key) in added:
                    continue
                if description.needs_cleaning_center and not (
                    robot.state and robot.state.has_cleaning_center
                ):
                    continue  # only bases with a dust bag (RB7) get these
                if description.needs_schedules and not robot.schedules_loaded:
                    continue
                added.add((rid, description.key))
                new.append(KoboldSensor(coordinator, rid, description))
        for pid, device in coordinator.vk7.items():
            for vk7_description in VK7_SENSORS:
                if (f"vk7_{pid}", vk7_description.key) not in added:
                    added.add((f"vk7_{pid}", vk7_description.key))
                    new.append(KoboldVk7Sensor(coordinator, pid, vk7_description))
            for tracker in device.trackers:
                if (f"vk7_{pid}", tracker.tracker_id) not in added:
                    added.add((f"vk7_{pid}", tracker.tracker_id))
                    new.append(KoboldVk7TrackerSensor(coordinator, pid, tracker))
        if new:
            async_add_entities(new)

    _add_new()
    entry.async_on_unload(coordinator.async_add_listener(_add_new))


class KoboldSensor(KoboldEntity, SensorEntity):
    entity_description: KoboldSensorDescription

    def __init__(self, coordinator, robot_id: str, description: KoboldSensorDescription) -> None:
        super().__init__(coordinator, robot_id, description.key)
        self.entity_description = description

    @property
    def native_value(self) -> Any:
        return self.entity_description.value(self.robot)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        return self.entity_description.attrs(self.robot) or None


class KoboldVk7Sensor(KoboldVk7Entity, SensorEntity):
    entity_description: KoboldVk7SensorDescription

    def __init__(self, coordinator, product_id: str, description: KoboldVk7SensorDescription) -> None:
        super().__init__(coordinator, product_id, description.key)
        self.entity_description = description

    @property
    def native_value(self) -> Any:
        return self.entity_description.value(self.device)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        return self.entity_description.attrs(self.device) or None


class KoboldVk7TrackerSensor(KoboldVk7Entity, SensorEntity):
    """A maintenance tracker of the app ("Bürste reinigen", ...), in % of its interval."""

    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:wrench-clock"

    def __init__(self, coordinator, product_id: str, tracker: Vk7Tracker) -> None:
        super().__init__(coordinator, product_id, f"tracker_{tracker.tracker_id}")
        self.tracker_id = tracker.tracker_id
        self._attr_name = tracker.name

    @property
    def tracker(self) -> Vk7Tracker | None:
        return next((t for t in self.device.trackers if t.tracker_id == self.tracker_id), None)

    @property
    def available(self) -> bool:
        return super().available and self.tracker is not None

    @property
    def native_value(self) -> int | None:
        tracker = self.tracker
        return tracker.progress if tracker else None

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        tracker = self.tracker
        if tracker is None:
            return None
        return {
            "type": tracker.kind,
            "hours_used": tracker.counted_hours,
            "interval_hours": tracker.life_hours,
            "maintenance_window_hours": list(tracker.maintenance_hours),
        }
