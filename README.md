# TCI Radio for Home Assistant

A Home Assistant integration that connects to **Zeus SDR**, **ExpertSDR** or any other software with a TCI (Transceiver Control Interface) server, so you can monitor and control the radio from Home Assistant.

- **Push-based.** It keeps one WebSocket connection open and reconnects automatically. Nothing is polled.
- **Discovery-driven setup.** It reads the server's init burst for receiver count, VFO limits and the actual mode list.
- **You pick what you want.** Choose which TCI elements become entities, per receiver, and change your choices any time.
- **Safe by default.** Transmitter control is off unless you enable it, and a watchdog unkeys transmissions that Home Assistant started.

## Installation (HACS)

1. In HACS, open **⋮ → Custom repositories**. Add `https://github.com/RIGeek/TCI-Home-Assistant` with the category **Integration**.
2. Install **TCI Radio** and restart Home Assistant.
3. Go to **Settings → Devices & services → Add integration → TCI Radio**.

For a manual install, copy `custom_components/tci` into your `config/custom_components/` folder.

## Setup

1. Enable the TCI server in your radio software. The default port is 40001.
2. Enter the host and port. The integration connects and reports the device, protocol version, receivers and modes it found.
3. Choose what to expose:

| Section | Elements |
|---|---|
| **Monitor** | VFO frequency, S-meter (dBm + `s_units` attribute), mode, center frequency, filter width, transmitting, tuning, TX power / peak, SWR, mic level, drive, split, RIT/XIT offset, AGC, running, volume, muted |
| **Control** | Mode select, VFO tune (frequency box), volume, mute, start/stop, TX monitor, RX mute, split, RIT/XIT, squelch + level, NB, NR, auto notch, VFO B receiver on/off |
| **TX control** | MOX, Tune, Drive, Tune drive. These only appear when *Allow Home Assistant to key the transmitter* is on. |

Each receiver becomes its own device (for example, *Zeus RX1* and *Zeus RX2*). Per-VFO elements appear as *VFO A* and *VFO B*.

To change the selection later, use **Configure** on the integration. To change the host or port, use **Reconfigure**, which probes the server again.

### Meter rates

S-meter and TX meters can arrive many times a second. Two settings control them:

- **Meter report interval** sets how often the radio sends the readings (`rx_sensors_enable` / `tx_sensors_enable`).
- **Meter update throttle** sets the minimum time between Home Assistant state writes. The latest value is always written once the interval passes, so the recorder isn't flooded.

When the radio returns to RX, TX power drops to 0 and the last SWR reading is kept.

## Transmitter safety

TX entities key a real transmitter. Keep these points in mind:

- TX entities are disabled by default and need an explicit opt-in.
- **TX watchdog:** any MOX or Tune started *from Home Assistant* is unkeyed after the timeout. The default is 180 s; 0 disables it. Transmissions started from the radio software, such as WSJT-X over TCI, are not affected.
- Unloading or reloading the integration unkeys anything Home Assistant keyed.
- The `send_command` service refuses `trx` and `tune` unless TX control is enabled.

You remain the control operator for anything an automation does.

## Service: `tci.send_command`

This service sends raw TCI commands for anything that has no entity:

```yaml
action: tci.send_command
data:
  command: "vfo:0,0,14074000;modulation:0,digu;"
```

`config_entry_id` is only needed when more than one radio is configured.

## Checking what your server supports

TCI servers differ in which commands they implement. Download the diagnostics from **Settings → Devices & services → TCI Radio → ⋮ → Download diagnostics**. The file includes:

- `init_burst`: everything the server sent before `ready;`
- `state`: the current parsed state
- `unhandled_commands`: commands the server sent that this integration doesn't map yet

If a server never sends `ready;`, the integration continues after 3 s and records `ready_via_fallback: true`.

For more detail, enable debug logging. It logs every command in both directions (`TCI <<` / `TCI >>`):

```yaml
logger:
  logs:
    custom_components.tci: debug
```

## Development

```bash
python -m venv .venv && .venv/bin/pip install -r requirements_test.txt
.venv/bin/pytest
```

The tests run the integration inside Home Assistant against a fake TCI server. They cover the config and options flows, entities, commands, the TX watchdog, throttling and reconnects. `tci_client.py` has no Home Assistant dependencies.
