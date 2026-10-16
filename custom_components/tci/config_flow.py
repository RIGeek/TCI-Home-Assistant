"""Config and options flow for the TCI integration."""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.const import CONF_HOST, CONF_PORT
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    BooleanSelector,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
)

from .const import (
    CONF_CONTROL,
    CONF_DISCOVERY,
    CONF_MONITOR,
    CONF_RECEIVERS,
    CONF_SENSOR_INTERVAL,
    CONF_TX_ELEMENTS,
    CONF_TX_ENABLED,
    CONF_TX_TIMEOUT,
    CONF_UPDATE_INTERVAL,
    DEFAULT_PORT,
    DEFAULT_SENSOR_INTERVAL,
    DEFAULT_TX_TIMEOUT,
    DEFAULT_UPDATE_INTERVAL,
    DOMAIN,
)
from .elements import (
    GROUP_CONTROL,
    GROUP_MONITOR,
    GROUP_TX,
    SCOPE_CHANNEL,
    SCOPE_RADIO,
    default_keys,
    elements_in_group,
)
from .tci_client import TciCannotConnect, probe

_LOGGER = logging.getLogger(__name__)


def _element_label(el) -> str:
    name = el.name.replace(" {vfo}", "").replace("{vfo} ", "")
    if el.scope == SCOPE_CHANNEL:
        return f"{name} (per VFO)"
    if el.scope == SCOPE_RADIO:
        return f"{name} (radio)"
    return name


def _multi(group: str) -> SelectSelector:
    return SelectSelector(
        SelectSelectorConfig(
            options=[
                SelectOptionDict(value=e.key, label=_element_label(e))
                for e in elements_in_group(group)
            ],
            multiple=True,
            mode=SelectSelectorMode.LIST,
        )
    )


def _number(lo: float, hi: float, step: float, unit: str) -> NumberSelector:
    return NumberSelector(
        NumberSelectorConfig(
            min=lo, max=hi, step=step, unit_of_measurement=unit,
            mode=NumberSelectorMode.BOX,
        )
    )


def options_schema(discovery: dict[str, Any], current: dict[str, Any]) -> vol.Schema:
    """Element picker shared by the config and options flows."""
    trx_count = int(discovery.get("trx_count", 1))
    receivers = SelectSelector(
        SelectSelectorConfig(
            options=[SelectOptionDict(value=str(i), label=f"RX{i + 1}") for i in range(trx_count)],
            multiple=True,
            mode=SelectSelectorMode.LIST,
        )
    )
    schema: dict[Any, Any] = {
        vol.Required(
            CONF_RECEIVERS,
            default=current.get(CONF_RECEIVERS, [str(i) for i in range(trx_count)]),
        ): receivers,
        vol.Optional(
            CONF_MONITOR, default=current.get(CONF_MONITOR, default_keys(GROUP_MONITOR))
        ): _multi(GROUP_MONITOR),
        vol.Optional(
            CONF_CONTROL, default=current.get(CONF_CONTROL, default_keys(GROUP_CONTROL))
        ): _multi(GROUP_CONTROL),
    }
    if not discovery.get("receive_only", False):
        schema[vol.Required(CONF_TX_ENABLED, default=current.get(CONF_TX_ENABLED, False))] = BooleanSelector()
        schema[vol.Optional(CONF_TX_ELEMENTS, default=current.get(CONF_TX_ELEMENTS, []))] = _multi(GROUP_TX)
        schema[vol.Required(CONF_TX_TIMEOUT, default=current.get(CONF_TX_TIMEOUT, DEFAULT_TX_TIMEOUT))] = _number(0, 3600, 1, "s")
    schema[vol.Required(
        CONF_SENSOR_INTERVAL, default=current.get(CONF_SENSOR_INTERVAL, DEFAULT_SENSOR_INTERVAL)
    )] = _number(100, 5000, 50, "ms")
    schema[vol.Required(
        CONF_UPDATE_INTERVAL, default=current.get(CONF_UPDATE_INTERVAL, DEFAULT_UPDATE_INTERVAL)
    )] = _number(0, 60, 0.5, "s")
    return vol.Schema(schema)


def _validate_options(user_input: dict[str, Any]) -> dict[str, str]:
    errors: dict[str, str] = {}
    if not user_input.get(CONF_RECEIVERS):
        errors[CONF_RECEIVERS] = "no_receivers"
    if user_input.get(CONF_TX_ENABLED) and not user_input.get(CONF_TX_ELEMENTS):
        errors[CONF_TX_ELEMENTS] = "no_tx_elements"
    return errors


