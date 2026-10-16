"""Async TCI (Transceiver Control Interface) client.

TCI is a WebSocket protocol carrying plain-text commands of the form
``name:arg1,arg2,...;``. Several commands may arrive in one frame. On connect
the server sends an init burst (protocol, device, limits, modes, current
state) followed by ``ready;``.

This module has no Home Assistant imports so it can be tested and reused
on its own.
"""

from __future__ import annotations

import asyncio
from collections import defaultdict, deque
from collections.abc import Callable
import contextlib
import logging
from typing import Any

import aiohttp

_LOGGER = logging.getLogger(__name__)

DEFAULT_PORT = 40001
CONNECTION_KEY = "__connection__"
READY_FALLBACK_SECONDS = 3.0
BACKOFF_MAX = 30.0

# Commands whose args are (trx, bool)
_TRX_BOOL = {
    "trx", "tune", "split_enable", "rit_enable", "xit_enable", "rx_mute",
    "sql_enable", "rx_nb_enable", "rx_nr_enable", "rx_anf_enable",
    "rx_apf_enable", "rx_bin_enable", "lock", "tx_enable", "rx_enable",
}
# Commands whose args are (trx, number). drive/tune_drive may arrive as a
# single arg on older protocol versions; that maps to trx 0.
_TRX_NUM = {
    "dds", "drive", "tune_drive", "rit_offset", "xit_offset", "sql_level",
    "agc_gain", "rx_volume", "rx_balance",
}
_TRX_STR = {"modulation", "agc_mode"}
# Commands whose args are (trx, channel, number)
_CHAN_NUM = {"vfo": "vfo", "if": "if", "rx_smeter": "smeter",
             "rx_channel_sensors": "smeter"}
_CHAN_BOOL = {"rx_channel_enable"}
_RADIO_BOOL = {"mute", "mon_enable", "receive_only"}
_RADIO_NUM = {"volume", "trx_count", "channels_count", "mon_volume"}
_RADIO_STR = {"device"}


class TciError(Exception):
    """Base TCI error."""


class TciNotConnected(TciError):
    """Raised when sending while not connected."""


class TciCannotConnect(TciError):
    """Raised when a probe cannot reach a TCI server."""


def make_key(name: str, trx: int | None = None, chan: int | None = None) -> str:
    """Build a state key such as ``vfo:0:1``."""
    if trx is None:
        return name
    if chan is None:
        return f"{name}:{trx}"
    return f"{name}:{trx}:{chan}"


