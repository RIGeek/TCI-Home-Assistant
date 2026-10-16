"""End-to-end tests of the integration inside Home Assistant."""

from __future__ import annotations

import asyncio
from datetime import timedelta

import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from homeassistant import config_entries
from homeassistant.const import CONF_HOST, CONF_PORT, STATE_OFF, STATE_ON
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util

from custom_components.tci.const import DOMAIN
from custom_components.tci.diagnostics import async_get_config_entry_diagnostics

DISCOVERY = {
    "device": "Zeus", "protocol": "ExpertSDR3 1.9", "trx_count": 2,
    "channels_count": 2, "modulations": ["AM", "LSB", "USB", "CW", "NFM", "DIGL", "DIGU"],
    "vfo_limits": [10000, 60000000], "receive_only": False, "ready_via_fallback": False,
}


async def _wait(hass, pred, timeout=3.0):
    for _ in range(int(timeout / 0.02)):
        await hass.async_block_till_done()
        if pred():
            return
        await asyncio.sleep(0.02)
    raise AssertionError("condition not met")


def _entity_id(hass, platform, unique_suffix, entry):
    reg = er.async_get(hass)
    eid = reg.async_get_entity_id(platform, DOMAIN, f"{entry.entry_id}_{unique_suffix}")
    assert eid, f"{platform} {unique_suffix} not registered"
    return eid


async def _setup(hass, fake, **options):
    entry = MockConfigEntry(
        domain=DOMAIN, title="Zeus", unique_id=f"127.0.0.1:{fake.port}",
        data={CONF_HOST: "127.0.0.1", CONF_PORT: fake.port, "discovery": DISCOVERY},
        options={"receivers": ["0", "1"], "monitor": ["vfo", "smeter", "mode", "tx_active", "tx_power", "swr", "running"],
                 "control": ["mode_select", "vfo_set", "volume_set", "channel_switch"],
                 "tx_enabled": False, "tx_elements": [], "tx_timeout": 180,
                 "sensor_interval_ms": 500, "update_interval": 0.0, **options},
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    client = entry.runtime_data.client
    await _wait(hass, lambda: client.connected)
    return entry


async def test_config_flow_discovers_and_creates_entry(hass: HomeAssistant, fake_tci):
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    assert result["type"] is FlowResultType.FORM and result["step_id"] == "user"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_HOST: "127.0.0.1", CONF_PORT: fake_tci.port}
    )
    assert result["type"] is FlowResultType.FORM and result["step_id"] == "elements"
    assert result["description_placeholders"]["device"] == "Zeus"
    assert result["description_placeholders"]["trx_count"] == "2"

    # TX enabled with nothing picked is rejected.
    bad = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"receivers": ["0"], "monitor": ["vfo"], "control": [],
                            "tx_enabled": True, "tx_elements": [], "tx_timeout": 60,
                            "sensor_interval_ms": 500, "update_interval": 1}
    )
    assert bad["errors"] == {"tx_elements": "no_tx_elements"}

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"receivers": ["0"], "monitor": ["vfo", "mode"], "control": ["mode_select"],
                            "tx_enabled": False, "tx_elements": [], "tx_timeout": 60,
                            "sensor_interval_ms": 500, "update_interval": 1}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Zeus"
    assert result["data"]["discovery"]["modulations"][-1] == "DIGU"
    assert result["options"]["receivers"] == ["0"]
    await hass.async_block_till_done()

    entry = hass.config_entries.async_entries(DOMAIN)[0]
    await _wait(hass, lambda: entry.runtime_data.client.connected)
    # Only RX1 was chosen: VFO A/B sensors exist for rx0 but not rx1.
    reg = er.async_get(hass)
    assert reg.async_get_entity_id("sensor", DOMAIN, f"{entry.entry_id}_rx0_0_vfo")
    assert reg.async_get_entity_id("sensor", DOMAIN, f"{entry.entry_id}_rx0_1_vfo")
    assert not reg.async_get_entity_id("sensor", DOMAIN, f"{entry.entry_id}_rx1_0_vfo")


async def test_config_flow_cannot_connect(hass: HomeAssistant, socket_enabled):
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_HOST: "127.0.0.1", CONF_PORT: 1}
    )
    assert result["errors"] == {"base": "cannot_connect"}


