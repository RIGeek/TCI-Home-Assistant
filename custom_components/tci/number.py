"""Number entities for TCI radios."""

from __future__ import annotations

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import TciConfigEntry
from .const import CONF_DISCOVERY
from .elements import TciElement
from .entity import TciEntity, build_entities

FALLBACK_VFO_LIMITS = (0, 2_000_000_000)


async def async_setup_entry(
    hass: HomeAssistant, entry: TciConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    async_add_entities(build_entities(entry, "number", TciNumber))


class TciNumber(TciEntity, NumberEntity):
    def __init__(self, entry: TciConfigEntry, element: TciElement, trx, chan) -> None:
        super().__init__(entry, element, trx, chan)
        self._attr_native_unit_of_measurement = element.unit
        self._attr_native_step = element.step or 1
        if element.limits_from_vfo:
            self._attr_mode = NumberMode.BOX
            limits = entry.data.get(CONF_DISCOVERY, {}).get("vfo_limits")
            low, high = limits if limits else FALLBACK_VFO_LIMITS
            self._attr_native_min_value = low
            self._attr_native_max_value = high
        else:
            self._attr_mode = NumberMode.SLIDER
            self._attr_native_min_value = element.native_min or 0
            self._attr_native_max_value = (
                element.native_max if element.native_max is not None else 100
            )

    @property
    def native_value(self) -> float | None:
        value = self.value
        return None if value is None else float(value)

    async def async_set_native_value(self, value: float) -> None:
        if (self.element.step or 1) >= 1:
            value = int(round(value))
        await self._send_value(value)
