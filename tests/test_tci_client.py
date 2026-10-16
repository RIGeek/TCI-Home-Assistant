"""Tests for the standalone TCI client against a fake TCI server."""

from __future__ import annotations

import asyncio
import importlib.util
from pathlib import Path

import aiohttp
from aiohttp import web
import pytest

# Load tci_client.py directly so these tests don't need Home Assistant.
_spec = importlib.util.spec_from_file_location(
    "tci_client",
    Path(__file__).parents[1] / "custom_components" / "tci" / "tci_client.py",
)
tci = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(tci)


@pytest.fixture(autouse=True)
def _allow_sockets(request):
    # Under the Home Assistant test harness sockets are blocked by default.
    try:
        request.getfixturevalue("socket_enabled")
    except pytest.FixtureLookupError:
        pass

INIT_BURST = (
    "protocol:ExpertSDR3,1.9;device:SunSDR2PRO;receive_only:false;"
    "trx_count:2;channels_count:2;vfo_limits:10000,30000000;"
    "if_limits:-48000,48000;modulations_list:AM,SAM,DSB,LSB,USB,CW,NFM,DIGL,DIGU,WFM,DRM;"
)
STATE = (
    "dds:0,14070000;vfo:0,0,14074000;vfo:0,1,14080000;modulation:0,DIGU;"
    "trx:0,false;tune:0,false;drive:0,50;volume:-12;mute:false;start;"
    "rx_filter_band:0,200,3000;"
)


class FakeTci:
    """Minimal TCI server: sends init burst, records commands."""

    def __init__(self, send_ready: bool = True) -> None:
        self.received: list[str] = []
        self.clients: list[web.WebSocketResponse] = []
        self.send_ready = send_ready

    async def handler(self, request: web.Request) -> web.WebSocketResponse:
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        self.clients.append(ws)
        # Split across frames, several commands per frame, like real servers.
        await ws.send_str(INIT_BURST)
        await ws.send_str(STATE + ("ready;" if self.send_ready else ""))
        async for msg in ws:
            if msg.type == aiohttp.WSMsgType.TEXT:
                self.received.append(msg.data)
        return ws

    async def push(self, text: str) -> None:
        for ws in self.clients:
            if not ws.closed:
                await ws.send_str(text)


async def _start(fake: FakeTci) -> tuple[web.AppRunner, int]:
    app = web.Application()
    app.router.add_get("/", fake.handler)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    return runner, port


async def _wait_for(pred, timeout=3.0):
    loop = asyncio.get_running_loop()
    end = loop.time() + timeout
    while not pred():
        if loop.time() > end:
            raise AssertionError("condition not met")
        await asyncio.sleep(0.01)


@pytest.mark.asyncio
async def test_probe_reads_init_burst():
    fake = FakeTci()
    runner, port = await _start(fake)
    async with aiohttp.ClientSession() as session:
        info = await tci.probe(session, "127.0.0.1", port)
    await runner.cleanup()
    assert info["device"] == "SunSDR2PRO"
    assert info["protocol"] == "ExpertSDR3 1.9"
    assert info["trx_count"] == 2
    assert info["channels_count"] == 2
    assert "DIGU" in info["modulations"]
    assert info["vfo_limits"] == [10000, 30000000]
    assert info["receive_only"] is False
    assert info["ready_via_fallback"] is False
    assert info["init_log"][0] == "protocol:ExpertSDR3,1.9"


@pytest.mark.asyncio
async def test_probe_cannot_connect():
    async with aiohttp.ClientSession() as session:
        with pytest.raises(tci.TciCannotConnect):
            await tci.probe(session, "127.0.0.1", 1, timeout=3)


@pytest.mark.asyncio
async def test_probe_without_ready_uses_fallback():
    fake = FakeTci(send_ready=False)
    runner, port = await _start(fake)
    async with aiohttp.ClientSession() as session:
        info = await tci.probe(session, "127.0.0.1", port, ready_fallback=0.3)
    await runner.cleanup()
    assert info["ready_via_fallback"] is True
    assert info["trx_count"] == 2


@pytest.mark.asyncio
async def test_state_updates_listeners_and_send():
    fake = FakeTci()
    runner, port = await _start(fake)
    async with aiohttp.ClientSession() as session:
        client = tci.TciClient(session, "127.0.0.1", port, sensor_interval_ms=250)
        hits: list[str] = []
        client.add_listener("vfo:0:0", lambda: hits.append("vfo"))
        client.add_listener(tci.CONNECTION_KEY, lambda: hits.append("conn"))
        task = asyncio.create_task(client.run())
        await _wait_for(lambda: client.connected)

        assert client.get("vfo:0:0") == 14074000
        assert client.get("vfo:0:1") == 14080000
        assert client.get("modulation:0") == "digu"
        assert client.get("drive:0") == 50
        assert client.get("volume") == -12
        assert client.get("running") is True
        assert client.get("rx_filter_band:0") == (200, 3000)
        assert "vfo" in hits and "conn" in hits

        # Sensor streams were enabled after ready.
        await _wait_for(lambda: any("rx_sensors_enable:true,250" in r for r in fake.received))

        # Live updates, including several commands in one frame.
        await fake.push("vfo:0,0,7074000;rx_channel_sensors:0,0,-73.5;")
        await _wait_for(lambda: client.get("vfo:0:0") == 7074000)
        assert client.get("smeter:0:0") == -73.5

        # TX sensors, then unkey zeroes power but keeps last SWR.
        await fake.push("trx:0,true;tx_sensors:0,-20,95.5,100,1.3;")
        await _wait_for(lambda: client.get("tx_power:0") == 95.5)
        assert client.get("tx_swr:0") == 1.3
        await fake.push("trx:0,false;")
        await _wait_for(lambda: client.get("tx_power:0") == 0)
        assert client.get("tx_swr:0") == 1.3

        # Unknown commands are kept as raw state, not dropped.
        await fake.push("some_new_cmd:1,2;")
        await _wait_for(lambda: client.get("raw:some_new_cmd") == "1,2")

        # Outbound command gets its terminator.
        await client.send("modulation:0,usb")
        await _wait_for(lambda: "modulation:0,usb;" in fake.received)

        await client.close()
        await asyncio.wait_for(task, 5)
        assert client.connected is False
    await runner.cleanup()


@pytest.mark.asyncio
async def test_reconnects_after_server_drop():
    fake = FakeTci()
    runner, port = await _start(fake)
    async with aiohttp.ClientSession() as session:
        client = tci.TciClient(session, "127.0.0.1", port)
        task = asyncio.create_task(client.run())
        await _wait_for(lambda: client.connected)
        await fake.clients[0].close()
        await _wait_for(lambda: not client.connected)
        await _wait_for(lambda: client.connected, timeout=5)
        assert len(fake.clients) == 2
        await client.close()
        await asyncio.wait_for(task, 5)
    await runner.cleanup()


@pytest.mark.asyncio
async def test_send_when_disconnected_raises():
    async with aiohttp.ClientSession() as session:
        client = tci.TciClient(session, "127.0.0.1", 1)
        with pytest.raises(tci.TciNotConnected):
            await client.send("trx:0,true")


def test_old_protocol_single_arg_drive():
    client = tci.TciClient(None, "x")  # type: ignore[arg-type]
    client.feed("drive:75;tx_power:10;")
    assert client.get("drive:0") == 75
    assert client.get("tx_power:0") == 10
