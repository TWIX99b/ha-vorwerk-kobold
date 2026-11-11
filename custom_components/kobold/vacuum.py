"""Vacuum platform for the Vorwerk Kobold integration."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from homeassistant.components.vacuum import (
    StateVacuumEntity,
    VacuumActivity,
    VacuumEntityFeature,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from .coordinator import KoboldConfigEntry, KoboldCoordinator
from .entity import KoboldEntity
from .protocol import FAN_SPEEDS, fan_speeds_from_features, segment_id, split_segment_id

try:  # HA 2026.3+
    from homeassistant.components.vacuum import Segment

    CLEAN_AREA = VacuumEntityFeature.CLEAN_AREA
except ImportError:  # pragma: no cover - older Home Assistant
    Segment = None
    CLEAN_AREA = VacuumEntityFeature(0)

_LOGGER = logging.getLogger(__name__)

PARALLEL_UPDATES = 1

ACTIVITY = {
    "cleaning": VacuumActivity.CLEANING,
    "docked": VacuumActivity.DOCKED,
    "idle": VacuumActivity.IDLE,
    "paused": VacuumActivity.PAUSED,
    "returning": VacuumActivity.RETURNING,
    "error": VacuumActivity.ERROR,
}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: KoboldConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data
    async_add_entities(KoboldVacuum(coordinator, rid) for rid in coordinator.robots)


class KoboldVacuum(KoboldEntity, StateVacuumEntity, RestoreEntity):
    """A Kobold robot vacuum."""

    _attr_name = None
    _attr_translation_key = "robot"
    _attr_supported_features = (
        VacuumEntityFeature.START
        | VacuumEntityFeature.PAUSE
        | VacuumEntityFeature.STOP
        | VacuumEntityFeature.RETURN_HOME
        | getattr(VacuumEntityFeature, "STATE", VacuumEntityFeature(0))
        | VacuumEntityFeature.FAN_SPEED
        | VacuumEntityFeature.LOCATE
        | CLEAN_AREA
    )

    def __init__(self, coordinator: KoboldCoordinator, robot_id: str) -> None:
        super().__init__(coordinator, robot_id, "vacuum")

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        if (last := await self.async_get_last_state()) is not None:
            speed = last.attributes.get("fan_speed")
            if speed in FAN_SPEEDS:
                self.coordinator.fan_speed[self.robot_id] = speed
        self._check_segments()

    @property
    def activity(self) -> VacuumActivity | None:
        state = self.robot.state
        return ACTIVITY.get(state.activity) if state else None

    @property
    def fan_speed_list(self) -> list[str]:
        return fan_speeds_from_features(self.robot.features)

    @property
    def fan_speed(self) -> str:
        return self.coordinator.fan_speed.get(self.robot_id, "auto")

    @callback
    def _handle_coordinator_update(self) -> None:
        self._check_segments()
        super()._handle_coordinator_update()

    # --- commands ---------------------------------------------------------

    async def async_start(self) -> None:
        state = self.robot.state
        if state and "resume" in state.commands and "start" not in state.commands:
            await self.coordinator.async_send(self.robot_id, "cleaning.resume")
            return
        await self.coordinator.async_start(self.robot_id)

    async def async_pause(self) -> None:
        await self.coordinator.async_send(self.robot_id, "cleaning.pause")

    async def async_stop(self, **kwargs: Any) -> None:
        """End the run and send the robot home.

        A plain ``cleaning.cancel`` leaves the robot standing in the room and
        the cloud then refuses ``navigation.return_to_base`` (HTTP 503), so
        stopping always ends with the robot driving back to its base.
        """
        state = self.robot.state
        if state and state.is_docked:
            if "cancel" in state.commands:
                await self.coordinator.async_send(self.robot_id, "cleaning.cancel")
            return
        await self.async_return_to_base()

    async def async_return_to_base(self, **kwargs: Any) -> None:
        state = self.robot.state
        commands = state.commands if state else frozenset()
        if state and not state.is_docked and "return_to_base" not in commands:
            if "pause" not in commands:
                # Idle in the room without a run (e.g. after a cancel): the cloud
                # refuses return-to-base, and a new run started off the base
                # does not find its way back reliably.
                raise HomeAssistantError(
                    "The robot is not on a cleaning run and cannot drive back on "
                    "its own; please put it on the base"
                )
            await self.coordinator.async_send(self.robot_id, "cleaning.pause")
            if not await self._wait_for_command("return_to_base", 30):
                raise HomeAssistantError("The robot does not accept 'return to base' right now")
        await self.coordinator.async_send(self.robot_id, "navigation.return_to_base")

    async def _wait_for_command(self, command: str, timeout: int) -> bool:
        for _ in range(timeout):
            state = self.robot.state
            if state and command in state.commands:
                return True
            await asyncio.sleep(1)
        return False

    async def async_locate(self, **kwargs: Any) -> None:
        await self.coordinator.async_send(self.robot_id, "utilities.find_me")

    async def async_set_fan_speed(self, fan_speed: str, **kwargs: Any) -> None:
        if fan_speed not in self.fan_speed_list:
            raise HomeAssistantError(f"Unknown fan speed {fan_speed}")
        self.coordinator.fan_speed[self.robot_id] = fan_speed
        self.async_write_ha_state()

    # --- area cleaning ----------------------------------------------------

    def _segments(self) -> list[Any]:
        if Segment is None:
            return []
        segments = []
        for plan in self.robot.floorplans:
            for zone in plan.zones:
                segments.append(
                    Segment(
                        id=segment_id(plan.floorplan_uuid, zone.track_uuid),
                        name=zone.name or zone.track_uuid[:8],
                        group=plan.name,
                    )
                )
        return segments

    async def async_get_segments(self) -> list[Any]:
        await self.coordinator.async_refresh_maps()
        return self._segments()

    async def async_clean_segments(self, segment_ids: list[str], **kwargs: Any) -> None:
        by_plan: dict[str, list[str]] = {}
        for value in segment_ids:
            parts = split_segment_id(value)
            if parts is None:
                continue
            by_plan.setdefault(parts[0], []).append(parts[1])
        if not by_plan:
            raise HomeAssistantError("No valid Kobold zones selected")
        if len(by_plan) > 1:
            raise HomeAssistantError(
                "The selected areas are on different Kobold maps; clean one map at a time"
            )
        floorplan, tracks = next(iter(by_plan.items()))
        await self.coordinator.async_start(self.robot_id, floorplan, tracks)

    @callback
    def _check_segments(self) -> None:
        if Segment is None or not self.robot.floorplans:
            return
        seen = getattr(self, "last_seen_segments", None)
        if not seen:
            return
        current = {seg.id for seg in self._segments()}
        if {seg.id for seg in seen} != current:
            self.async_create_segments_issue()
