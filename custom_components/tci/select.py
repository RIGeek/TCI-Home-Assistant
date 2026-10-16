"""Select entities (mode) for TCI radios."""

from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import TciConfigEntry
from .const import CONF_DISCOVERY
from .entity import TciEntity, build_entities

FALLBACK_MODES = ["AM", "LSB", "USB", "CW", "NFM", "DIGL", "DIGU"]


async def async_setup_entry(
    hass: HomeAssistant, entry: TciConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    async_add_entities(build_entities(entry, "select", TciModeSelect))


class TciModeSelect(TciEntity, SelectEntity):
    """Mode select; options come from the server's modulations_list."""

    def _modes(self) -> list[str]:
        modes = self._client.modulations or self._entry.data.get(
            CONF_DISCOVERY, {}
        ).get("modulations") or FALLBACK_MODES
        return [m.upper() for m in modes]

    @property
    def options(self) -> list[str]:
        modes = self._modes()
        current = self.current_option
        if current and current not in modes:
            modes.append(current)
        return modes

    @property
    def current_option(self) -> str | None:
        value = self.value
        return value.upper() if value else None

    async def async_select_option(self, option: str) -> None:
        await self._send_value(option.lower())
