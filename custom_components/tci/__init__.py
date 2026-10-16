"""The TCI (Transceiver Control Interface) integration."""

from __future__ import annotations

from dataclasses import dataclass, field
import logging
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.const import CONF_HOST, CONF_PORT, Platform
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, ServiceCall, callback
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.event import async_call_later

from .const import (
    ATTR_COMMAND,
    ATTR_CONFIG_ENTRY,
    CONF_SENSOR_INTERVAL,
    CONF_TX_ENABLED,
    CONF_TX_TIMEOUT,
    DEFAULT_SENSOR_INTERVAL,
    DEFAULT_TX_TIMEOUT,
    DOMAIN,
    SERVICE_SEND_COMMAND,
)
from .tci_client import TciClient, TciError, make_key

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [
    Platform.BINARY_SENSOR,
    Platform.NUMBER,
    Platform.SELECT,
    Platform.SENSOR,
    Platform.SWITCH,
]

# Raw commands that key the transmitter; blocked unless TX control is on.
TX_COMMANDS = {"trx", "tune"}
# Binary streams the integration does not consume.
BLOCKED_COMMANDS = {"audio_start", "iq_start", "line_out_start"}

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

SEND_COMMAND_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_COMMAND): cv.string,
        vol.Optional(ATTR_CONFIG_ENTRY): cv.string,
    }
)


class TxGuard:
    """Unkeys transmissions that Home Assistant started after a timeout."""

    def __init__(self, hass: HomeAssistant, client: TciClient, timeout: int) -> None:
        self._hass = hass
        self._client = client
        self._timeout = timeout
        # (command name, trx) -> cancel handle
        self._timers: dict[tuple[str, int], CALLBACK_TYPE] = {}
        self._unsubs: list[CALLBACK_TYPE] = []
        self._watched: set[tuple[str, int]] = set()

    def is_armed(self, name: str, trx: int) -> bool:
        return (name, trx) in self._timers

    async def key(self, name: str, trx: int) -> None:
        """Key TX via HA and arm the watchdog."""
        await self._client.send(f"{name}:{trx},true;")
        self._arm(name, trx)

    async def unkey(self, name: str, trx: int) -> None:
        self._disarm(name, trx)
        await self._client.send(f"{name}:{trx},false;")

    def _arm(self, name: str, trx: int) -> None:
        self._disarm(name, trx)
        if self._timeout <= 0:
            return

        async def _expire(_now: Any) -> None:
            self._timers.pop((name, trx), None)
            _LOGGER.warning(
                "TCI %s on RX%d exceeded %ss; unkeying", name, trx + 1, self._timeout
            )
            try:
                await self._client.send(f"{name}:{trx},false;")
            except TciError as err:
                _LOGGER.error("Could not unkey %s on RX%d: %s", name, trx + 1, err)

        self._timers[(name, trx)] = async_call_later(self._hass, self._timeout, _expire)

        # If the radio reports RX on its own, stop watching (one listener per key).
        if (name, trx) not in self._watched:
            self._watched.add((name, trx))

            @callback
            def _on_change() -> None:
                if self._client.get(make_key(name, trx)) is False:
                    self._disarm(name, trx)

            self._unsubs.append(
                self._client.add_listener(make_key(name, trx), _on_change)
            )

    def _disarm(self, name: str, trx: int) -> None:
        if cancel := self._timers.pop((name, trx), None):
            cancel()

    async def shutdown(self) -> None:
        """Unkey anything HA keyed, then stop."""
        for name, trx in list(self._timers):
            self._disarm(name, trx)
            try:
                await self._client.send(f"{name}:{trx},false;")
            except TciError:
                pass
        for unsub in self._unsubs:
            unsub()
        self._unsubs.clear()


@dataclass
class TciRuntimeData:
    client: TciClient
    tx_guard: TxGuard
    tx_enabled: bool
    unsubs: list[CALLBACK_TYPE] = field(default_factory=list)


type TciConfigEntry = ConfigEntry[TciRuntimeData]


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    """Register the raw command service."""

    async def _send_command(call: ServiceCall) -> None:
        entry = _resolve_entry(hass, call.data.get(ATTR_CONFIG_ENTRY))
        data: TciRuntimeData = entry.runtime_data
        command: str = call.data[ATTR_COMMAND]
        for part in filter(None, (p.strip() for p in command.split(";"))):
            name = part.partition(":")[0].strip().lower()
            if name in BLOCKED_COMMANDS:
                raise ServiceValidationError(f"'{name}' starts a binary stream and is not supported")
            if name in TX_COMMANDS and not data.tx_enabled:
                raise ServiceValidationError(
                    f"'{name}' keys the transmitter; enable TX control in the integration options first"
                )
        try:
            await data.client.send(command)
        except TciError as err:
            raise HomeAssistantError(str(err)) from err

    hass.services.async_register(
        DOMAIN, SERVICE_SEND_COMMAND, _send_command, schema=SEND_COMMAND_SCHEMA
    )
    return True


def _resolve_entry(hass: HomeAssistant, entry_id: str | None) -> TciConfigEntry:
    entries = [
        e for e in hass.config_entries.async_entries(DOMAIN)
        if e.state is ConfigEntryState.LOADED
    ]
    if entry_id:
        for entry in entries:
            if entry.entry_id == entry_id:
                return entry
        raise ServiceValidationError(f"TCI entry {entry_id} not found or not loaded")
    if len(entries) == 1:
        return entries[0]
    if not entries:
        raise ServiceValidationError("No TCI radio is loaded")
    raise ServiceValidationError("Several TCI radios are configured; set config_entry_id")


async def async_setup_entry(hass: HomeAssistant, entry: TciConfigEntry) -> bool:
    opts = entry.options
    client = TciClient(
        async_get_clientsession(hass),
        entry.data[CONF_HOST],
        entry.data[CONF_PORT],
        sensor_interval_ms=int(opts.get(CONF_SENSOR_INTERVAL, DEFAULT_SENSOR_INTERVAL)),
    )
    tx_enabled = bool(opts.get(CONF_TX_ENABLED, False))
    guard = TxGuard(hass, client, int(opts.get(CONF_TX_TIMEOUT, DEFAULT_TX_TIMEOUT)))
    entry.runtime_data = TciRuntimeData(client=client, tx_guard=guard, tx_enabled=tx_enabled)

    entry.async_create_background_task(hass, client.run(), f"tci {client.url}")
    entry.async_on_unload(entry.add_update_listener(_async_reload))

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def _async_reload(hass: HomeAssistant, entry: TciConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: TciConfigEntry) -> bool:
    data = entry.runtime_data
    await data.tx_guard.shutdown()
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    await data.client.close()
    return unloaded