async def test_entities_monitor_and_control(hass: HomeAssistant, fake_tci):
    entry = await _setup(hass, fake_tci)

    vfo_a = _entity_id(hass, "sensor", "rx0_0_vfo", entry)
    assert hass.states.get(vfo_a).state == "14074000"
    assert hass.states.get(vfo_a).attributes["unit_of_measurement"] == "Hz"
    smeter = _entity_id(hass, "sensor", "rx0_0_smeter", entry)
    assert hass.states.get(smeter).attributes["s_units"] == "S8"
    mode = _entity_id(hass, "sensor", "rx0_mode", entry)
    assert hass.states.get(mode).state == "DIGU"
    assert hass.states.get(_entity_id(hass, "sensor", "rx1_mode", entry)).state == "LSB"
    assert hass.states.get(_entity_id(hass, "binary_sensor", "radio_running", entry)).state == STATE_ON

    # Push update from radio.
    await fake_tci.push("vfo:0,0,7074000;")
    await _wait(hass, lambda: hass.states.get(vfo_a).state == "7074000")

    # Mode select -> TCI command -> echoed back -> state.
    select = _entity_id(hass, "select", "rx0_mode_select", entry)
    assert "USB" in hass.states.get(select).attributes["options"]
    await hass.services.async_call("select", "select_option", {"entity_id": select, "option": "USB"}, blocking=True)
    await _wait(hass, lambda: hass.states.get(select).state == "USB")
    assert "modulation:0,usb" in fake_tci.received

    # Frequency number (box, limits from discovery).
    num = _entity_id(hass, "number", "rx0_0_vfo_set", entry)
    attrs = hass.states.get(num).attributes
    assert attrs["min"] == 10000 and attrs["max"] == 60000000
    await hass.services.async_call("number", "set_value", {"entity_id": num, "value": 10136000}, blocking=True)
    await _wait(hass, lambda: hass.states.get(vfo_a).state == "10136000")
    assert "vfo:0,0,10136000" in fake_tci.received

    # VFO B receiver switch exists only for channel 1.
    _entity_id(hass, "switch", "rx0_1_channel_switch", entry)
    assert not er.async_get(hass).async_get_entity_id("switch", DOMAIN, f"{entry.entry_id}_rx0_0_channel_switch")

    # TX entities are not created while TX control is off; raw TX is refused.
    assert not er.async_get(hass).async_get_entity_id("switch", DOMAIN, f"{entry.entry_id}_rx0_mox")
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(DOMAIN, "send_command", {"command": "trx:0,true;"}, blocking=True)
    await hass.services.async_call(DOMAIN, "send_command", {"command": "volume:-20"}, blocking=True)
    await _wait(hass, lambda: "volume:-20" in fake_tci.received)

    # Unavailable on disconnect, back after reconnect.
    await fake_tci.clients[0].close()
    await _wait(hass, lambda: hass.states.get(vfo_a).state == "unavailable")
    await _wait(hass, lambda: hass.states.get(vfo_a).state != "unavailable", timeout=5)

    diag = await async_get_config_entry_diagnostics(hass, entry)
    assert diag["init_burst"][0] == "protocol:ExpertSDR3,1.9"
    assert diag["connected"] is True


async def test_tx_watchdog_unkeys(hass: HomeAssistant, fake_tci):
    entry = await _setup(hass, fake_tci, tx_enabled=True, tx_elements=["mox", "drive_set"], tx_timeout=10)
    mox = _entity_id(hass, "switch", "rx0_mox", entry)
    tx = _entity_id(hass, "binary_sensor", "rx0_tx_active", entry)
    power = _entity_id(hass, "sensor", "rx0_tx_power", entry)

    await hass.services.async_call("switch", "turn_on", {"entity_id": mox}, blocking=True)
    await _wait(hass, lambda: hass.states.get(tx).state == STATE_ON)
    assert hass.states.get(mox).attributes["watchdog_armed"] is True

    await fake_tci.push("tx_sensors:0,-20,98.5,100,1.25;")
    await _wait(hass, lambda: hass.states.get(power).state == "98.5")

    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=11))
    await _wait(hass, lambda: hass.states.get(tx).state == STATE_OFF)
    assert fake_tci.received.count("trx:0,false") == 1
    # Power zeroed on unkey, SWR kept.
    await _wait(hass, lambda: hass.states.get(power).state == "0")
    assert hass.states.get(_entity_id(hass, "sensor", "rx0_swr", entry)).state == "1.25"


async def test_unload_unkeys_ha_transmission(hass: HomeAssistant, fake_tci):
    entry = await _setup(hass, fake_tci, tx_enabled=True, tx_elements=["mox"], tx_timeout=600)
    mox = _entity_id(hass, "switch", "rx0_mox", entry)
    await hass.services.async_call("switch", "turn_on", {"entity_id": mox}, blocking=True)
    await _wait(hass, lambda: "trx:0,true" in fake_tci.received)
    assert await hass.config_entries.async_unload(entry.entry_id)
    await _wait(hass, lambda: "trx:0,false" in fake_tci.received)


async def test_fast_sensor_throttle(hass: HomeAssistant, fake_tci):
    entry = await _setup(hass, fake_tci, update_interval=5.0)
    smeter = _entity_id(hass, "sensor", "rx0_0_smeter", entry)
    await fake_tci.push("rx_channel_sensors:0,0,-60;")
    await asyncio.sleep(0.1)
    # Either written at once or deferred; after the interval it must show.
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=6))
    await _wait(hass, lambda: hass.states.get(smeter).state == "-60")
    # A burst right after a write is held back...
    await fake_tci.push("rx_channel_sensors:0,0,-50;rx_channel_sensors:0,0,-40;")
    await asyncio.sleep(0.2)
    await hass.async_block_till_done()
    assert hass.states.get(smeter).state == "-60"
    # ...and the latest value lands once the interval elapses.
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=12))
    await _wait(hass, lambda: hass.states.get(smeter).state == "-40")


async def test_options_flow_reloads(hass: HomeAssistant, fake_tci):
    entry = await _setup(hass, fake_tci)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["step_id"] == "init"
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"receivers": ["1"], "monitor": ["vfo"], "control": [],
                            "tx_enabled": False, "tx_elements": [], "tx_timeout": 60,
                            "sensor_interval_ms": 1000, "update_interval": 2}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    await _wait(hass, lambda: entry.runtime_data.client.connected)
    assert entry.options["sensor_interval_ms"] == 1000
    rx1 = _entity_id(hass, "sensor", "rx1_0_vfo", entry)
    assert hass.states.get(rx1).state == "7074000"
