"""Coordinator for the Vorwerk Kobold integration."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import (
    KoboldApi,
    KoboldApiError,
    KoboldAuth,
    KoboldAuthError,
    KoboldConnectionError,
    KoboldError,
)
from .const import (
    CONF_ID_TOKEN,
    CONF_MARKET,
    CONF_REFRESH_TOKEN,
    DEFAULT_MARKET,
    DOMAIN,
    REFRESH_INTERVAL,
    STALE_AFTER,
)
from .protocol import (
    CleaningProgress,
    FloorPlan,
    RobotState,
    SocketEvent,
    build_cleaning_request,
    parse_cleaning_state,
    parse_floorplan,
    parse_robot_state,
    parse_zones,
    token_valid,
)
from .websocket import KoboldSocket

_LOGGER = logging.getLogger(__name__)

MAP_REFRESH_EVERY = 6  # refresh cycles (6 x 10 min = 1 h)

type KoboldConfigEntry = ConfigEntry[KoboldCoordinator]


@dataclass
class RobotData:
    """Everything known about one robot."""

    robot_id: str
    serial: str
    name: str
    model: str | None = None
    firmware: str | None = None
    state: RobotState | None = None
    cleaning: CleaningProgress | None = None
    last_seen: datetime | None = None
    floorplans: list[FloorPlan] = field(default_factory=list)

    @classmethod
    def from_api(cls, data: dict[str, Any]) -> RobotData:
        return cls(
            robot_id=str(data["id"]),
            serial=str(data.get("serial") or data["id"]),
            name=str(data.get("name") or "Kobold"),
            model=data.get("model_name") or None,
            firmware=data.get("firmware") or None,
        )

    def update_info(self, other: RobotData) -> None:
        self.serial, self.name, self.model, self.firmware = (
            other.serial,
            other.name,
            other.model,
            other.firmware,
        )


class KoboldCoordinator(DataUpdateCoordinator[dict[str, RobotData]]):
    """Owns the cloud session, the socket and the robot data of one account."""

    config_entry: KoboldConfigEntry

    def __init__(self, hass: HomeAssistant, entry: KoboldConfigEntry) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=DOMAIN,
            update_interval=REFRESH_INTERVAL,
        )
        self._session = async_get_clientsession(hass)
        self.market: str = entry.data.get(CONF_MARKET, DEFAULT_MARKET)
        self._auth = KoboldAuth(self._session, self.market)
        self.api = KoboldApi(self._session, self._get_token, self.market)
        self.socket: KoboldSocket | None = None
        self.robots: dict[str, RobotData] = {}
        self.fan_speed: dict[str, str] = {}
        self._cycle = 0
        self._reauth_started = False

    # --- tokens -----------------------------------------------------------

    async def _get_token(self) -> str:
        data = self.config_entry.data
        token = data.get(CONF_ID_TOKEN)
        if token_valid(token):
            return token
        refresh = data.get(CONF_REFRESH_TOKEN)
        if not refresh:
            # No refresh token: the cloud may still accept the token; if not,
            # the request fails with 401 and a re-auth flow is started.
            return token or ""
        _LOGGER.debug("Refreshing the Kobold login")
        result = await self._auth.refresh(refresh)
        new_data = {**data, CONF_ID_TOKEN: result["id_token"]}
        if result.get("refresh_token"):
            new_data[CONF_REFRESH_TOKEN] = result["refresh_token"]
        self.hass.config_entries.async_update_entry(self.config_entry, data=new_data)
        return result["id_token"]

    @callback
    def _start_reauth(self) -> None:
        if not self._reauth_started:
            self._reauth_started = True
            self.config_entry.async_start_reauth(self.hass)

    # --- refresh ----------------------------------------------------------

    async def _async_update_data(self) -> dict[str, RobotData]:
        try:
            if not self.robots or self._cycle % MAP_REFRESH_EVERY == 0:
                await self._load_robots()
                await self._load_maps()
            self._cycle += 1
        except KoboldAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except KoboldError as err:
            if not self.robots:
                raise UpdateFailed(f"Kobold cloud not reachable: {err}") from err
            _LOGGER.debug("Kobold refresh failed, keeping old data: %s", err)

        if self.socket is None:
            self._start_socket()
        elif self.socket.connected:
            await self.socket.request_state()
        return self.robots

    async def _load_robots(self) -> None:
        found = {}
        for item in await self.api.robots():
            robot = RobotData.from_api(item)
            if robot.robot_id in self.robots:
                self.robots[robot.robot_id].update_info(robot)
                robot = self.robots[robot.robot_id]
            found[robot.robot_id] = robot
        self.robots = found

    async def _load_maps(self) -> None:
        for robot in self.robots.values():
            try:
                plans = []
                for raw in await self.api.floorplans(robot.robot_id):
                    plan = parse_floorplan(raw)
                    if plan is None or not plan.name:
                        continue  # unnamed maps are temporary mapping runs
                    zones = parse_zones(await self.api.tracks(plan.floorplan_uuid))
                    plans.append(FloorPlan(plan.floorplan_uuid, plan.name, zones))
                robot.floorplans = plans
            except KoboldApiError as err:
                _LOGGER.debug("Could not load maps of %s: %s", robot.name, err)

    async def async_refresh_maps(self) -> None:
        try:
            await self._load_maps()
        except KoboldAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except KoboldConnectionError as err:
            raise HomeAssistantError(f"Kobold cloud not reachable: {err}") from err

    # --- socket -----------------------------------------------------------

    def _start_socket(self) -> None:
        self.socket = KoboldSocket(
            self._session,
            self.api,
            list(self.robots),
            self._handle_event,
            self._handle_connection,
            self._start_reauth,
        )
        self.socket.start(
            lambda coro: self.config_entry.async_create_background_task(
                self.hass, coro, f"{DOMAIN}_socket"
            )
        )

    async def async_shutdown(self) -> None:
        if self.socket is not None:
            await self.socket.stop()
        await super().async_shutdown()

    @callback
    def _handle_connection(self, connected: bool) -> None:
        _LOGGER.debug("Kobold socket %s", "connected" if connected else "disconnected")
        self.async_update_listeners()

    @callback
    def _handle_event(self, event: SocketEvent) -> None:
        robot = self.robots.get(event.robot_id or "")
        if robot is None and len(self.robots) == 1:
            robot = next(iter(self.robots.values()))
        if robot is None:
            return
        if event.kind == "state":
            robot.state = parse_robot_state(event.payload)
        elif event.kind == "cleaning":
            robot.cleaning = parse_cleaning_state(event.payload)
        else:
            return
        robot.last_seen = dt_util.utcnow()
        self.async_set_updated_data(self.robots)

    # --- availability -----------------------------------------------------

    def robot_available(self, robot_id: str) -> bool:
        robot = self.robots.get(robot_id)
        if robot is None or robot.state is None or robot.last_seen is None:
            return False
        if self.socket is not None and self.socket.connected:
            return True
        return dt_util.utcnow() - robot.last_seen < STALE_AFTER

    # --- commands ---------------------------------------------------------

    async def _call(self, coro) -> Any:
        try:
            return await coro
        except KoboldAuthError as err:
            self._start_reauth()
            raise HomeAssistantError("Kobold login expired, please re-authenticate") from err
        except KoboldError as err:
            raise HomeAssistantError(f"Kobold command failed: {err}") from err

    async def async_send(self, robot_id: str, ability: str) -> None:
        robot = self.robots[robot_id]
        await self._call(self.api.send_ability(robot.serial, ability))
        await self._poke(robot_id)

    async def async_start(
        self,
        robot_id: str,
        floorplan_uuid: str | None = None,
        track_uuids: list[str] | None = None,
    ) -> None:
        body = build_cleaning_request(
            self.fan_speed.get(robot_id, "auto"), floorplan_uuid, track_uuids
        )
        await self._call(self.api.start_cleaning(robot_id, body))
        await self._poke(robot_id)

    async def _poke(self, robot_id: str) -> None:
        if self.socket is not None:
            try:
                await self.socket.request_state(robot_id)
            except Exception:  # noqa: BLE001
                _LOGGER.debug("Could not request state after command")