def _normalize(user_input: dict[str, Any]) -> dict[str, Any]:
    out = dict(user_input)
    for key in (CONF_TX_TIMEOUT, CONF_SENSOR_INTERVAL):
        if key in out:
            out[key] = int(out[key])
    if CONF_UPDATE_INTERVAL in out:
        out[CONF_UPDATE_INTERVAL] = float(out[CONF_UPDATE_INTERVAL])
    return out


class TciConfigFlow(ConfigFlow, domain=DOMAIN):
    """Set up a TCI radio."""

    VERSION = 1

    def __init__(self) -> None:
        self._host: str | None = None
        self._port: int = DEFAULT_PORT
        self._discovery: dict[str, Any] = {}

    async def _probe(self, host: str, port: int) -> dict[str, Any]:
        info = await probe(async_get_clientsession(self.hass), host, port)
        init_log = info.pop("init_log", [])
        _LOGGER.debug("TCI init burst from %s:%s: %s", host, port, init_log)
        return info

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            host = user_input[CONF_HOST].strip()
            port = int(user_input[CONF_PORT])
            await self.async_set_unique_id(f"{host}:{port}")
            self._abort_if_unique_id_configured()
            try:
                self._discovery = await self._probe(host, port)
            except TciCannotConnect:
                errors["base"] = "cannot_connect"
            except Exception:  # noqa: BLE001
                _LOGGER.exception("Unexpected error probing TCI server")
                errors["base"] = "unknown"
            else:
                self._host, self._port = host, port
                return await self.async_step_elements()

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_HOST, default=(user_input or {}).get(CONF_HOST, "")): str,
                    vol.Required(CONF_PORT, default=(user_input or {}).get(CONF_PORT, DEFAULT_PORT)): vol.All(
                        vol.Coerce(int), vol.Range(min=1, max=65535)
                    ),
                }
            ),
            errors=errors,
        )

    async def async_step_elements(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            errors = _validate_options(user_input)
            if not errors:
                return self.async_create_entry(
                    title=self._discovery.get("device") or "TCI radio",
                    data={
                        CONF_HOST: self._host,
                        CONF_PORT: self._port,
                        CONF_DISCOVERY: self._discovery,
                    },
                    options=_normalize(user_input),
                )
        return self.async_show_form(
            step_id="elements",
            data_schema=options_schema(self._discovery, user_input or {}),
            errors=errors,
            description_placeholders=_placeholders(self._discovery),
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Change host/port and refresh what the server offers."""
        entry = self._get_reconfigure_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            host = user_input[CONF_HOST].strip()
            port = int(user_input[CONF_PORT])
            try:
                discovery = await self._probe(host, port)
            except TciCannotConnect:
                errors["base"] = "cannot_connect"
            except Exception:  # noqa: BLE001
                _LOGGER.exception("Unexpected error probing TCI server")
                errors["base"] = "unknown"
            else:
                return self.async_update_reload_and_abort(
                    entry,
                    unique_id=f"{host}:{port}",
                    data={CONF_HOST: host, CONF_PORT: port, CONF_DISCOVERY: discovery},
                )
        return self.async_show_form(
            step_id="reconfigure",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_HOST, default=entry.data[CONF_HOST]): str,
                    vol.Required(CONF_PORT, default=entry.data[CONF_PORT]): vol.All(
                        vol.Coerce(int), vol.Range(min=1, max=65535)
                    ),
                }
            ),
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return TciOptionsFlow()


def _placeholders(discovery: dict[str, Any]) -> dict[str, str]:
    modes = discovery.get("modulations") or []
    return {
        "device": str(discovery.get("device") or "TCI radio"),
        "protocol": str(discovery.get("protocol") or "unknown"),
        "trx_count": str(discovery.get("trx_count", 1)),
        "modes": ", ".join(m.upper() for m in modes) or "none reported",
    }


class TciOptionsFlow(OptionsFlow):
    """Pick which TCI elements become entities."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        discovery = self.config_entry.data.get(CONF_DISCOVERY, {})
        errors: dict[str, str] = {}
        if user_input is not None:
            errors = _validate_options(user_input)
            if not errors:
                return self.async_create_entry(data=_normalize(user_input))
        return self.async_show_form(
            step_id="init",
            data_schema=options_schema(discovery, user_input or dict(self.config_entry.options)),
            errors=errors,
            description_placeholders=_placeholders(discovery),
        )
