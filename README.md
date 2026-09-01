# Silvus Radio Telemetry Logger

Polls Silvus radios and IPCOMM devices every 60s, with optional DATAQ DI-245
thermocouples. Live panel, text log, CSV, and a thermal cutout that idles the
radio when anything gets too hot.

## Setup

```
pip install -r requirements.txt
cp config.example.py config.py
```

Or with uv:

```
uv venv && uv pip install -r requirements.txt
```

`pyserial` is only needed for the DI-245; without it that reading shows
`No pyserial` and the rest carries on.

Edit `config.py` — radio IP, credentials, IPCOMM URL. It is gitignored, so it
survives pulls.

## Run

```
python temp_test.py
```

Live panel on a TTY, one plain line per poll when piped. Ctrl-C quits.
Colours: temperature against the overheat threshold, SNR red <15 dB,
yellow <25 dB.

## Thermal cutout

Every temperature feeds it — the radio, IPCOMM1, IPCOMM2, and each
thermocouple. Any one at or above the trip forces the radio idle
(`tx_fifo_disable=1`); all of them must be back at or below the resume
temperature before transmit returns. A sensor that is absent or failed to read
is skipped, not treated as cool.

| Setting | Default |
| --- | --- |
| `THERMAL_IDLE` | `True` |
| `IDLE_TEMP_C` | the radio's own overheat threshold |
| `IDLE_RESUME_C` | trip − 5 °C |

`THERMAL_IDLE = False` turns the cutout off and leaves the script read-only.
On startup it reads the radio's current `tx_fifo_disable`, so a restart
mid-cutout does not leave transmit disabled forever.

## Thermocouples (DATAQ DI-245)

Optional. All configured channels are read once per poll.

| Setting | Default | Meaning |
| --- | --- | --- |
| `DATAQ_PORT` | `''` | serial port; empty disables the DI-245 |
| `DATAQ_CHANNELS` | `[0, 1]` | one per probe; 0-3, silkscreen says 1-4 |
| `DATAQ_TC_TYPE` | `'K'` | `B E J K N R S T`, same for every channel |
| `DATAQ_OFFSET_C` | `0.0` | calibration trim, added to every reading |

Channels are numbered 0-3 but the terminal block is silkscreened 1-4: probes in
blocks 1 and 2 are `DATAQ_CHANNELS = [0, 1]`, shown as `CH1`, `CH2`. One probe
only: `[0]`. Type and offset apply to every channel.

`ftdi_sio` does not claim the DI-245, so no serial port appears until the
VID/PID pair is registered. On Ubuntu, once per machine:

```
sudo ./setup-di245.sh
```

That gives you `/dev/dataq-di245` and adds you to `dialout` — log out and back
in, replug the DI-245, then set `DATAQ_PORT = '/dev/dataq-di245'`. By hand
instead (lasts until reboot, port is `/dev/ttyUSB0`):

```
sudo modprobe ftdi_sio
echo "0683 2450" | sudo tee /sys/bus/usb-serial/drivers/ftdi_sio/new_id
```

Check the probes without waiting for a poll:

```
python temp_test.py --dataq
```

`TC Open` is a broken or disconnected probe, `CJC Error` a cold-junction fault;
both come from the device. Anything else is in the debug log.

## Output

Three files next to the script:

| File | Default | Override |
| --- | --- | --- |
| Text log | `temp_test.txt` | `SILVUS_OUTPUT_FILE` |
| CSV | `temp_test.csv` | `SILVUS_CSV_FILE` |
| Debug log | `temp_test.txt.debug` | `SILVUS_DEBUG_LOG` |

CSV appends raw values, empty cells for missing readings. Links share one
`links` column, `src>dst:snr` joined by `;`. `tc1`-`tc4` are the DI-245 inputs,
empty for the ones you do not use. `tx_idle` is 1 while the cutout holds
transmit off.

## When a reading is N/A

`tail -f temp_test.txt.debug` in a second window — the failure is in there.

Common causes: wrong IP or credentials in `config.py`; `SILVUS_FIPS_MODE` not
matching the radio (True forces HTTPS + login, which FIPS radios require).

Check the script itself before blaming the network:

```
python temp_test.py --selftest
```
