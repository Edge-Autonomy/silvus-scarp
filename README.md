# Silvus Radio Telemetry Logger

Polls Silvus radios and IPCOMM devices every 60s. Live panel + logs.

## Setup

```
pip install requests beautifulsoup4 pyserial
cp config.example.py config.py
```

Edit `config.py` — radio IP, credentials, IPCOMM URL. It is gitignored: write
it once, it survives pulls.

## Run

```
python temp_test.py
```

Live panel on a TTY, one plain line per poll when piped. Ctrl-C quits.
Colours: temperature against the overheat threshold, SNR red <15 dB,
yellow <25 dB.

## Thermal cutout

At or above the trip temperature the script forces the radio idle —
`tx_fifo_disable=1`, so it will not initialise any transmission — and restores
transmit once it cools to the resume temperature. Both transitions are logged
to the debug log and marked in the text log and the CSV `tx_idle` column.

| Setting | Default |
| --- | --- |
| `THERMAL_IDLE` | `True` |
| `IDLE_TEMP_C` | the radio's own overheat threshold |
| `IDLE_RESUME_C` | trip − 5 °C |

Set them in `config.py`. `THERMAL_IDLE = False` turns the cutout off and leaves
the script read-only. On startup it reads the radio's current `tx_fifo_disable`,
so a restart mid-cutout does not leave transmit disabled forever.

## Thermocouple (DATAQ DI-245)

Optional. One thermocouple channel read once per poll, alongside everything
else — panel row, log line, CSV `tc` column. It does not drive the thermal
cutout; that still runs off the radio's own internal temperature.

Set in `config.py`:

| Setting | Default | Meaning |
| --- | --- | --- |
| `DATAQ_PORT` | `''` | serial port; empty disables the DI-245 |
| `DATAQ_CHANNEL` | `0` | DI-245 analog channel, 0-3 |
| `DATAQ_TC_TYPE` | `'K'` | `B E J K N R S T` |
| `DATAQ_OFFSET_C` | `0.0` | calibration trim, added to every reading |

The DI-245 is an FTDI virtual COM port, but `ftdi_sio` does not claim it by
default — its VID/PID pair is not in the driver's table. Once per boot:

```
sudo modprobe ftdi_sio
echo "0683 2450" | sudo tee /sys/bus/usb-serial/drivers/ftdi_sio/new_id
```

Replug the DI-245 and it appears as `/dev/ttyUSB0`. Add yourself to the
`dialout` group (or whatever owns the node) or you get a permission error.

Check the probe without waiting for a poll:

```
python temp_test.py --dataq
```

`TC Open` means a broken or disconnected thermocouple, `CJC Error` means the
DI-245 cannot read its own cold-junction sensor. Both come straight from the
device. Anything else — `No Data`, `Fetch Error` — is in the debug log.

Verified against a DI-245 (firmware 0x7A): the command set, framing and
decode are confirmed, using a voltage-mode channel as the live signal. The
thermocouple path itself is still unconfirmed — no probe was connected at
the time, and all four channels correctly reported `TC Open`.

## Output

Three files next to the script:

| File | Default | Override |
| --- | --- | --- |
| Text log | `temp_test.txt` | `SILVUS_OUTPUT_FILE` |
| CSV | `temp_test.csv` | `SILVUS_CSV_FILE` |
| Debug log | `temp_test.txt.debug` | `SILVUS_DEBUG_LOG` |

CSV appends raw values, empty cells for missing readings. Links share one
`links` column, `src>dst:snr` joined by `;`. `tc` is the DI-245 thermocouple
reading. `tx_idle` is 1 while the thermal cutout holds transmit off.

## When a reading is N/A

`tail -f temp_test.txt.debug` in a second window — the failure is in there.

Common causes: wrong IP or credentials in `config.py`; `SILVUS_FIPS_MODE`
not matching the radio (True forces HTTPS + login, which FIPS radios require).

Check the script itself is sane before blaming the network:

```
python temp_test.py --selftest
```
