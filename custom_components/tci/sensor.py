"""Sensors for TCI radios."""

from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import TciConfigEntry
from .elements import TciElement
from .entity import TciEntity, build_entities


async def async_setup_entry(
    hass: HomeAssistant, entry: TciConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    async_add_entities(build_entities(entry, "sensor", TciSensor))


class TciSensor(TciEntity, SensorEntity):
    def __init__(self, entry: TciConfigEntry, element: TciElement, trx, chan) -> None:
        super().__init__(entry, element, trx, chan)
        self._attr_native_unit_of_measurement = element.unit
        if element.device_class:
            self._attr_device_class = SensorDeviceClass(element.device_class)
        if element.state_class:
            self._attr_state_class = SensorStateClass(element.state_class)
        if element.precision is not None:
            self._attr_suggested_display_precision = element.precision

    @property
    def native_value(self) -> Any:
        return self.value
