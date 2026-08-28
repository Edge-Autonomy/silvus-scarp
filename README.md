# Silvus Radio Telemetry Logger

Polls Silvus radios and IPCOMM devices every 60s. Live panel + logs.

## Setup

```
pip install requests beautifulsoup4
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

## Output

Three files next to the script:

| File | Default | Override |
| --- | --- | --- |
| Text log | `temp_test.txt` | `SILVUS_OUTPUT_FILE` |
| CSV | `temp_test.csv` | `SILVUS_CSV_FILE` |
| Debug log | `temp_test.txt.debug` | `SILVUS_DEBUG_LOG` |

CSV appends raw values, empty cells for missing readings. Links share one
`links` column, `src>dst:snr` joined by `;`. `tx_idle` is 1 while the thermal
cutout holds transmit off.

## When a reading is N/A

`tail -f temp_test.txt.debug` in a second window — the failure is in there.

Common causes: wrong IP or credentials in `config.py`; `SILVUS_FIPS_MODE`
not matching the radio (True forces HTTPS + login, which FIPS radios require).

Check the script itself is sane before blaming the network:

```
python temp_test.py --selftest
```
