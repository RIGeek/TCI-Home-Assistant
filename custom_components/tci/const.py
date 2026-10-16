"""Constants for the TCI integration."""

from __future__ import annotations

DOMAIN = "tci"
DEFAULT_PORT = 40001

# entry.data
CONF_DISCOVERY = "discovery"

# entry.options
CONF_RECEIVERS = "receivers"
CONF_MONITOR = "monitor"
CONF_CONTROL = "control"
CONF_TX_ENABLED = "tx_enabled"
CONF_TX_ELEMENTS = "tx_elements"
CONF_TX_TIMEOUT = "tx_timeout"
CONF_SENSOR_INTERVAL = "sensor_interval_ms"
CONF_UPDATE_INTERVAL = "update_interval"

DEFAULT_TX_TIMEOUT = 180  # seconds; HA-initiated TX is unkeyed after this
DEFAULT_SENSOR_INTERVAL = 500  # ms between S-meter / TX sensor reports
DEFAULT_UPDATE_INTERVAL = 1.0  # s; min time between HA writes for fast sensors

SERVICE_SEND_COMMAND = "send_command"
ATTR_COMMAND = "command"
ATTR_CONFIG_ENTRY = "config_entry_id"

VFO_LETTERS = "ABCDEFGH"
