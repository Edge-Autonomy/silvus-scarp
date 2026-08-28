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

## Output

Three files next to the script:

| File | Default | Override |
| --- | --- | --- |
| Text log | `temp_test.txt` | `SILVUS_OUTPUT_FILE` |
| CSV | `temp_test.csv` | `SILVUS_CSV_FILE` |
| Debug log | `temp_test.txt.debug` | `SILVUS_DEBUG_LOG` |

CSV appends raw values, empty cells for missing readings. Links share one
`links` column, `src>dst:snr` joined by `;`.

## When a reading is N/A

`tail -f temp_test.txt.debug` in a second window — the failure is in there.

Common causes: wrong IP or credentials in `config.py`; `SILVUS_FIPS_MODE`
not matching the radio (True forces HTTPS + login, which FIPS radios require).

Check the script itself is sane before blaming the network:

```
python temp_test.py --selftest
```
