"""Live connection to the Kobold cloud (Phoenix channels over a websocket)."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
import contextlib
import logging
import uuid

import aiohttp

from .api import KoboldApi, KoboldAuthError, KoboldError
from .const import COMPANION_WS_URL, HEARTBEAT_INTERVAL, RECONNECT_MAX_DELAY
from .protocol import SocketEvent, decode_socket_message, phx_message, robot_topic

_LOGGER = logging.getLogger(__name__)


class KoboldSocket:
    """Keeps one websocket open and joined to every robot channel."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        api: KoboldApi,
        robot_ids: list[str],
        on_event: Callable[[SocketEvent], None],
        on_connection: Callable[[bool], None],
        on_auth_failed: Callable[[], None],
    ) -> None:
        self._session = session
        self._api = api
        self._robot_ids = list(robot_ids)
        self._on_event = on_event
        self._on_connection = on_connection
        self._on_auth_failed = on_auth_failed
        self._ws: aiohttp.ClientWebSocketResponse | None = None
        self._task: asyncio.Task | None = None
        self._ref = 0
        self._join_refs: dict[str, str] = {}
        self._stopping = False
        self.connected = False

    def start(self, create_task: Callable) -> None:
        self._stopping = False
        if self._task is None or self._task.done():
            self._task = create_task(self._run())

    async def stop(self) -> None:
        self._stopping = True
        if self._ws is not None and not self._ws.closed:
            with contextlib.suppress(Exception):
                await self._ws.close()
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._task
        self._task = None
        self._set_connected(False)

    async def request_state(self, robot_id: str | None = None) -> bool:
        """Ask for the latest state of one or all robots. False if not connected."""
        if not self.connected or self._ws is None or self._ws.closed:
            return False
        for rid in [robot_id] if robot_id else self._robot_ids:
            join_ref = self._join_refs.get(rid)
            if join_ref is None:
                continue
            await self._ws.send_str(
                phx_message(
                    join_ref,
                    self._next_ref(),
                    robot_topic(rid),
                    "last_state",
                    {"request_id": str(uuid.uuid4())},
                )
            )
        return True

    def _next_ref(self) -> str:
        self._ref += 1
        return str(self._ref)

    def _set_connected(self, value: bool) -> None:
        if value != self.connected:
            self.connected = value
            self._on_connection(value)

    async def _run(self) -> None:
        delay = 2
        while not self._stopping:
            try:
                await self._connect_and_listen()
                delay = 2  # clean close after a working session
            except asyncio.CancelledError:
                raise
            except KoboldAuthError as err:
                _LOGGER.warning("Kobold socket login rejected: %s", err)
                self._set_connected(False)
                self._on_auth_failed()
                return
            except (KoboldError, aiohttp.ClientError, asyncio.TimeoutError) as err:
                _LOGGER.debug("Kobold socket error: %s", err)
            except Exception:  # noqa: BLE001 - never let the loop die
                _LOGGER.exception("Unexpected error in the Kobold socket")
            self._set_connected(False)
            if self._stopping:
                break
            _LOGGER.debug("Reconnecting the Kobold socket in %s s", delay)
            await asyncio.sleep(delay)
            delay = min(delay * 2, RECONNECT_MAX_DELAY)

    async def _connect_and_listen(self) -> None:
        authorization = await self._api.socket_authorization()
        self._ws = await self._session.ws_connect(
            COMPANION_WS_URL,
            headers=self._api.socket_headers(authorization),
            heartbeat=None,
            timeout=aiohttp.ClientWSTimeout(ws_close=10),
        )
        self._ref = 0
        self._join_refs = {}
        for rid in self._robot_ids:
            join_ref = self._next_ref()
            self._join_refs[rid] = join_ref
            await self._ws.send_str(
                phx_message(join_ref, join_ref, robot_topic(rid), "phx_join", {})
            )
        self._set_connected(True)
        await self.request_state()

        heartbeat = asyncio.create_task(self._heartbeat())
        try:
            async for msg in self._ws:
                if msg.type == aiohttp.WSMsgType.TEXT:
                    for event in decode_socket_message(msg.data):
                        if event.kind == "reply_error":
                            _LOGGER.debug("Kobold socket error reply: %s", event.payload)
                            continue
                        try:
                            self._on_event(event)
                        except Exception:  # noqa: BLE001
                            _LOGGER.exception("Error handling Kobold event %s", event.kind)
                elif msg.type in (
                    aiohttp.WSMsgType.CLOSE,
                    aiohttp.WSMsgType.CLOSED,
                    aiohttp.WSMsgType.CLOSING,
                    aiohttp.WSMsgType.ERROR,
                ):
                    break
        finally:
            heartbeat.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await heartbeat
            if not self._ws.closed:
                await self._ws.close()

    async def _heartbeat(self) -> None:
        while self._ws is not None and not self._ws.closed:
            await asyncio.sleep(HEARTBEAT_INTERVAL)
            try:
                await self._ws.send_str(
                    phx_message(None, self._next_ref(), "phoenix", "heartbeat", {})
                )
            except (aiohttp.ClientError, ConnectionError):
                return
