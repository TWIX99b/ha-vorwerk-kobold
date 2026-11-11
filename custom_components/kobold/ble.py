"""Bluetooth access to the VK7 stick vacuum (through Home Assistant Bluetooth / proxies).

The VK7 talks to the MyKobold app over Bluetooth LE only. This module keeps that
optional: it is only used when Home Assistant's Bluetooth integration is loaded.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError

from .protocol import describe_bytes

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

    client = await establish_connection(
        BleakClientWithServiceCache, ble_device, "VK7", max_attempts=3
    )
    try:
        details = getattr(ble_device, "details", None)
        if isinstance(details, dict):
            result["connected_via"] = details.get("source")
        if pair:
            try:
                result["paired"] = await client.pair()
            except Exception as err:  # noqa: BLE001 - report everything to the caller
                result["pair_error"] = repr(err)
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
