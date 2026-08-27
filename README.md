# Silvus Radio Telemetry Logger

Polls Silvus StreamCaster radios and IPCOMM devices for temperature, voltage, and power data. Logs to a file every 60 seconds.

## Data Collected

**Silvus (via StreamCaster JSON-RPC API):**
- Internal temperature (C)
- Input voltage (mV)
- TX power (dBm and mW)
- Battery percentage

**IPCOMM (via web scrape):**
- Board temperature (F)

## Setup

```
pip install requests beautifulsoup4
```

Edit the config at the top of `temp_test.py`:

```python
SILVUS_IP = '172.20.X.X'
SILVUS_FIPS_MODE = True
SILVUS_USER = 'admin'
SILVUS_PASS = 'your_password'
IPCOMM1_URL = '10.128.1.1'
```

## Run

```
python temp_test.py
```

Output goes to console and appends to the file defined by `OUTPUT_FILE`.

## FIPS Mode

Set `SILVUS_FIPS_MODE = True` (default). This forces HTTPS and authenticated API access, which FIPS requires. Self-signed TLS certs are accepted.
