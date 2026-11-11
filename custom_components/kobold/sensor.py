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
from homeassistant.const import PERCENTAGE, EntityCategory, UnitOfArea
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import KoboldConfigEntry, RobotData
from .entity import KoboldEntity
from .protocol import BAG_STATES, CLEANING_ACTIONS, RETURNING_ACTIONS

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


def _error_code(robot: RobotData) -> str | None:
    state = robot.state
    if state is None:
        return None
    if state.errors:
        return state.errors[0].code
    return state.base_error or "none"


def _error_attrs(robot: RobotData) -> dict[str, Any]:
    state = robot.state
    if state is None or not state.errors:
        return {}
    return {
        "errors": [
            {"code": err.code, "severity": err.severity} for err in state.errors
        ]
    }


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
        value=_error_code,
        attrs=_error_attrs,
    ),
    KoboldSensorDescription(
        key="bag",
        translation_key="bag",
        device_class=SensorDeviceClass.ENUM,
        options=list(BAG_STATES),
        value=lambda r: r.state.bag if r.state else None,
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
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: KoboldConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data
    async_add_entities(
        KoboldSensor(coordinator, rid, description)
        for rid in coordinator.robots
        for description in SENSORS
    )


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
