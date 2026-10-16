"""Base entity and entity builder for the TCI integration."""

from __future__ import annotations

from collections.abc import Callable
import time
from typing import TYPE_CHECKING, Any

from homeassistant.core import CALLBACK_TYPE, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.event import async_call_later

from .const import (
    CONF_CONTROL,
    CONF_DISCOVERY,
    CONF_MONITOR,
    CONF_RECEIVERS,
    CONF_TX_ELEMENTS,
    CONF_TX_ENABLED,
    CONF_UPDATE_INTERVAL,
    DEFAULT_UPDATE_INTERVAL,
    DOMAIN,
    VFO_LETTERS,
)
from .elements import (
    ELEMENTS_BY_KEY,
    GROUP_CONTROL,
    GROUP_MONITOR,
    SCOPE_CHANNEL,
    SCOPE_RADIO,
    SCOPE_TRX,
    TciElement,
    default_keys,
)
from .tci_client import CONNECTION_KEY, TciError, format_value, make_key

if TYPE_CHECKING:
    from . import TciConfigEntry


def selected_elements(entry: TciConfigEntry) -> list[TciElement]:
    """Elements the user enabled in the options dialog."""
    opts = entry.options
    disc = entry.data.get(CONF_DISCOVERY, {})
    keys = list(opts.get(CONF_MONITOR, default_keys(GROUP_MONITOR)))
    keys += list(opts.get(CONF_CONTROL, default_keys(GROUP_CONTROL)))
    if opts.get(CONF_TX_ENABLED, False) and not disc.get("receive_only", False):
        keys += list(opts.get(CONF_TX_ELEMENTS, []))
    return [ELEMENTS_BY_KEY[k] for k in keys if k in ELEMENTS_BY_KEY]


def build_entities(
    entry: TciConfigEntry,
    platform: str,
    factory: Callable[[TciConfigEntry, TciElement, int | None, int | None], Entity],
) -> list[Entity]:
    """Create one entity per selected element x receiver x channel."""
    disc = entry.data.get(CONF_DISCOVERY, {})
    trx_count = int(disc.get("trx_count", 1))
    chan_count = int(disc.get("channels_count", 2))
    receivers = [
        int(r) for r in entry.options.get(CONF_RECEIVERS, [str(i) for i in range(trx_count)])
    ]
    entities: list[Entity] = []
    for element in selected_elements(entry):
        if element.platform != platform:
            continue
        if element.scope == SCOPE_RADIO:
            entities.append(factory(entry, element, None, None))
        elif element.scope == SCOPE_TRX:
            entities += [factory(entry, element, trx, None) for trx in receivers]
        elif element.scope == SCOPE_CHANNEL:
            entities += [
                factory(entry, element, trx, chan)
                for trx in receivers
                for chan in range(element.min_channel, chan_count)
            ]
    return entities


def radio_device(entry: TciConfigEntry) -> DeviceInfo:
    disc = entry.data.get(CONF_DISCOVERY, {})
    return DeviceInfo(
        identifiers={(DOMAIN, entry.entry_id)},
        name=entry.title,
        manufacturer="TCI",
        model=disc.get("device"),
        sw_version=disc.get("protocol"),
        configuration_url=None,
    )


def trx_device(entry: TciConfigEntry, trx: int) -> DeviceInfo:
    return DeviceInfo(
        identifiers={(DOMAIN, f"{entry.entry_id}_rx{trx}")},
        name=f"{entry.title} RX{trx + 1}",
        via_device=(DOMAIN, entry.entry_id),
    )


class TciEntity(Entity):
    """Push-updated entity bound to one TCI state key."""

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(
        self,
        entry: TciConfigEntry,
        element: TciElement,
        trx: int | None,
        chan: int | None,
    ) -> None:
        self._entry = entry
        self._client = entry.runtime_data.client
        self.element = element
        self._trx = trx
        self._chan = chan
        self._state_key = make_key(element.state, trx, chan)
        self._update_interval = float(
            entry.options.get(CONF_UPDATE_INTERVAL, DEFAULT_UPDATE_INTERVAL)
        )
        self._last_write = float("-inf")
        self._pending: CALLBACK_TYPE | None = None

        scope_id = "radio" if trx is None else f"rx{trx}" + ("" if chan is None else f"_{chan}")
        self._attr_unique_id = f"{entry.entry_id}_{scope_id}_{element.key}"
        letter = VFO_LETTERS[chan] if chan is not None and chan < len(VFO_LETTERS) else str(chan)
        self._attr_name = element.name.format(vfo=letter)
        self._attr_icon = element.icon
        self._attr_entity_registry_enabled_default = element.enabled_default
        self._attr_device_info = (
            radio_device(entry) if trx is None else trx_device(entry, trx)
        )

    # ------------------------------------------------------------- state
    @property
    def raw_value(self) -> Any:
        return self._client.get(self._state_key)

    @property
    def value(self) -> Any:
        raw = self.raw_value
        if raw is None:
            return None
        return self.element.value_fn(raw) if self.element.value_fn else raw

    @property
    def available(self) -> bool:
        return self._client.connected

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        if self.element.attrs_fn is None:
            return None
        raw = self.raw_value
        return self.element.attrs_fn(raw) if raw is not None else None

    # ------------------------------------------------------- lifecycle
    async def async_added_to_hass(self) -> None:
        self.async_on_remove(self._client.add_listener(self._state_key, self._on_update))
        self.async_on_remove(self._client.add_listener(CONNECTION_KEY, self._on_connection))
        self.async_on_remove(self._cancel_pending)

    @callback
    def _on_connection(self) -> None:
        self._cancel_pending()
        self.async_write_ha_state()

    @callback
    def _on_update(self) -> None:
        if not self.element.fast or self._update_interval <= 0:
            self.async_write_ha_state()
            return
        # Throttle high-rate data so the recorder isn't flooded; the latest
        # value is always written once the interval elapses.
        now = time.monotonic()
        wait = self._update_interval - (now - self._last_write)
        if wait <= 0:
            self._write_now()
        elif self._pending is None:
            self._pending = async_call_later(self.hass, wait, self._flush)

    @callback
    def _flush(self, _now: Any) -> None:
        self._pending = None
        self._write_now()

    @callback
    def _write_now(self) -> None:
        self._last_write = time.monotonic()
        self.async_write_ha_state()

    @callback
    def _cancel_pending(self) -> None:
        if self._pending is not None:
            self._pending()
            self._pending = None

    # ---------------------------------------------------------- commands
    async def _send_value(self, value: Any) -> None:
        template = self.element.command
        if template is None:
            raise HomeAssistantError(f"{self.element.key} is read-only")
        await self._send(
            template.format(trx=self._trx, chan=self._chan, value=format_value(value))
        )

    async def _send(self, command: str) -> None:
        try:
            await self._client.send(command)
        except TciError as err:
            raise HomeAssistantError(f"TCI command failed: {err}") from err

