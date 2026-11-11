"""Keeps the VK7 maintenance trackers going from Bluetooth runtimes."""

from __future__ import annotations

from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .const import DOMAIN
from .protocol import (
    TrackerBaseline,
    Vk7Data,
    Vk7Tracker,
    tracker_hours,
    tracker_progress,
    vk7_source_runtime,
)

STORAGE_VERSION = 1


class Vk7Maintenance:
    """Per tracker: hours at an anchor (last app sync or a reset in HA) plus runtime since."""

    def __init__(self, hass: HomeAssistant, entry_id: str) -> None:
        self._store: Store[dict[str, Any]] = Store(
            hass, STORAGE_VERSION, f"{DOMAIN}.{entry_id}.vk7_maintenance"
        )
        self._baselines: dict[str, TrackerBaseline] = {}

    async def async_load(self) -> None:
        data = await self._store.async_load() or {}
        for key, value in (data.get("baselines") or {}).items():
            try:
                self._baselines[key] = TrackerBaseline(
                    float(value["hours"]), int(value["runtime_s"]), str(value["anchor"])
                )
            except (KeyError, TypeError, ValueError):
                continue

    def _save(self) -> None:
        self._store.async_delay_save(
            lambda: {
                "baselines": {
                    key: {"hours": b.hours, "runtime_s": b.runtime_s, "anchor": b.anchor}
                    for key, b in self._baselines.items()
                }
            },
            10,
        )

    @staticmethod
    def _key(device: Vk7Data, tracker: Vk7Tracker) -> str:
        return f"{device.serial}_{tracker.tracker_id}"

    def state(self, device: Vk7Data, tracker: Vk7Tracker) -> tuple[float | None, int | None, str]:
        """(hours, progress %, source) of one tracker."""
        key = self._key(device, tracker)
        old = self._baselines.get(key)
        hours, baseline, source = tracker_hours(tracker, device.last_sync, device.live, old)
        if baseline is not None and baseline != old:
            self._baselines[key] = baseline
            self._save()
        return hours, tracker_progress(hours, tracker), source

    def reset(self, device: Vk7Data, tracker: Vk7Tracker) -> None:
        """Maintenance done: start counting from zero (only in Home Assistant)."""
        runtime = vk7_source_runtime(device.live, tracker.source)
        if runtime is None:
            raise HomeAssistantError(
                "The VK7 has not been read over Bluetooth yet; use it once and try again"
            )
        self._baselines[self._key(device, tracker)] = TrackerBaseline(
            0.0, runtime, f"reset:{dt_util.utcnow().isoformat()}"
        )
        self._save()
