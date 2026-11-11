"""Select platform for the Vorwerk Kobold integration (robot language)."""

from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import KoboldConfigEntry
from .entity import KoboldSettingEntity
from .protocol import language_code, language_option

PARALLEL_UPDATES = 1


async def async_setup_entry(
    hass: HomeAssistant,
    entry: KoboldConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data
    added: set[str] = set()

    @callback
    def _add_new() -> None:
        new = []
        for robot_id, robot in coordinator.robots.items():
            if robot_id not in added and robot.settings.get("robot_language", {}).get("language"):
                added.add(robot_id)
                new.append(KoboldLanguage(coordinator, robot_id))
        if new:
            async_add_entities(new)

    _add_new()
    entry.async_on_unload(coordinator.async_add_listener(_add_new))


class KoboldLanguage(KoboldSettingEntity, SelectEntity):
    """Language of the robot's voice."""

    _setting = "robot_language"
    _attr_translation_key = "language"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator, robot_id: str) -> None:
        super().__init__(coordinator, robot_id, "language")

    @property
    def _available_codes(self) -> list[str]:
        codes = [str(c) for c in self.setting.get("available_languages") or []]
        current = self.setting.get("language")
        if current and current not in codes:
            codes.append(str(current))
        return codes

    @property
    def options(self) -> list[str]:
        return [language_option(code) for code in self._available_codes]

    @property
    def current_option(self) -> str | None:
        current = self.setting.get("language")
        return language_option(str(current)) if current else None

    async def async_select_option(self, option: str) -> None:
        await self.coordinator.async_set_setting(
            self.robot_id, "robot_language", language=language_code(option, self._available_codes)
        )
