"""The Vorwerk Kobold integration."""

from __future__ import annotations

import json
from typing import Any

import voluptuous as vol

from homeassistant.const import Platform
from homeassistant.core import (
    HomeAssistant,
    ServiceCall,
    ServiceResponse,
    SupportsResponse,
)
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.typing import ConfigType

from .api import KoboldError
from .const import DOMAIN

from .coordinator import KoboldConfigEntry, KoboldCoordinator

PLATFORMS: list[Platform] = [
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.IMAGE,
    Platform.NUMBER,
    Platform.SELECT,
    Platform.SENSOR,
    Platform.SWITCH,
    Platform.VACUUM,
]


CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

SERVICE_API_REQUEST = "api_request"
API_REQUEST_SCHEMA = vol.Schema(
    {
        vol.Optional("path"): vol.All(cv.string, vol.Match(r"^/[A-Za-z0-9_./?=&{}\-\[\]]*$")),
        vol.Optional("host", default="orbital"): vol.In(["orbital", "companion"]),
        # read-only robot abilities only (x.show / x.get / x.list / x.info)
        vol.Optional("ability"): vol.All(
            cv.string, vol.Match(r"^[a-z_]+\.(show|get|list|info|status)$")
        ),
    }
)


def _shorten(value: Any, depth: int = 0) -> Any:
    """Keep the structure but cut long strings (map images) and long lists."""
    if isinstance(value, dict):
        return {k: _shorten(v, depth + 1) for k, v in value.items()}
    if isinstance(value, list):
        items = [_shorten(v, depth + 1) for v in value[:20]]
        if len(value) > 20:
            items.append(f"... {len(value) - 20} more")
        return items
    if isinstance(value, str) and len(value) > 200:
        return f"<{len(value)} chars>"
    return value


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register the developer service."""

    async def _api_request(call: ServiceCall) -> ServiceResponse:
        entries = hass.config_entries.async_loaded_entries(DOMAIN)
        if not entries:
            raise HomeAssistantError("No Vorwerk Kobold account is set up")
        coordinator: KoboldCoordinator = entries[0].runtime_data
        robot = next(iter(coordinator.robots.values()), None)
        try:
            if "ability" in call.data:
                if robot is None:
                    raise HomeAssistantError("No robot")
                status, body = await coordinator.api.raw_ability(
                    robot.serial, call.data["ability"]
                )
            else:
                path: str = call.data.get("path") or "/users/me/robots"
                if robot is not None:
                    path = path.replace("{robot_id}", robot.robot_id).replace(
                        "{serial}", robot.serial
                    )
                status, body = await coordinator.api.raw_get(call.data["host"], path)
        except KoboldError as err:
            raise HomeAssistantError(str(err)) from err
        body = _shorten(body)
        return {"status": status, "body": json.loads(json.dumps(body, default=str))}

    hass.services.async_register(
        DOMAIN,
        SERVICE_API_REQUEST,
        _api_request,
        schema=API_REQUEST_SCHEMA,
        supports_response=SupportsResponse.ONLY,
    )
    return True


async def async_setup_entry(hass: HomeAssistant, entry: KoboldConfigEntry) -> bool:
    """Set up Vorwerk Kobold from a config entry."""
    coordinator = KoboldCoordinator(hass, entry)
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: KoboldConfigEntry) -> bool:
    """Unload a config entry."""
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        await entry.runtime_data.async_shutdown()
    return unloaded
