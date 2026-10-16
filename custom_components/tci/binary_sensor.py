"""Binary sensors for TCI radios."""

from __future__ import annotations

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import TciConfigEntry
from .elements import TciElement
from .entity import TciEntity, build_entities


async def async_setup_entry(
    hass: HomeAssistant, entry: TciConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    async_add_entities(build_entities(entry, "binary_sensor", TciBinarySensor))


class TciBinarySensor(TciEntity, BinarySensorEntity):
    def __init__(self, entry: TciConfigEntry, element: TciElement, trx, chan) -> None:
        super().__init__(entry, element, trx, chan)
        if element.device_class:
            self._attr_device_class = BinarySensorDeviceClass(element.device_class)

    @property
    def is_on(self) -> bool | None:
        value = self.value
        return None if value is None else bool(value)
