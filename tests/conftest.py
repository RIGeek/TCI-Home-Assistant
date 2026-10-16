"""Shared fixtures: a fake TCI server that echoes setters like a real radio."""

from __future__ import annotations

import aiohttp
from aiohttp import web
import pytest

INIT_BURST = (
    "protocol:ExpertSDR3,1.9;device:Zeus;receive_only:false;"
    "trx_count:2;channels_count:2;vfo_limits:10000,60000000;"
    "if_limits:-48000,48000;modulations_list:AM,LSB,USB,CW,NFM,DIGL,DIGU;"
)
STATE = (
    "dds:0,14070000;vfo:0,0,14074000;vfo:0,1,14080000;modulation:0,digu;"
    "dds:1,7070000;vfo:1,0,7074000;vfo:1,1,7080000;modulation:1,lsb;"
    "trx:0,false;trx:1,false;tune:0,false;drive:0,50;volume:-12;mute:false;start;"
    "rx_channel_sensors:0,0,-79;ready;"
)
ECHO = {"modulation", "vfo", "trx", "tune", "drive", "volume", "mute",
        "split_enable", "rx_channel_enable"}


class FakeTci:
    def __init__(self) -> None:
        self.received: list[str] = []
        self.clients: list[web.WebSocketResponse] = []

    async def handler(self, request: web.Request) -> web.WebSocketResponse:
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        self.clients.append(ws)
        await ws.send_str(INIT_BURST)
        await ws.send_str(STATE)
        async for msg in ws:
            if msg.type != aiohttp.WSMsgType.TEXT:
                continue
            for cmd in filter(None, (c.strip() for c in msg.data.split(";"))):
                self.received.append(cmd)
                if cmd.split(":")[0] in ECHO:
                    await ws.send_str(cmd + ";")
        return ws

    async def push(self, text: str) -> None:
        for ws in self.clients:
            if not ws.closed:
                await ws.send_str(text)


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    yield


@pytest.fixture
async def fake_tci(socket_enabled):
    fake = FakeTci()
    app = web.Application()
    app.router.add_get("/", fake.handler)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    fake.port = site._server.sockets[0].getsockname()[1]
    yield fake
    for ws in fake.clients:
        await ws.close()
    await runner.cleanup()
