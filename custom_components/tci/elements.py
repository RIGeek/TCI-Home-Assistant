"""Catalog of TCI elements the user can pick in the options dialog.

Each element maps one piece of TCI state to one Home Assistant entity per
radio, receiver (TRX) or receiver channel (VFO), depending on its scope.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

SCOPE_RADIO = "radio"
SCOPE_TRX = "trx"
SCOPE_CHANNEL = "channel"

GROUP_MONITOR = "monitor"
GROUP_CONTROL = "control"
GROUP_TX = "tx"


def s_units(dbm: float | None) -> str | None:
    """Convert dBm to an S-meter reading (HF: S9 = -73 dBm, 6 dB/S-unit)."""
    if dbm is None:
        return None
    if dbm <= -73:
        s = max(0, round(9 + (dbm + 73) / 6))
        return f"S{s}"
    return f"S9+{round(dbm + 73)}"


@dataclass(frozen=True, kw_only=True)
class TciElement:
    """One selectable TCI element."""

    key: str
    name: str
    platform: str  # sensor | binary_sensor | switch | number | select
    scope: str
    group: str
    state: str  # client state name; full key adds trx/chan
    default: bool = False
    fast: bool = False  # high-rate data, throttled before writing to HA
    command: str | None = None  # template: {trx} {chan} {value}
    on_command: str | None = None
    off_command: str | None = None
    tx_guard: bool = False  # keying the transmitter; watchdog applies
    min_channel: int = 0
    unit: str | None = None
    device_class: str | None = None
    state_class: str | None = None
    icon: str | None = None
    precision: int | None = None
    native_min: float | None = None
    native_max: float | None = None
    step: float | None = None
    limits_from_vfo: bool = False
    enabled_default: bool = True
    value_fn: Callable[[Any], Any] | None = None
    attrs_fn: Callable[[Any], dict[str, Any]] | None = None


ELEMENTS: tuple[TciElement, ...] = (
    # ---------------------------------------------------------- monitor
    TciElement(key="vfo", name="VFO {vfo}", platform="sensor", scope=SCOPE_CHANNEL,
               group=GROUP_MONITOR, state="vfo", default=True, unit="Hz",
               device_class="frequency", icon="mdi:sine-wave", precision=0),
    TciElement(key="smeter", name="S-meter {vfo}", platform="sensor",
               scope=SCOPE_CHANNEL, group=GROUP_MONITOR, state="smeter",
               default=True, fast=True, unit="dBm", device_class="signal_strength",
               state_class="measurement", precision=1,
               attrs_fn=lambda v: {"s_units": s_units(v)}),
    TciElement(key="mode", name="Mode", platform="sensor", scope=SCOPE_TRX,
               group=GROUP_MONITOR, state="modulation", default=True,
               icon="mdi:radio", value_fn=lambda v: v.upper() if v else None),
    TciElement(key="dds", name="Center frequency", platform="sensor",
               scope=SCOPE_TRX, group=GROUP_MONITOR, state="dds", unit="Hz",
               device_class="frequency", precision=0),
    TciElement(key="filter_width", name="Filter width", platform="sensor",
               scope=SCOPE_TRX, group=GROUP_MONITOR, state="rx_filter_band",
               unit="Hz", icon="mdi:filter", precision=0,
               value_fn=lambda v: (v[1] - v[0]) if v else None,
               attrs_fn=lambda v: {"low": v[0], "high": v[1]} if v else {}),
    TciElement(key="tx_active", name="Transmitting", platform="binary_sensor",
               scope=SCOPE_TRX, group=GROUP_MONITOR, state="trx", default=True,
               icon="mdi:radio-tower"),
    TciElement(key="tune_active", name="Tuning", platform="binary_sensor",
               scope=SCOPE_TRX, group=GROUP_MONITOR, state="tune",
               icon="mdi:tune-vertical"),
    TciElement(key="tx_power", name="TX power", platform="sensor", scope=SCOPE_TRX,
               group=GROUP_MONITOR, state="tx_power", default=True, fast=True,
               unit="W", device_class="power", state_class="measurement",
               precision=1),
    TciElement(key="tx_peak", name="TX peak power", platform="sensor",
               scope=SCOPE_TRX, group=GROUP_MONITOR, state="tx_peak", fast=True,
               unit="W", device_class="power", state_class="measurement",
               precision=1),
    TciElement(key="swr", name="SWR", platform="sensor", scope=SCOPE_TRX,
               group=GROUP_MONITOR, state="tx_swr", default=True, fast=True,
               state_class="measurement", icon="mdi:antenna", precision=2),
    TciElement(key="mic", name="Mic level", platform="sensor", scope=SCOPE_TRX,
               group=GROUP_MONITOR, state="tx_mic", fast=True, unit="dBm",
               state_class="measurement", icon="mdi:microphone", precision=1),
    TciElement(key="drive_level", name="Drive", platform="sensor", scope=SCOPE_TRX,
               group=GROUP_MONITOR, state="drive", unit="%",
               icon="mdi:knob"),
    TciElement(key="split", name="Split", platform="binary_sensor", scope=SCOPE_TRX,
               group=GROUP_MONITOR, state="split_enable", icon="mdi:call-split"),
    TciElement(key="rit_offset", name="RIT offset", platform="sensor",
               scope=SCOPE_TRX, group=GROUP_MONITOR, state="rit_offset", unit="Hz"),
    TciElement(key="xit_offset", name="XIT offset", platform="sensor",
               scope=SCOPE_TRX, group=GROUP_MONITOR, state="xit_offset", unit="Hz"),
    TciElement(key="agc", name="AGC", platform="sensor", scope=SCOPE_TRX,
               group=GROUP_MONITOR, state="agc_mode", icon="mdi:tune",
               value_fn=lambda v: v.upper() if v else None),
    TciElement(key="running", name="Running", platform="binary_sensor",
               scope=SCOPE_RADIO, group=GROUP_MONITOR, state="running",
               default=True, device_class="running"),
    TciElement(key="volume", name="Volume", platform="sensor", scope=SCOPE_RADIO,
               group=GROUP_MONITOR, state="volume", unit="dB",
               icon="mdi:volume-high"),
    TciElement(key="muted", name="Muted", platform="binary_sensor",
               scope=SCOPE_RADIO, group=GROUP_MONITOR, state="mute",
               icon="mdi:volume-off"),
    # ---------------------------------------------------------- control
    TciElement(key="mode_select", name="Mode", platform="select", scope=SCOPE_TRX,
               group=GROUP_CONTROL, state="modulation",
               command="modulation:{trx},{value}", icon="mdi:radio"),
    TciElement(key="vfo_set", name="VFO {vfo} tune", platform="number",
               scope=SCOPE_CHANNEL, group=GROUP_CONTROL, state="vfo",
               command="vfo:{trx},{chan},{value}", unit="Hz", step=1,
               limits_from_vfo=True, icon="mdi:sine-wave"),
    TciElement(key="volume_set", name="Volume", platform="number",
               scope=SCOPE_RADIO, group=GROUP_CONTROL, state="volume",
               command="volume:{value}", unit="dB", native_min=-60,
               native_max=0, step=1, icon="mdi:volume-high"),
    TciElement(key="mute_switch", name="Mute", platform="switch",
               scope=SCOPE_RADIO, group=GROUP_CONTROL, state="mute",
               command="mute:{value}", icon="mdi:volume-off"),
    TciElement(key="running_switch", name="Radio running", platform="switch",
               scope=SCOPE_RADIO, group=GROUP_CONTROL, state="running",
               on_command="start", off_command="stop", icon="mdi:power"),
    TciElement(key="monitor_switch", name="TX monitor", platform="switch",
               scope=SCOPE_RADIO, group=GROUP_CONTROL, state="mon_enable",
               command="mon_enable:{value}", icon="mdi:headphones"),
    TciElement(key="rx_mute", name="RX mute", platform="switch", scope=SCOPE_TRX,
               group=GROUP_CONTROL, state="rx_mute",
               command="rx_mute:{trx},{value}", icon="mdi:volume-off"),
    TciElement(key="split_switch", name="Split", platform="switch",
               scope=SCOPE_TRX, group=GROUP_CONTROL, state="split_enable",
               command="split_enable:{trx},{value}", icon="mdi:call-split"),
    TciElement(key="rit_switch", name="RIT", platform="switch", scope=SCOPE_TRX,
               group=GROUP_CONTROL, state="rit_enable",
               command="rit_enable:{trx},{value}"),
    TciElement(key="rit_offset_set", name="RIT offset", platform="number",
               scope=SCOPE_TRX, group=GROUP_CONTROL, state="rit_offset",
               command="rit_offset:{trx},{value}", unit="Hz",
               native_min=-9999, native_max=9999, step=1),
    TciElement(key="xit_switch", name="XIT", platform="switch", scope=SCOPE_TRX,
               group=GROUP_CONTROL, state="xit_enable",
               command="xit_enable:{trx},{value}"),
    TciElement(key="sql_switch", name="Squelch", platform="switch",
               scope=SCOPE_TRX, group=GROUP_CONTROL, state="sql_enable",
               command="sql_enable:{trx},{value}"),
    TciElement(key="sql_level", name="Squelch level", platform="number",
               scope=SCOPE_TRX, group=GROUP_CONTROL, state="sql_level",
               command="sql_level:{trx},{value}", unit="dB", native_min=-140,
               native_max=0, step=1),
    TciElement(key="nb_switch", name="Noise blanker", platform="switch",
               scope=SCOPE_TRX, group=GROUP_CONTROL, state="rx_nb_enable",
               command="rx_nb_enable:{trx},{value}"),
    TciElement(key="nr_switch", name="Noise reduction", platform="switch",
               scope=SCOPE_TRX, group=GROUP_CONTROL, state="rx_nr_enable",
               command="rx_nr_enable:{trx},{value}"),
    TciElement(key="anf_switch", name="Auto notch", platform="switch",
               scope=SCOPE_TRX, group=GROUP_CONTROL, state="rx_anf_enable",
               command="rx_anf_enable:{trx},{value}"),
    TciElement(key="channel_switch", name="VFO {vfo} receiver", platform="switch",
               scope=SCOPE_CHANNEL, group=GROUP_CONTROL, state="rx_channel_enable",
               command="rx_channel_enable:{trx},{chan},{value}", min_channel=1),
    # --------------------------------------------------------------- TX
    TciElement(key="mox", name="MOX (transmit)", platform="switch",
               scope=SCOPE_TRX, group=GROUP_TX, state="trx",
               command="trx:{trx},{value}", tx_guard=True,
               icon="mdi:radio-tower"),
    TciElement(key="tune_switch", name="Tune", platform="switch", scope=SCOPE_TRX,
               group=GROUP_TX, state="tune", command="tune:{trx},{value}",
               tx_guard=True, icon="mdi:tune-vertical"),
    TciElement(key="drive_set", name="Drive", platform="number", scope=SCOPE_TRX,
               group=GROUP_TX, state="drive", command="drive:{trx},{value}",
               unit="%", native_min=0, native_max=100, step=1, icon="mdi:knob"),
    TciElement(key="tune_drive_set", name="Tune drive", platform="number",
               scope=SCOPE_TRX, group=GROUP_TX, state="tune_drive",
               command="tune_drive:{trx},{value}", unit="%", native_min=0,
               native_max=100, step=1, icon="mdi:knob"),
)

ELEMENTS_BY_KEY = {e.key: e for e in ELEMENTS}


def elements_in_group(group: str) -> list[TciElement]:
    return [e for e in ELEMENTS if e.group == group]


def default_keys(group: str) -> list[str]:
    return [e.key for e in ELEMENTS if e.group == group and e.default]
