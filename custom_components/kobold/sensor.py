"""Sensor platform for the Vorwerk Kobold integration."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.sensor import (
    RestoreSensor,
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
    RUN_RESULTS,
    VK7_BAG_STATES,
    VK7_BATTERY_STATES,
    Vk7Data,
    Vk7Tracker,
    next_schedule_run,
    vk7_live_is_newer,
    vk7_minutes_by_mode,
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


def _last_run_attrs(robot: RobotData) -> dict[str, Any]:
    run = robot.last_run
    if run is None:
        return {}

    def minutes(seconds: int | None) -> float | None:
        return round(seconds / 60, 1) if seconds is not None else None

    return {
        "start": run.start,
        "end": run.end,
        "duration_min": minutes(run.duration_s),
        "pause_min": minutes(run.pause_s),
        "error_min": minutes(run.error_s),
        "area_m2": run.area,
        "started_by": run.started_by,
        "mode": run.mode,
        "rooms": list(run.zones),
        "returned_to_base": run.docked,
    }


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
    needs_last_run: bool = False


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
        key="last_run",
        translation_key="last_run",
        device_class=SensorDeviceClass.ENUM,
        options=list(RUN_RESULTS),
        value=lambda r: r.last_run.result if r.last_run else None,
        attrs=_last_run_attrs,
        needs_last_run=True,
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
    needs_bluetooth: bool = False  # only with a Bluetooth reader for this VK7
    restore: bool = False  # keep the last value until the next reading
    mode: str | None = None  # runtime per attachment: only if the VK7 reports it


def _vk7_bag(device: Vk7Data) -> str | None:
    if vk7_live_is_newer(device) and device.live and device.live.bag:
        return device.live.bag
    return device.bag


def _vk7_total_minutes(device: Vk7Data) -> float | None:
    cloud = device.total_minutes
    live = device.live.total_minutes if device.live else None
    if live is None:
        return cloud
    # the app rounds differently; never go backwards (total_increasing)
    return max(live, cloud or 0)


VK7_AREA_PER_MINUTE = 2  # m²; the app's statistics: total area = 2 x total minutes


def _vk7_total_area(device: Vk7Data) -> float | None:
    cloud = device.total_area
    live = device.live.total_minutes if device.live else None
    if live is None:
        return cloud
    return max(round(live * VK7_AREA_PER_MINUTE, 1), cloud or 0)


def _vk7_mode_attrs(device: Vk7Data) -> dict[str, Any]:
    if vk7_live_is_newer(device) and device.live and device.live.runtimes_s:
        minutes = vk7_minutes_by_mode(device.live)
    else:
        minutes = device.minutes_by_mode
    return {f"minutes_{mode}": value for mode, value in minutes.items()}


VK7_MODES = ("eb7", "sp7", "pb7", "passive")


def _vk7_mode_minutes(device: Vk7Data, mode: str) -> float | None:
    cloud = device.minutes_by_mode.get(mode)
    live = vk7_minutes_by_mode(device.live).get(mode) if device.live else None
    if live is None:
        return cloud
    return max(live, cloud or 0)  # never go backwards (total_increasing)


def _vk7_ble_attrs(device: Vk7Data) -> dict[str, Any]:
    live = device.live
    if live is None:
        return {}
    attrs: dict[str, Any] = {
        "battery_absolute": live.battery_absolute,
        "battery_extra_raw": live.battery_extra,
        "motor_minutes": round(live.runtimes_s["motor"] / 60)
        if "motor" in live.runtimes_s
        else None,
        "event_counter": live.event_counter,
    }
    if live.last_event is not None:
        event = live.last_event
        attrs["last_event"] = {
            "runtime_h": round(event.runtime_s / 3600, 2),
            "type": event.event_type,
            "source": event.source,
            "warning": event.warning,
            "number": event.number,
        }
    return attrs


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
        value=_vk7_bag,
    ),
    KoboldVk7SensorDescription(
        key="total_time",
        translation_key="vk7_total_time",
        device_class=SensorDeviceClass.DURATION,
        state_class=SensorStateClass.TOTAL_INCREASING,
        native_unit_of_measurement=UnitOfTime.MINUTES,
        suggested_unit_of_measurement=UnitOfTime.HOURS,
        suggested_display_precision=1,
        value=_vk7_total_minutes,
        attrs=_vk7_mode_attrs,
    ),
    KoboldVk7SensorDescription(
        key="total_area",
        translation_key="vk7_total_area",
        device_class=SensorDeviceClass.AREA,
        state_class=SensorStateClass.TOTAL_INCREASING,
        native_unit_of_measurement=UnitOfArea.SQUARE_METERS,
        value=_vk7_total_area,
    ),
    KoboldVk7SensorDescription(
        key="last_sync",
        translation_key="vk7_last_sync",
        device_class=SensorDeviceClass.TIMESTAMP,
        entity_category=EntityCategory.DIAGNOSTIC,
        value=lambda d: _parse_time(d.last_sync),
    ),
    *(
        KoboldVk7SensorDescription(
            key=f"time_{mode}",
            translation_key=f"vk7_time_{mode}",
            device_class=SensorDeviceClass.DURATION,
            state_class=SensorStateClass.TOTAL_INCREASING,
            native_unit_of_measurement=UnitOfTime.MINUTES,
            suggested_unit_of_measurement=UnitOfTime.HOURS,
            suggested_display_precision=1,
            value=lambda d, mode=mode: _vk7_mode_minutes(d, mode),
            mode=mode,
        )
        for mode in VK7_MODES
    ),
    KoboldVk7SensorDescription(
        key="battery_level",
        device_class=SensorDeviceClass.BATTERY,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=PERCENTAGE,
        value=lambda d: d.live.battery if d.live else None,
        needs_bluetooth=True,
        restore=True,
    ),
    KoboldVk7SensorDescription(
        key="bluetooth_read",
        translation_key="vk7_bluetooth_read",
        device_class=SensorDeviceClass.TIMESTAMP,
        entity_category=EntityCategory.DIAGNOSTIC,
        value=lambda d: d.live.read_at if d.live else None,
        attrs=_vk7_ble_attrs,
        needs_bluetooth=True,
        restore=True,
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
                if description.needs_last_run and robot.last_run is None:
                    continue
                added.add((rid, description.key))
                new.append(KoboldSensor(coordinator, rid, description))
        for pid, device in coordinator.vk7.items():
            for vk7_description in VK7_SENSORS:
                if vk7_description.needs_bluetooth and pid not in coordinator.vk7_readers:
                    continue
                if vk7_description.mode and _vk7_mode_minutes(device, vk7_description.mode) is None:
                    continue
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


class KoboldVk7Sensor(KoboldVk7Entity, RestoreSensor):
    """A VK7 value from the app's cloud data and/or read over Bluetooth."""

    entity_description: KoboldVk7SensorDescription

    def __init__(self, coordinator, product_id: str, description: KoboldVk7SensorDescription) -> None:
        super().__init__(coordinator, product_id, description.key)
        self.entity_description = description
        self._restored: Any = None

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        if self.entity_description.restore and (last := await self.async_get_last_sensor_data()):
            self._restored = last.native_value

    @property
    def available(self) -> bool:
        if self.entity_description.needs_bluetooth:
            # Bluetooth values do not depend on the cloud being reachable
            return self.product_id in self.coordinator.vk7
        return super().available

    @property
    def native_value(self) -> Any:
        value = self.entity_description.value(self.device)
        if value is None and self.entity_description.restore:
            return self._restored
        return value

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        attrs = dict(self.entity_description.attrs(self.device))
        if self.entity_description.key == "bluetooth_read":
            reader = self.coordinator.vk7_readers.get(self.product_id)
            if reader is not None:
                attrs["last_attempt"] = reader.last_attempt_at
                attrs["status"] = "ok" if reader.last_error is None else "not_reachable"
        return attrs or None


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
