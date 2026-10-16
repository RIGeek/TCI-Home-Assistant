"""Switches for TCI radios."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import TciConfigEntry
from .entity import TciEntity, build_entities
from .tci_client import TciError


async def async_setup_entry(
    hass: HomeAssistant, entry: TciConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    async_add_entities(build_entities(entry, "switch", TciSwitch))


class TciSwitch(TciEntity, SwitchEntity):
    @property
    def is_on(self) -> bool | None:
        value = self.value
        return None if value is None else bool(value)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        if self.element.tx_guard:
            guard = self._entry.runtime_data.tx_guard
            return {"watchdog_armed": guard.is_armed(self.element.state, self._trx)}
        return super().extra_state_attributes

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._set(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._set(False)

    async def _set(self, on: bool) -> None:
        el = self.element
        if el.tx_guard:
            guard = self._entry.runtime_data.tx_guard
            try:
                if on:
                    await guard.key(el.state, self._trx)
                else:
                    await guard.unkey(el.state, self._trx)
            except TciError as err:
                raise HomeAssistantError(f"TCI command failed: {err}") from err
            self.async_write_ha_state()
        elif el.on_command and el.off_command:
            await self._send(el.on_command if on else el.off_command)
        else:
            await self._send_value(on)
