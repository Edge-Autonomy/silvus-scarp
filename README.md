# Silvus Radio Telemetry Logger

Polls Silvus StreamCaster radios and IPCOMM devices for RF, temperature, voltage, and power data. Redraws a live terminal panel and appends a log line every 60 seconds.

## Data Collected

**Silvus (via StreamCaster JSON-RPC API):**
- Internal temperature (C) and the overheat threshold it is judged against (§3.18 — above it the radio throttles duty cycle to 50%, then 25%)
- Input voltage (mV)
- TX power (dBm and mW)
- Battery percentage
- Center frequency, bandwidth, MCS, max_speed (§3.2, 3.3, 3.14, 3.22)
- Noise level in dBm (§3.66)
- Uptime and 1-minute load average (§3.265)
- Active mesh links with per-link SNR (`network_status`, §3.32)

**IPCOMM (via web scrape):**
- Board temperature (F)

## Setup

```
pip install requests beautifulsoup4
cp config.example.py config.py
```

Put your IPs and credentials in `config.py`:

```python
SILVUS_IP = '172.20.X.X'
SILVUS_FIPS_MODE = True
SILVUS_USER = 'admin'
SILVUS_PASS = 'your_password'
IPCOMM1_URL = 'http://10.128.1.1'
```

`config.py` is gitignored, so it is written once after cloning and then survives
every pull without ever landing in a commit. Anything it defines overrides the
defaults at the top of `temp_test.py`; anything it omits keeps the default.

## Run

```
python temp_test.py
```

On a TTY this draws a live panel that updates in place, colour-coded: temperature against the overheat threshold, link SNR (red <15 dB, yellow <25 dB). Ctrl-C to quit.

Piped or redirected, it falls back to plain one-line-per-poll output with no ANSI.

Either way each poll appends a plain-text line to `OUTPUT_FILE`, which defaults to `temp_test.txt` next to the script. Override it with the `SILVUS_OUTPUT_FILE` env var.

The same poll also appends a row to `CSV_FILE` — same path with a `.csv` extension, override `SILVUS_CSV_FILE`. Raw values, no units or formatting; missing readings are empty cells. The header is written when the file is created, so an existing CSV keeps appending. Links vary per poll, so they share one `links` column as `src>dst:snr` joined by `;`.

Debug/error chatter goes to `OUTPUT_FILE + '.debug'` (override: `SILVUS_DEBUG_LOG`) so it does not fight the panel for the terminal. `tail -f` it in a second window when something reads N/A.

## Self-test

```
python temp_test.py --selftest
```

Checks the `network_status` and `uptime` parsers and asserts every panel line renders to exactly the requested width.

## FIPS Mode

Set `SILVUS_FIPS_MODE = True` (default). This forces HTTPS and authenticated API access, which FIPS requires. Self-signed TLS certs are accepted.
