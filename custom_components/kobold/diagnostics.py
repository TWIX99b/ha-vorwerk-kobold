"""Diagnostics for the Vorwerk Kobold integration."""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.const import CONF_EMAIL
from homeassistant.core import HomeAssistant

from .const import CONF_ID_TOKEN, CONF_REFRESH_TOKEN
from .coordinator import KoboldConfigEntry

TO_REDACT = {CONF_EMAIL, CONF_ID_TOKEN, CONF_REFRESH_TOKEN, "serial", "mac_address", "user_id"}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: KoboldConfigEntry
) -> dict[str, Any]:
    coordinator = entry.runtime_data
    robots = {}
    for index, robot in enumerate(coordinator.robots.values()):
        robots[f"robot_{index}"] = {
            "name": robot.name,
            "model": robot.model,
            "firmware": robot.firmware,
            "last_seen": robot.last_seen.isoformat() if robot.last_seen else None,
            "state_raw": robot.state.raw if robot.state else None,
            "cleaning": repr(robot.cleaning) if robot.cleaning else None,
            "maps": [
                {"name": plan.name, "zones": [zone.name for zone in plan.zones]}
                for plan in robot.floorplans
            ],
            "map_probe": robot.map_probe,
        }
    return {
        "entry": async_redact_data(dict(entry.data), TO_REDACT),
        "socket_connected": bool(coordinator.socket and coordinator.socket.connected),
        "robots": async_redact_data(robots, TO_REDACT),
        "api_probe": async_redact_data(coordinator.api_probe, TO_REDACT),
    }