def format_value(value: Any) -> str:
    """Format a value for a TCI command."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def _bool(raw: str) -> bool:
    return raw.strip().lower() in ("true", "1", "on")


def _num(raw: str) -> int | float | None:
    try:
        val = float(raw)
    except ValueError:
        return None
    return int(val) if val.is_integer() else val


def _int(raw: str, default: int = 0) -> int:
    try:
        return int(float(raw))
    except ValueError:
        return default


class TciClient:
    """Persistent TCI client with reconnect and per-key listeners."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        host: str,
        port: int = DEFAULT_PORT,
        *,
        sensor_interval_ms: int = 500,
        ready_fallback: float = READY_FALLBACK_SECONDS,
    ) -> None:
        self._session = session
        self.host = host
        self.port = port
        self.sensor_interval_ms = sensor_interval_ms
        self._ready_fallback = ready_fallback
        self.state: dict[str, Any] = {}
        self.init_log: deque[str] = deque(maxlen=1000)
        self.ready = asyncio.Event()
        self.connected = False
        self.ready_via_fallback = False
        self.last_error: str | None = None
        self._ws: aiohttp.ClientWebSocketResponse | None = None
        self._listeners: dict[str, list[Callable[[], None]]] = defaultdict(list)
        self._closing = False
        self._fallback_handle: asyncio.TimerHandle | None = None

    # ------------------------------------------------------------------ info
    @property
    def url(self) -> str:
        return f"ws://{self.host}:{self.port}"

    @property
    def trx_count(self) -> int:
        return int(self.state.get("trx_count") or 1)

    @property
    def channels_count(self) -> int:
        return int(self.state.get("channels_count") or 2)

    @property
    def modulations(self) -> list[str]:
        return list(self.state.get("modulations_list") or [])

    @property
    def vfo_limits(self) -> tuple[int, int] | None:
        return self.state.get("vfo_limits")

    @property
    def receive_only(self) -> bool:
        return bool(self.state.get("receive_only", False))

    def info(self) -> dict[str, Any]:
        """Discovery summary used by the config flow."""
        return {
            "device": self.state.get("device") or "TCI radio",
            "protocol": self.state.get("protocol"),
            "trx_count": self.trx_count,
            "channels_count": self.channels_count,
            "modulations": self.modulations,
            "vfo_limits": list(self.vfo_limits) if self.vfo_limits else None,
            "receive_only": self.receive_only,
            "ready_via_fallback": self.ready_via_fallback,
        }

    def get(self, key: str, default: Any = None) -> Any:
        return self.state.get(key, default)

    # ------------------------------------------------------------- listeners
    def add_listener(self, key: str, cb: Callable[[], None]) -> Callable[[], None]:
        """Call ``cb`` whenever ``key`` changes. Returns an unsubscribe fn."""
        self._listeners[key].append(cb)

        def _remove() -> None:
            with contextlib.suppress(ValueError):
                self._listeners[key].remove(cb)

        return _remove

    def _notify(self, keys: set[str]) -> None:
        for key in keys:
            for cb in list(self._listeners.get(key, ())):
                try:
                    cb()
                except Exception:  # noqa: BLE001
                    _LOGGER.exception("TCI listener for %s failed", key)

    def _set_connected(self, value: bool) -> None:
        if self.connected != value:
            self.connected = value
            self._notify({CONNECTION_KEY})

    # ------------------------------------------------------------ lifecycle
    async def run(self) -> None:
        """Connect and stay connected until :meth:`close` is called."""
        backoff = 1.0
        while not self._closing:
            try:
                await self._session_once()
                if self.ready.is_set():
                    backoff = 1.0
            except asyncio.CancelledError:
                raise
            except (aiohttp.ClientError, OSError, asyncio.TimeoutError) as err:
                self.last_error = str(err) or type(err).__name__
                _LOGGER.debug("TCI connection to %s failed: %s", self.url, err)
            finally:
                self._ws = None
                self._cancel_fallback()
                self._set_connected(False)
            if self._closing:
                break
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, BACKOFF_MAX)

    async def _session_once(self) -> None:
        self.ready.clear()
        self.ready_via_fallback = False
        self.init_log.clear()
        async with self._session.ws_connect(
            self.url, heartbeat=30, timeout=aiohttp.ClientWSTimeout(ws_close=5)
        ) as ws:
            self._ws = ws
            _LOGGER.info("Connected to TCI server at %s", self.url)
            loop = asyncio.get_running_loop()
            self._fallback_handle = loop.call_later(
                self._ready_fallback, self._on_ready, True
            )
            async for msg in ws:
                if msg.type == aiohttp.WSMsgType.TEXT:
                    self.feed(msg.data)
                elif msg.type == aiohttp.WSMsgType.BINARY:
                    continue  # audio/IQ streams are not used
                elif msg.type in (aiohttp.WSMsgType.ERROR, aiohttp.WSMsgType.CLOSE):
                    break
            _LOGGER.info("TCI server at %s closed the connection", self.url)

    async def close(self) -> None:
        self._closing = True
        self._cancel_fallback()
        if self._ws is not None and not self._ws.closed:
            await self._ws.close()

    def _cancel_fallback(self) -> None:
        if self._fallback_handle is not None:
            self._fallback_handle.cancel()
            self._fallback_handle = None

    def _on_ready(self, fallback: bool = False) -> None:
        self._cancel_fallback()
        if self.ready.is_set():
            return
        if fallback:
            self.ready_via_fallback = True
            _LOGGER.warning(
                "TCI server at %s sent no 'ready;' within %.0fs; continuing anyway",
                self.url, self._ready_fallback,
            )
        self.ready.set()
        self._set_connected(True)
        if self._ws is not None and self.sensor_interval_ms > 0:
            ms = int(self.sensor_interval_ms)
            asyncio.get_running_loop().create_task(
                self._send_quiet(
                    f"rx_sensors_enable:true,{ms};tx_sensors_enable:true,{ms};"
                )
            )

    async def _send_quiet(self, command: str) -> None:
        with contextlib.suppress(TciError, aiohttp.ClientError, ConnectionError):
            await self.send(command)

    async def send(self, command: str) -> None:
        """Send one or more raw commands (``;`` appended if missing)."""
        ws = self._ws
        if ws is None or ws.closed:
            raise TciNotConnected(f"Not connected to {self.url}")
        cmd = command.strip()
        if not cmd.endswith(";"):
            cmd += ";"
        _LOGGER.debug("TCI >> %s", cmd)
        await ws.send_str(cmd)

    # --------------------------------------------------------------- parsing
    def feed(self, text: str) -> None:
        """Parse a text frame that may hold several commands."""
        changed: set[str] = set()
        for part in text.split(";"):
            part = part.strip()
            if part:
                changed |= self._dispatch(part)
        if changed:
            self._notify(changed)

    def _dispatch(self, msg: str) -> set[str]:
        if not self.ready.is_set():
            self.init_log.append(msg)
        _LOGGER.debug("TCI << %s", msg)
        name, _, argstr = msg.partition(":")
        name = name.strip().lower()
        args = [a.strip() for a in argstr.split(",")] if argstr else []
        changed: set[str] = set()

        def put(key: str, value: Any) -> None:
            if self.state.get(key, object()) != value:
                self.state[key] = value
                changed.add(key)

        if name == "ready":
            self._on_ready()
        elif name == "start":
            put("running", True)
        elif name == "stop":
            put("running", False)
        elif name == "protocol":
            put("protocol", " ".join(args))
        elif name in ("vfo_limits", "if_limits") and len(args) >= 2:
            put(name, (_int(args[0]), _int(args[1])))
        elif name == "modulations_list":
            put(name, [a for a in args if a])
        elif name in _RADIO_BOOL and args:
            put(name, _bool(args[-1]))
        elif name in _RADIO_NUM and args:
            put(name, _num(args[-1]))
        elif name in _RADIO_STR and args:
            put(name, argstr.strip())
        elif name in _TRX_BOOL and len(args) >= 2:
            trx = _int(args[0])
            put(make_key(name, trx), _bool(args[1]))
            if name == "trx" and not _bool(args[1]):
                # Sensors stop arriving on RX; don't leave stale TX power.
                put(make_key("tx_power", trx), 0)
                put(make_key("tx_peak", trx), 0)
        elif name in _TRX_NUM and args:
            trx, raw = (_int(args[0]), args[1]) if len(args) >= 2 else (0, args[0])
            put(make_key(name, trx), _num(raw))
        elif name in _TRX_STR and len(args) >= 2:
            put(make_key(name, _int(args[0])), args[1].lower())
        elif name in _CHAN_NUM and len(args) >= 3:
            put(make_key(_CHAN_NUM[name], _int(args[0]), _int(args[1])), _num(args[2]))
        elif name in _CHAN_BOOL and len(args) >= 3:
            put(make_key(name, _int(args[0]), _int(args[1])), _bool(args[2]))
        elif name == "rx_filter_band" and len(args) >= 3:
            put(make_key(name, _int(args[0])), (_num(args[1]), _num(args[2])))
        elif name == "tx_sensors" and len(args) >= 5:
            trx = _int(args[0])
            put(make_key("tx_mic", trx), _num(args[1]))
            put(make_key("tx_power", trx), _num(args[2]))
            put(make_key("tx_peak", trx), _num(args[3]))
            put(make_key("tx_swr", trx), _num(args[4]))
        elif name in ("tx_power", "tx_swr") and args:
            # Older protocol: single value, applies to trx 0.
            put(make_key(name, 0), _num(args[-1]))
        else:
            put(f"raw:{name}", argstr)
        return changed


async def probe(
    session: aiohttp.ClientSession,
    host: str,
    port: int = DEFAULT_PORT,
    timeout: float = 10.0,
    ready_fallback: float = READY_FALLBACK_SECONDS,
) -> dict[str, Any]:
    """Connect once, read the init burst and return a discovery summary."""
    client = TciClient(
        session, host, port, sensor_interval_ms=0, ready_fallback=ready_fallback
    )
    task = asyncio.ensure_future(client._session_once())  # noqa: SLF001
    ready = asyncio.ensure_future(client.ready.wait())
    try:
        done, _ = await asyncio.wait(
            {task, ready}, timeout=timeout, return_when=asyncio.FIRST_COMPLETED
        )
        if ready not in done:
            if task in done and task.exception() is not None:
                raise TciCannotConnect(str(task.exception())) from task.exception()
            raise TciCannotConnect(f"No response from TCI server at {client.url}")
        info = client.info()
        info["init_log"] = list(client.init_log)
        return info
    finally:
        await client.close()
        ready.cancel()
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await task
