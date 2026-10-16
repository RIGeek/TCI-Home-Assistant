"""Diagnostics for the TCI integration.

The init burst captured here is the quickest way to see exactly which
TCI commands a given server (Zeus, ExpertSDR, ...) actually supports.
"""

from __future__ import annotations

from typing import Any

from homeassistant.core import HomeAssistant

from . import TciConfigEntry


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: TciConfigEntry
) -> dict[str, Any]:
    client = entry.runtime_data.client
    return {
        "data": dict(entry.data),
        "options": dict(entry.options),
        "connected": client.connected,
        "ready_via_fallback": client.ready_via_fallback,
        "last_error": client.last_error,
        "init_burst": list(client.init_log),
        "state": {k: v for k, v in sorted(client.state.items())},
        "unhandled_commands": sorted(
            k.removeprefix("raw:") for k in client.state if k.startswith("raw:")
        ),
    }
