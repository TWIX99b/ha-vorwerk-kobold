"""Bluetooth access to the VK7 stick vacuum (through Home Assistant Bluetooth / proxies).

The VK7 talks to the MyKobold app over Bluetooth LE only. This module keeps that
optional: it is only used when Home Assistant's Bluetooth integration is loaded.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from collections.abc import Callable
from datetime import timedelta
import time

from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.util import dt as dt_util

from .protocol import (
    VK7_CHAR_UUID,
    VK7_IDX_EVENT_LOG,
    VK7_READ_PLAN,
    Vk7Live,
    apply_vk7_value,
    describe_bytes,
    normalise_mac,
    parse_vk7_response,
    vk7_read_request,
)

_LOGGER = logging.getLogger(__name__)

PROBE_TIMEOUT = 120


def bluetooth_loaded(hass: HomeAssistant) -> bool:
    return "bluetooth" in hass.config.components


async def async_probe(
    hass: HomeAssistant,
    address: str,
    *,
    pair: bool = False,
    listen_seconds: int = 0,
) -> dict[str, Any]:
    """Connect, list all GATT services/characteristics and read what is readable.

    Read-only: nothing is written to the device except, when ``listen_seconds``
    is set, the standard notification descriptors needed to receive notifications.
    """
    if not bluetooth_loaded(hass):
        raise HomeAssistantError("Home Assistant Bluetooth is not set up")
    try:
        async with asyncio.timeout(PROBE_TIMEOUT):
            return await _probe(hass, address, pair, listen_seconds)
    except TimeoutError as err:
        raise HomeAssistantError("Bluetooth probe timed out") from err


async def _connect(establish_connection: Any, client_class: Any, ble_device: Any, pair: bool) -> Any:
    """Connect; with ``pair`` the bond is created right while connecting.

    Devices that only accept bonded centrals drop an unencrypted connection
    before a later ``pair()`` call could run, so pairing has to happen first.
    """
    if pair:
        try:
            return await establish_connection(
                client_class, ble_device, "VK7", max_attempts=2, pair=True
            )
        except TypeError:
            client = await establish_connection(client_class, ble_device, "VK7", max_attempts=2)
            await client.pair()
            return client
    return await establish_connection(client_class, ble_device, "VK7", max_attempts=3)


async def _probe(
    hass: HomeAssistant, address: str, pair: bool, listen_seconds: int
) -> dict[str, Any]:
    from bleak_retry_connector import (  # noqa: PLC0415 - shipped with HA Bluetooth
        BleakClientWithServiceCache,
        establish_connection,
    )

    from homeassistant.components import bluetooth  # noqa: PLC0415

    result: dict[str, Any] = {"address": address}
    info = bluetooth.async_last_service_info(hass, address, connectable=False)
    if info is not None:
        result["advertisement"] = {
            "name": info.name,
            "rssi": info.rssi,
            "source": info.source,
            "connectable": info.connectable,
            "manufacturer_data": {
                str(k): v.hex() for k, v in info.manufacturer_data.items()
            },
            "service_uuids": list(info.service_uuids),
            "service_data": {k: v.hex() for k, v in info.service_data.items()},
        }
    ble_device = bluetooth.async_ble_device_from_address(hass, address, connectable=True)
    if ble_device is None:
        result["error"] = (
            "not reachable through a connectable Bluetooth adapter or proxy"
            if info is not None
            else "not seen by any Bluetooth adapter or proxy"
        )
        return result

    try:
        client = await _connect(establish_connection, BleakClientWithServiceCache, ble_device, pair)
    except Exception as err:  # noqa: BLE001 - report everything to the caller
        _LOGGER.debug("VK7 connection failed: %s", err)
        result["connect_error"] = str(err)
        return result
    try:
        details = getattr(ble_device, "details", None)
        if isinstance(details, dict):
            result["connected_via"] = details.get("source")
        if pair:
            result["paired"] = True
        services = []
        notifiable = []
        for service in client.services:
            chars = []
            for char in service.characteristics:
                entry: dict[str, Any] = {
                    "uuid": char.uuid,
                    "handle": char.handle,
                    "properties": list(char.properties),
                    "descriptors": [d.uuid for d in char.descriptors],
                }
                if "read" in char.properties:
                    try:
                        async with asyncio.timeout(10):
                            entry["value"] = describe_bytes(await client.read_gatt_char(char))
                    except Exception as err:  # noqa: BLE001
                        entry["read_error"] = repr(err)
                if "notify" in char.properties or "indicate" in char.properties:
                    notifiable.append(char)
                chars.append(entry)
            services.append(
                {"uuid": service.uuid, "description": service.description, "characteristics": chars}
            )
        result["services"] = services

        if listen_seconds and notifiable:
            received: list[dict[str, Any]] = []
            loop = asyncio.get_running_loop()
            start = loop.time()

            def _handler(char: Any, data: bytearray) -> None:
                received.append(
                    {
                        "uuid": getattr(char, "uuid", str(char)),
                        "t": round(loop.time() - start, 2),
                        **describe_bytes(data),
                    }
                )

            subscribed = []
            for char in notifiable:
                try:
                    await client.start_notify(char, _handler)
                    subscribed.append(char)
                except Exception as err:  # noqa: BLE001
                    received.append({"uuid": char.uuid, "subscribe_error": repr(err)})
            await asyncio.sleep(listen_seconds)
            for char in subscribed:
                try:
                    await client.stop_notify(char)
                except Exception:  # noqa: BLE001
                    pass
            result["notifications"] = received[:200]
    finally:
        await client.disconnect()
    return result


# --- reading the VK7 -------------------------------------------------------------

VK7_MANUFACTURER_ID = 2158
REQUEST_TIMEOUT = 3
READ_TIMEOUT = 60
FIRST_READ_DELAY = 30  # s after start
RETRY_AFTER_SUCCESS = timedelta(minutes=5)
RETRY_AFTER_FAILURE = timedelta(minutes=10)
AWAKE_WINDOW = timedelta(minutes=30)  # after a wake-up advertisement, try harder
AWAKE_FAST_RETRIES = timedelta(minutes=3)  # first minutes: retry every 30 s
AWAKE_RETRY_FAST = 30  # s
AWAKE_RETRY_SLOW = 120  # s
AWAKE_MIN_GAP = 20  # s between reads triggered by a wake-up advertisement


class Vk7Unreachable(HomeAssistantError):
    """The VK7 is not in range or does not accept a connection (asleep)."""


async def async_read_vk7(hass: HomeAssistant, address: str, max_attempts: int = 1) -> Vk7Live:
    """Connect to the VK7 and read battery, filter bag, runtimes and the latest event."""
    async with asyncio.timeout(READ_TIMEOUT):
        return await _read_vk7(hass, address, max_attempts)


async def _read_vk7(hass: HomeAssistant, address: str, max_attempts: int) -> Vk7Live:
    from bleak_retry_connector import (  # noqa: PLC0415
        BleakClientWithServiceCache,
        establish_connection,
    )

    from homeassistant.components import bluetooth  # noqa: PLC0415

    ble_device = bluetooth.async_ble_device_from_address(hass, address, connectable=True)
    if ble_device is None:
        raise Vk7Unreachable("VK7 not in range of a connectable Bluetooth adapter or proxy")
    try:
        client = await establish_connection(
            BleakClientWithServiceCache, ble_device, "VK7", max_attempts=max_attempts
        )
    except Exception as err:  # noqa: BLE001 - asleep / busy / out of range
        raise Vk7Unreachable(str(err)) from err

    live = Vk7Live()
    try:
        char = client.services.get_characteristic(VK7_CHAR_UUID)
        if char is None:
            raise HomeAssistantError("VK7 data characteristic not found")
        queue: asyncio.Queue[bytes] = asyncio.Queue()

        def _on_indication(_char: Any, data: bytearray) -> None:
            queue.put_nowait(bytes(data))

        await client.start_notify(char, _on_indication)

        async def _ask(index: int, sub: int, param: int) -> bytes | None:
            while not queue.empty():
                queue.get_nowait()
            await client.write_gatt_char(char, vk7_read_request(index, sub, param), response=True)
            try:
                async with asyncio.timeout(REQUEST_TIMEOUT):
                    while True:
                        parsed = parse_vk7_response(await queue.get())
                        if parsed and parsed[0] == index and parsed[1] == sub:
                            return parsed[2]
            except TimeoutError:
                _LOGGER.debug("VK7 did not answer %04x/%d", index, sub)
                return None

        for index, sub, param in VK7_READ_PLAN:
            if (data := await _ask(index, sub, param)) is not None:
                apply_vk7_value(live, index, sub, data)
        if live.event_index is not None:
            if (data := await _ask(VK7_IDX_EVENT_LOG, 0, live.event_index)) is not None:
                apply_vk7_value(live, VK7_IDX_EVENT_LOG, 0, data)
        try:
            await client.stop_notify(char)
        except Exception:  # noqa: BLE001
            pass
    finally:
        await client.disconnect()
    if live.battery is None and not live.runtimes_s:
        raise HomeAssistantError("VK7 connected but returned no data")
    live.read_at = dt_util.utcnow()
    return live


class Vk7BleReader:
    """Reads a VK7 whenever it is awake (in use or charging) and in range.

    The VK7 advertises all the time but only accepts connections while it is
    awake, so this tries every few minutes and right away when an advertisement
    signals that it was woken up.
    """

    def __init__(
        self,
        hass: HomeAssistant,
        address: str,
        on_data: Callable[[Vk7Live], None],
        create_task: Callable[[Any], Any],
    ) -> None:
        self.hass = hass
        self.address = address
        self._on_data = on_data
        self._create_task = create_task
        self._mac_bytes = bytes.fromhex((normalise_mac(address) or "").replace(":", ""))
        self._lock = asyncio.Lock()
        self._started = time.monotonic()
        self._last_attempt: float | None = None
        self._next_attempt = self._started + FIRST_READ_DELAY
        self._woken_at: float | None = None
        self.last_error: str | None = None
        self.last_success: Any = None

    @callback
    def start(self) -> Callable[[], None]:
        from homeassistant.components import bluetooth  # noqa: PLC0415

        unsubs = [
            bluetooth.async_register_callback(
                self.hass,
                self._async_advertisement,
                bluetooth.BluetoothCallbackMatcher(address=self.address, connectable=False),
                bluetooth.BluetoothScanningMode.PASSIVE,
            ),
            async_track_time_interval(self.hass, self._async_tick, timedelta(seconds=15)),
        ]

        @callback
        def _stop() -> None:
            for unsub in unsubs:
                unsub()

        return _stop

    @callback
    def _async_advertisement(self, info: Any, _change: Any) -> None:
        payload = info.manufacturer_data.get(VK7_MANUFACTURER_ID)
        if payload is None or payload == self._mac_bytes:
            return
        # a status payload instead of the address: the VK7 was just woken up
        now = time.monotonic()
        if self._woken_at is None or now - self._woken_at > AWAKE_MIN_GAP:
            _LOGGER.debug("VK7 wake-up advertisement: %s", payload.hex())
        self._woken_at = now
        if self._last_attempt is None or now - self._last_attempt > AWAKE_MIN_GAP:
            self._next_attempt = min(self._next_attempt, now)
            self._schedule()

    @callback
    def _async_tick(self, _now: Any) -> None:
        if time.monotonic() >= self._next_attempt:
            self._schedule()

    def _retry_delay(self, success: bool) -> float:
        now = time.monotonic()
        if success:
            return RETRY_AFTER_SUCCESS.total_seconds()
        if self._woken_at is not None and now - self._woken_at < AWAKE_WINDOW.total_seconds():
            if now - self._woken_at < AWAKE_FAST_RETRIES.total_seconds():
                return AWAKE_RETRY_FAST
            return AWAKE_RETRY_SLOW
        return RETRY_AFTER_FAILURE.total_seconds()

    @callback
    def _schedule(self) -> None:
        if not self._lock.locked():
            self._create_task(self._async_read())

    async def _async_read(self) -> None:
        from homeassistant.components import bluetooth  # noqa: PLC0415

        async with self._lock:
            now = time.monotonic()
            if now < self._next_attempt:
                return
            self._last_attempt = now
            if not bluetooth.async_address_present(self.hass, self.address, connectable=True):
                self._next_attempt = now + RETRY_AFTER_FAILURE.total_seconds()
                return
            try:
                live = await async_read_vk7(self.hass, self.address)
            except (HomeAssistantError, TimeoutError) as err:
                self.last_error = str(err)
                delay = self._retry_delay(success=False)
                _LOGGER.debug("VK7 not readable right now (retry in %ss): %s", int(delay), err)
                self._next_attempt = time.monotonic() + delay
                return
            self.last_error = None
            self.last_success = live.read_at
            self._next_attempt = time.monotonic() + self._retry_delay(success=True)
            _LOGGER.debug("VK7 read: battery %s%%, bag %s", live.battery, live.bag)
            self._on_data(live)
