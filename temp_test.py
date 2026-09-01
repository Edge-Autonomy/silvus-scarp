import time
import datetime
import re
import shutil
import sys
import requests
import urllib3
import os
import csv
import logging
from bs4 import BeautifulSoup

# Self-signing cert to stop complaining
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# Defaults next to this script; override with SILVUS_OUTPUT_FILE / SILVUS_DEBUG_LOG.
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
OUTPUT_FILE = os.environ.get('SILVUS_OUTPUT_FILE',
                             os.path.join(_SCRIPT_DIR, 'logs.txt'))
# Machine-readable twin of OUTPUT_FILE; override with SILVUS_CSV_FILE.
CSV_FILE = os.environ.get('SILVUS_CSV_FILE',
                          os.path.splitext(OUTPUT_FILE)[0] + '.csv')
INTERVAL_SECONDS = 60

SILVUS_IP = 'XXX'
# IPCOMM boards only. The radio's own temperature comes from the API, not
# from scraping a board page.
IPCOMM1_URL = ''
IPCOMM2_URL = ''

# FIPS mode enforces: HTTPS only, login authentication required, SSH disabled.
# If your radio is in FIPS mode, set SILVUS_FIPS_MODE = True.
SILVUS_FIPS_MODE = True

# Login credentials (required when FIPS is on, or when login_auth_disable is 0)
SILVUS_USER = 'XXX'
SILVUS_PASS = 'XXX'

# Thermal cutout. Above IDLE_TEMP_C the radio is forced idle -- tx_fifo_disable=1
# (Section 3.33), which stops it initialising any transmission -- and released
# again once it cools back to IDLE_RESUME_C.
THERMAL_IDLE = True
# None: use the radio's own overheat threshold (temp_reporting_max_threshold).
IDLE_TEMP_C = None
# None: 5 C below the trip point. The gap is hysteresis; without it the radio
# flaps in and out of idle every poll while it sits on the threshold.
IDLE_RESUME_C = None

# DATAQ DI-245 thermocouple, read once per poll. Empty port disables it, the
# same way an empty IPCOMM URL does. Needs pyserial.
DATAQ_PORT = ''
# One entry per probe, read in the same scan. 0-3; the silkscreen says 1-4.
DATAQ_CHANNELS = [0, 1]
DATAQ_TC_TYPE = 'K'      # all channels; the DI-245 allows a type per channel,
                         # but every probe here is the same kind.
# Trim for probe and junction error; added to every reading.
DATAQ_OFFSET_C = 0.0

# Your IPs and credentials live in config.py, which git ignores. Copy
# config.example.py to config.py once after cloning and edit it there;
# anything it defines overrides the defaults above.
try:
    from config import *  # noqa: F401,F403
except ImportError:
    pass

# One list, so the poll, the log line, the CSV row and the panel all walk the
# same thing. An empty URL reads "Not Set" and the cutout skips it.
IPCOMM_URLS = [IPCOMM1_URL, IPCOMM2_URL]

# The live panel owns the terminal, so debug chatter has to go somewhere else.
# Logs land next to OUTPUT_FILE; tail it in a second terminal when debugging.
DEBUG_LOG_FILE = os.environ.get('SILVUS_DEBUG_LOG', OUTPUT_FILE + '.debug')

logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
    # delay=True: main() creates the directory, so don't open the file at import.
    handlers=[logging.FileHandler(DEBUG_LOG_FILE, delay=True)],
)
log = logging.getLogger(__name__)

# API endpoint: POST http://<IP>/streamscape_api

    # Supports both FIPS and non-FIPS radios:
    #   - FIPS mode: HTTPS only, login authentication mandatory (Section 13).
    #   - Non-FIPS: HTTP by default, auth optional.

    # Session cookies are maintained automatically
    # The cookie refreshes with every API call (10-minute expiry).
    # If a session expires, the client re-authenticates automatically.

class SilvusAPI:
    def __init__(self, ip, username=None, password=None, fips_mode=False):
        self.ip = ip
        self.username = username
        self.password = password
        self.fips_mode = fips_mode
        self._authenticated = False

        # FIPS enforces HTTPS (https_disable is forced to 0)
        if fips_mode:
            self.base_url = f"https://{ip}"
        else:
            self.base_url = f"http://{ip}"
        self.api_url = f"{self.base_url}/streamscape_api"

        # Persistent session for cookie management
        self.session = requests.Session()
        self.session.verify = False  # Silvus uses self-signed certs (even in FIPS)
        self.session.headers.update({'Content-Type': 'application/json'})

        # FIPS mandates login auth; non-FIPS only if credentials provided
        if fips_mode or (username and password):
            self._authenticate()

    def _authenticate(self):
        """Authenticate via /login.sh to obtain a session cookie.

        Per Section 9 of the API manual:
          curl -skL "http://<IP>/login.sh?username=<user>&password=<pass>&Submit=1" -c cookie.jar

        In FIPS mode, HTTPS is mandatory. The -k flag (verify=False) is needed
        because FIPS radios still typically use self-signed TLS certs.
        """
        if not self.username or not self.password:
            log.error("Silvus API: Credentials required but not provided")
            return False

        login_params = {
            'username': self.username,
            'password': self.password,
            'Submit': '1'
        }

        schemes = ['https'] if self.fips_mode else ['https', 'http']

        for scheme in schemes:
            login_url = f"{scheme}://{self.ip}/login.sh"
            try:
                log.debug(f"Silvus API: Authenticating via {scheme.upper()} to {self.ip}")
                resp = self.session.get(
                    login_url, params=login_params,
                    timeout=10, allow_redirects=True
                )
                if resp.ok:
                    self.base_url = f"{scheme}://{self.ip}"
                    self.api_url = f"{self.base_url}/streamscape_api"
                    self._authenticated = True
                    log.info(f"Silvus API: Authenticated via {scheme.upper()} to {self.ip}")
                    return True
                else:
                    log.warning(f"Silvus API: {scheme.upper()} login returned HTTP {resp.status_code}")
            except requests.exceptions.SSLError as e:
                log.error(f"Silvus API: SSL error on {scheme.upper()} login: {e}")
            except requests.exceptions.ConnectionError as e:
                log.debug(f"Silvus API: {scheme.upper()} connection failed, trying next: {e}")
                continue
            except Exception as e:
                log.error(f"Silvus API: {scheme.upper()} login error: {type(e).__name__}: {e}")

        log.error("Silvus API: Authentication failed on all schemes")
        self._authenticated = False
        return False

    def _call(self, method, params=None):
        """Execute a single JSON-RPC 2.0 API call.

        If the session cookie has expired (401/403), automatically re-authenticates
        and retries once. Per Section 9, every successful API response refreshes
        the cookie for another 10 minutes.

        Args:
            method: API command name (e.g. 'read_current_temperature')
            params: Optional list of string parameters

        Returns:
            The 'result' array from the response, or None on error.
        """
        payload = {
            "jsonrpc": "2.0",
            "method": method,
            "id": "1"
        }
        if params is not None:
            payload["params"] = params

        for attempt in range(2):
            try:
                resp = self.session.post(self.api_url, json=payload, timeout=10)

                # Session expired -- re-authenticate and retry once
                if resp.status_code in (401, 403) and attempt == 0:
                    log.info(f"Silvus API [{method}]: Got {resp.status_code}, re-authenticating...")
                    if self._authenticate():
                        continue
                    else:
                        log.error(f"Silvus API [{method}]: Re-authentication failed")
                        return None

                if resp.status_code != 200:
                    log.warning(f"Silvus API [{method}]: HTTP {resp.status_code}")
                    return None

                data = resp.json()

                if "error" in data and data["error"]:
                    log.warning(f"Silvus API [{method}]: Error: {data['error']}")
                    return None

                result = data.get("result")
                log.debug(f"Silvus API [{method}]: result={result}")
                return result

            except requests.exceptions.ConnectionError as e:
                log.error(f"Silvus API [{method}]: Cannot connect to {self.ip}: {e}")
                return None
            except requests.exceptions.Timeout:
                log.error(f"Silvus API [{method}]: Timeout")
                return None
            except ValueError as e:
                log.error(f"Silvus API [{method}]: Invalid JSON response: {e}")
                return None
            except Exception as e:
                log.error(f"Silvus API [{method}]: {type(e).__name__}: {e}")
                return None

        return None

    def _read(self, method, cast=str, index=0):
        """Call a read-only API and cast element `index` of the result array."""
        result = self._call(method)
        if not result or len(result) <= index:
            return None
        try:
            return cast(result[index])
        except (ValueError, TypeError):
            log.warning(f"Silvus API [{method}]: Could not parse {result[index]!r} as {cast.__name__}")
            return None

    # --- Convenience methods for telemetry data ---

    def get_temperature(self):
        """Read current temperature in degrees Celsius (Section 3.21)."""
        return self._read("read_current_temperature", int)

    def get_tx_disabled(self):
        """Read tx_fifo_disable (Section 3.33): 1 = radio cannot transmit."""
        return self._read("tx_fifo_disable", int)

    def set_tx_disabled(self, disable):
        """Set tx_fifo_disable (Section 3.33). Returns True if the call landed."""
        return self._call("tx_fifo_disable", [str(int(disable))]) is not None

    def get_temp_max_threshold(self):
        """Read the overheat threshold in C (Section 3.18).

        Above this the radio throttles its duty cycle to 50%, then 25%, so the
        raw temperature is not interpretable without it.
        """
        return self._read("temp_reporting_max_threshold", int)

    def get_input_voltage(self):
        """Read input voltage in millivolts (Section 3.119)."""
        return self._read("input_voltage_monitoring", float)

    def get_tx_power_dbm(self):
        """Read actual transmitted total output power in dBm (Section 3.58)."""
        return self._read("read_power_dBm", int)

    def get_tx_power_mw(self):
        """Read actual transmitted total output power in mW (Section 3.59)."""
        return self._read("read_power_mw", int)

    def get_battery_percent(self):
        """Read battery percentage (Section 3.122). Divide by 100 for GUI value."""
        return self._read("battery_percent", float)

    def get_node_id(self):
        """Read the radio's unique 20-bit node ID (Section 3.1)."""
        return self._read("nodeid")

    def get_freq_mhz(self):
        """Read current center frequency in MHz (Section 3.2)."""
        return self._read("freq", float)

    def get_bandwidth_mhz(self):
        """Read current channel bandwidth in MHz (Section 3.3)."""
        return self._read("bw", float)

    def get_mcs(self):
        """Read the MIMO modulation/coding index (Section 3.14). 255 = auto."""
        return self._read("mcs", int)

    def get_max_speed_mph(self):
        """Read the max_speed Doppler setting in mph (Section 3.22)."""
        return self._read("max_speed", int)

    def get_noise_level_dbm(self):
        """Read the current noise level in dBm (Section 3.66)."""
        return self._read("noise_level", int)

    def get_uptime(self):
        """Read the raw uptime string (Section 3.265)."""
        return self._read("uptime")

    def get_network_status(self):
        """Read active routing links (Section 3.32).

        The API returns a flat array of (src, dst, snr) trios; return it as a
        list of tuples instead.
        """
        return parse_network_status(self._call("network_status"))

    def get_all_telemetry(self):
        """Fetch all available telemetry data in one poll.

        Returns a dict with all readings (values may be None if unavailable).
        """
        return {
            "temperature_c": self.get_temperature(),
            "voltage_mv": self.get_input_voltage(),
            "tx_power_dbm": self.get_tx_power_dbm(),
            "tx_power_mw": self.get_tx_power_mw(),
            "battery_pct": self.get_battery_percent(),
            "freq_mhz": self.get_freq_mhz(),
            "bw_mhz": self.get_bandwidth_mhz(),
            "mcs": self.get_mcs(),
            "max_speed_mph": self.get_max_speed_mph(),
            "noise_dbm": self.get_noise_level_dbm(),
            "uptime": self.get_uptime(),
            "links": self.get_network_status(),
        }


def parse_network_status(result):
    """Flat ["src","dst","snr",...] array -> [(src, dst, snr_float), ...].

    A trailing partial trio is dropped; a trio with an unparseable SNR is kept
    with snr=None so the link still shows up on the panel.
    """
    links = []
    if not result:
        return links
    for i in range(0, len(result) - 2, 3):
        src, dst, snr = result[i], result[i + 1], result[i + 2]
        try:
            snr = float(snr)
        except (ValueError, TypeError):
            snr = None
        links.append((str(src), str(dst), snr))
    return links


def parse_uptime(raw):
    """Pull (up_duration, load_1min) out of the uptime string (Section 3.265).

    e.g. "23:51:08 up 2 days, 5:07, 0 users, load average: 1.05, 1.00, 1.02"
    Either field is None if the string does not match.
    """
    if not raw:
        return None, None
    up = re.search(r'\bup\s+(.*?),\s*\d+\s+users?', raw)
    load = re.search(r'load average:\s*([\d.]+)', raw)
    return (up.group(1).strip() if up else None,
            float(load.group(1)) if load else None)


def as_c(value):
    """Degrees C from a reading, or None if it is not one.

    Sensor readers hand back display strings ("47.0°C") or a status word
    ("Not Set", "TC Open"); the cutout needs the number or nothing.
    """
    if isinstance(value, (int, float)):
        return value
    try:
        return float(str(value).rstrip("°C"))
    except (TypeError, ValueError):
        return None


def hottest(temps):
    """(label, temp_c) of the hottest reading in {label: temp_c}, ignoring None.

    (None, None) if nothing read. An absent or failed sensor is simply not
    counted -- an unwired IPCOMM must not hold the cutout on forever.
    """
    have = [(c, k) for k, v in temps.items() if (c := as_c(v)) is not None]
    if not have:
        return None, None
    v, k = max(have)
    return k, v


def thermal_action(temp_c, idled, trip_c, resume_c):
    """Decide the thermal cutout: True = force idle, False = release, None = hold.

    temp_c is the hottest of every sensor we have -- any one of them over the
    trip idles the radio, and all of them must be back under resume to release.

    None on a missing reading too -- a poll that failed is not evidence the
    radio is cool, so an already-idled radio stays idled.
    """
    if temp_c is None or trip_c is None:
        return None
    if not idled and temp_c >= trip_c:
        return True
    if idled and resume_c is not None and temp_c <= resume_c:
        return False
    return None


# =============================================================================
#  Terminal panel
# =============================================================================

RESET = '\033[0m'
DIM = '\033[2m'
BOLD = '\033[1m'
GREEN = '\033[32m'
YELLOW = '\033[33m'
RED = '\033[31m'
CYAN = '\033[36m'

ANSI_RE = re.compile(r'\033\[[0-9;]*m')


def visible_len(s):
    """Length of s as rendered, ignoring ANSI colour escapes."""
    return len(ANSI_RE.sub('', s))


def paint(text, colour):
    return f"{colour}{text}{RESET}"


def temp_colour(temp_c, max_threshold):
    """Green below the heating band, yellow inside it, red once throttling.

    Section 3.18: the heating band runs from min to max threshold; above max
    the radio throttles. We only read the max, so approximate the band start
    as 10 C below it.
    """
    if temp_c is None or max_threshold is None:
        return CYAN
    if temp_c >= max_threshold:
        return RED
    if temp_c >= max_threshold - 10:
        return YELLOW
    return GREEN


def snr_colour(snr):
    if snr is None:
        return DIM
    if snr < 15:
        return RED
    if snr < 25:
        return YELLOW
    return GREEN


def fmt(value, spec="", suffix="", scale=1.0):
    """Format a possibly-None reading, falling back to a dim N/A."""
    if value is None:
        return paint("N/A", DIM)
    if scale != 1.0:
        value = value * scale
    return f"{value:{spec}}{suffix}"


class Panel:
    """Fixed-size box redrawn in place each poll."""

    def __init__(self, width):
        self.width = width
        self.lines = []

    def rule(self, title=None, top=False, bottom=False):
        left = '╭' if top else ('╰' if bottom else '├')
        right = '╮' if top else ('╯' if bottom else '┤')
        if title:
            bar = f"─ {title} "
            bar += '─' * max(0, self.width - 2 - visible_len(bar))
        else:
            bar = '─' * (self.width - 2)
        self.lines.append(f"{left}{bar}{right}")

    def row(self, label, body):
        text = f" {label:<7}{body}"
        pad = ' ' * max(0, self.width - 2 - visible_len(text))
        self.lines.append(f"│{text}{pad}│")

    def render(self):
        return '\n'.join(self.lines)


def tc_summary(tc):
    """Thermocouple readings on one line: "CH1 22.3°C   CH2 24.0°C"."""
    if not tc:
        return "Not Set"
    return "   ".join(f"CH{ch + 1} {v}" for ch, v in sorted(tc.items()))


def build_panel(timestamp, ip, t, max_threshold, ipcomms, width,
                tx_idle=False, tc=None):
    p = Panel(width)
    p.rule(f"{BOLD}Silvus {ip}{RESET}  {DIM}{timestamp}{RESET}", top=True)

    mcs = t["mcs"]
    mcs_str = "auto" if mcs == 255 else (paint("N/A", DIM) if mcs is None else str(mcs))
    p.row("RF", f"{fmt(t['freq_mhz'], '.1f', ' MHz')}   "
                f"BW {fmt(t['bw_mhz'], '.0f', ' MHz')}   "
                f"MCS {mcs_str}   "
                f"MaxSpd {fmt(t['max_speed_mph'], 'd', ' mph')}")

    temp_c = t["temperature_c"]
    tcol = temp_colour(temp_c, max_threshold)
    if temp_c is None or max_threshold is None:
        state = ""
    elif temp_c >= max_threshold:
        state = paint("  THROTTLING", RED)
    elif temp_c >= max_threshold - 10:
        state = paint("  HEATING", YELLOW)
    else:
        state = paint("  ok", GREEN)
    p.row("Temp", f"{paint(fmt(temp_c, 'd', ' °C'), tcol)}"
                  f"   {DIM}limit {fmt(max_threshold, 'd', ' °C')}{RESET}{state}")

    if tx_idle:
        p.row("TX", paint("FORCED IDLE - too hot, transmit disabled", RED))
    p.row("Power", f"{fmt(t['tx_power_dbm'], 'd', ' dBm')} / {fmt(t['tx_power_mw'], 'd', ' mW')}"
                   f"   Volt {fmt(t['voltage_mv'], '.2f', ' V', scale=0.001)}"
                   f"   Batt {fmt(t['battery_pct'], '.1f', ' %', scale=0.01)}")

    up, load = parse_uptime(t["uptime"])
    p.row("Noise", f"{fmt(t['noise_dbm'], 'd', ' dBm')}"
                   f"   Up {up or paint('N/A', DIM)}"
                   f"   Load {fmt(load, '.2f')}")

    p.rule("Mesh links (SNR)")
    links = t["links"]
    if links:
        for src, dst, snr in links:
            snr_str = paint(fmt(snr, '.0f', ' dB'), snr_colour(snr))
            p.row("", f"{src} → {dst:<10} {snr_str}")
    else:
        p.row("", paint("no active links", DIM))

    p.rule("IPCOMM")
    p.row("", "  ".join(f"{i + 1}: {v:<11}" for i, v in enumerate(ipcomms)).rstrip())

    p.rule("Thermocouples (DI-245)")
    p.row("", tc_summary(tc))
    p.rule(bottom=True)
    return p.render()


def ensure_url_scheme(url, default_scheme='http'):
    """Prepend http:// if no scheme is provided."""
    if url and not url.startswith(('http://', 'https://')):
        return f"{default_scheme}://{url}"
    return url


def get_board_temp(url, label="IPCOMM"):
    if not url:
        return "Not Set"

    try:
        url = ensure_url_scheme(url)
        log.debug(f"{label}: Fetching {url}")
        session = requests.Session()
        session.verify = False
        session.headers.update({
            'Cache-Control': 'no-cache, no-store, must-revalidate',
            'Pragma': 'no-cache',
            'Expires': '0'
        })

        response = session.get(url, timeout=5.0)
        log.debug(f"{label}: Response status={response.status_code}, len={len(response.text)}")

        if response.status_code != 200:
            log.warning(f"{label}: Got HTTP {response.status_code}")
            return "Conn Error"

        soup = BeautifulSoup(response.text, 'html.parser')

        label_cell = soup.find('td', string='Board Temp.')
        if not label_cell:
            label_cell = soup.find('td', string=lambda t: t and 'board temp' in t.lower())

        if label_cell:
            value_cell = label_cell.find_next('td')
            if value_cell:
                raw_text = value_cell.text.strip().replace('deg. F', '').replace('F', '').replace('°', '').strip()
                log.debug(f"{label}: Raw temp text='{raw_text}'")
                # Board reports Fahrenheit; display Celsius everywhere.
                return f"{(float(raw_text) - 32) * 5 / 9:.1f}°C"
            else:
                log.warning(f"{label}: Found 'Board Temp.' label but no value cell next to it")
                return "Val Missing"
        else:
            all_tds = [td.text.strip() for td in soup.find_all('td')][:20]
            log.warning(f"{label}: Could not find 'Board Temp.' in page. First 20 td cells: {all_tds}")
            return "Tag Error"

    except requests.exceptions.ConnectionError as e:
        log.error(f"{label}: Cannot connect to {url}: {e}")
        return "Fetch Error"
    except requests.exceptions.Timeout:
        log.error(f"{label}: Timeout connecting to {url}")
        return "Fetch Error"
    except ValueError as e:
        log.error(f"{label}: Could not parse temperature value: {e}")
        return "Parse Error"
    except Exception as e:
        log.error(f"{label}: Unexpected error: {type(e).__name__}: {e}")
        return "Fetch Error"


# =============================================================================
#  DATAQ DI-245 thermocouple
# =============================================================================

# Protocol: DI-245 Communication Protocol rev 1.09 (di-245-protocol.pdf).
# Virtual COM port, 115200 8N1. Short commands are prefixed with a null byte,
# long ones are space-separated and terminated with CR.

# chn scan-list word: bits 0-1 channel, bit 12 mode (1 = thermocouple),
# bits 8-10 pick the type. Mode bit plus the type index below is the whole word
# for a TC channel.
TC_TYPES = {
    #        type index, m, b   -- temperature = m * counts + b
    'B': (0, 0.095825, 1035),
    'E': (1, 0.073242, 400),
    'J': (2, 0.08606, 495),
    'K': (3, 0.095947, 586),
    'N': (4, 0.091553, 550),
    'R': (5, 0.110962, 859),
    'S': (6, 0.110962, 859),
    'T': (7, 0.036621, 100),
}

# Sentinel counts the DI-245 borrows from the measurement range for faults.
TC_CJC_ERROR = 8191
TC_BURNOUT = -8192


def tc_scan_word(channel, tc_type):
    """Scan-list value for one thermocouple channel."""
    return (1 << 12) | (TC_TYPES[tc_type][0] << 8) | channel


def decode_tc_scan(data, tc_type, count=1):
    """Latest scan as a list of `count` temperatures in C, or None.

    Each sample is two bytes: bit 0 is the sync flag (cleared on the first byte
    of a scan, set everywhere else), the remaining 7 bits of each carry the low
    then high half of a 14-bit signed count. A scan holds one sample per
    channel in the scan list, in scan-list order.
    """
    m, b = TC_TYPES[tc_type][1:]
    size = 2 * count
    # Walk backwards for the freshest complete scan.
    for i in range(len(data) - size, -1, -1):
        # Sync: first byte of the scan clear, every other byte of it set.
        if data[i] & 1 or not all(data[i + j] & 1 for j in range(1, size)):
            continue
        scan = []
        for j in range(0, size, 2):
            # Invert the most significant bit and read as two's complement,
            # which is a plain -0x2000 bias. The protocol's channel coding
            # table shows this without the inversion, but both the text above
            # that table and DATAQ's own 245SimpleTest2.py (data<<2 then
            # -32768) apply it, so the table is the odd one out.
            counts = ((data[i + j] >> 1) | ((data[i + j + 1] >> 1) << 7)) - 0x2000
            scan.append("CJC Error" if counts == TC_CJC_ERROR else
                        "TC Open" if counts == TC_BURNOUT else m * counts + b)
        return scan
    return None


def get_tc_temps(port, channels, tc_type, offset_c=0.0, label="DATAQ"):
    """{channel: display string} for every configured probe, one scan for all.

    Mirrors get_board_temp: never raises, a failure is a status word instead of
    a number. Opens, samples and closes per poll. At a 60s interval that costs
    nothing and means an unplugged DI-245 recovers by itself on the next poll.
    """
    def every(value):
        return {ch: value for ch in channels}

    if not port or not channels:
        return every("Not Set")
    try:
        import serial
    except ImportError:
        log.error(f"{label}: pyserial not installed (pip install pyserial)")
        return every("No pyserial")

    if tc_type not in TC_TYPES:
        log.error(f"{label}: unknown thermocouple type {tc_type!r}")
        return every("Bad TC Type")

    try:
        with serial.Serial(port, 115200, timeout=1.0) as ser:
            # Short commands take a leading null and no CR; long ones are
            # space-separated and CR-terminated. The device accepts these bare
            # too (DATAQ's 245SimpleTest2.py writes b"S1"), but it echoes the
            # command either way and never the null, so send it as documented.
            ser.write(b'\x00S0')            # stop, in case a previous run left it scanning
            time.sleep(0.1)
            ser.reset_input_buffer()
            # Scan-list slot per probe; the DI-245 then samples them in order.
            for slot, ch in enumerate(channels):
                ser.write(f"chn {slot} {tc_scan_word(ch, tc_type)}\r".encode())
            ser.write(b'dchn 0\r')          # analog only, no digital word in the scan
            ser.write(b'xrate 1795 200\r')  # 200 Hz burst, so a scan lands in ~5 ms
            time.sleep(0.1)
            ser.reset_input_buffer()
            ser.write(b'\x00S1')
            # Blocking read: returns as soon as the buffer is full (32 scans,
            # whatever the channel count), or after the port timeout if the
            # device says nothing. Sleeping a fixed interval instead means
            # guessing how long the first scan takes, and at low xrate values
            # that guess is wrong.
            data = ser.read(64 * len(channels))
            ser.write(b'\x00S0')

        log.debug(f"{label}: read {len(data)} bytes from {port}")
        scan = decode_tc_scan(data, tc_type, len(channels))
        if scan is None:
            log.warning(f"{label}: no complete scan in {len(data)} bytes")
            return every("No Data")

        out = {}
        for ch, temp_c in zip(channels, scan):
            if isinstance(temp_c, str):          # CJC Error / TC Open
                log.warning(f"{label} ch{ch + 1}: {temp_c}")
                out[ch] = temp_c
            else:
                out[ch] = f"{temp_c + offset_c:.1f}°C"
        return out

    except Exception as e:
        # serial.SerialException covers most of it, but an unplug mid-read can
        # surface as OSError too, and a dead probe must not kill the poll loop.
        log.error(f"{label}: {type(e).__name__}: {e}")
        return every("Fetch Error")
def build_log_entry(timestamp, t, ipcomms, tx_idle=False, tc=None):
    """One plain-text line for OUTPUT_FILE (no colour, no box)."""
    def plain(value, spec="", suffix="", scale=1.0):
        if value is None:
            return "N/A"
        if scale != 1.0:
            value = value * scale
        return f"{value:{spec}}{suffix}"

    mcs = t["mcs"]
    mcs_str = "auto" if mcs == 255 else plain(mcs)
    links = " ".join(f"{s}>{d}:{plain(snr, '.0f')}dB" for s, d, snr in t["links"]) or "none"
    up, load = parse_uptime(t["uptime"])
    return (
        f"[{timestamp}] "
        f"Silvus: {plain(t['temperature_c'], 'd', 'C')} "
        f"{plain(t['voltage_mv'], '.2f', 'V', scale=0.001)} "
        f"TX:{plain(t['tx_power_dbm'], 'd', 'dBm')}/{plain(t['tx_power_mw'], 'd', 'mW')} "
        f"Batt:{plain(t['battery_pct'], '.1f', '%', scale=0.01)} "
        f"Freq:{plain(t['freq_mhz'], '.1f', 'MHz')} "
        f"BW:{plain(t['bw_mhz'], '.0f', 'MHz')} "
        f"MCS:{mcs_str} "
        f"MaxSpd:{plain(t['max_speed_mph'], 'd', 'mph')} "
        f"Noise:{plain(t['noise_dbm'], 'd', 'dBm')} "
        f"Up:{up or 'N/A'} Load:{plain(load, '.2f')} "
        f"Links:{links} "
        f"{'TX:IDLE(thermal) ' if tx_idle else ''}| "
        f"TC: {tc_summary(tc)} | "
        + " | ".join(f"IPCOMM{i + 1}: {v}" for i, v in enumerate(ipcomms)) + "\n"
    )


CSV_FIELDS = [
    "timestamp", "temperature_c", "voltage_v", "tx_power_dbm", "tx_power_mw",
    "battery_pct", "freq_mhz", "bw_mhz", "mcs", "max_speed_mph", "noise_dbm",
    "uptime", "load_avg", "links", "ipcomm1", "ipcomm2",
    # One column per DI-245 input, always all four, so the header does not
    # move when DATAQ_CHANNELS changes. Unused inputs stay empty.
    "tc1", "tc2", "tc3", "tc4", "tx_idle",
]


def build_csv_row(timestamp, t, ipcomms, tx_idle=False, tc=None):
    """One row for CSV_FILE: raw values, empty cell for a missing reading."""
    up, load = parse_uptime(t["uptime"])
    volts = None if t["voltage_mv"] is None else round(t["voltage_mv"] / 1000, 3)
    batt = None if t["battery_pct"] is None else round(t["battery_pct"] / 100, 1)
    # Links vary in count per poll, so they stay one field instead of ragged columns.
    links = ";".join(f"{s}>{d}:{'' if snr is None else snr}" for s, d, snr in t["links"])
    tc = tc or {}
    row = [timestamp, t["temperature_c"], volts, t["tx_power_dbm"], t["tx_power_mw"],
           batt, t["freq_mhz"], t["bw_mhz"], t["mcs"], t["max_speed_mph"],
           t["noise_dbm"], up, load, links, *ipcomms,
           *(tc.get(ch) for ch in range(4)), int(tx_idle)]
    return ["" if v is None else v for v in row]


def append_csv_row(path, row):
    """Append one row, writing the header first if the file is new or empty."""
    new = not os.path.exists(path) or os.path.getsize(path) == 0
    with open(path, "a", newline="") as f:
        w = csv.writer(f)
        if new:
            w.writerow(CSV_FIELDS)
        w.writerow(row)


def selftest():
    """Parsing and layout checks. Run with: python temp_test.py --selftest"""
    assert parse_network_status(["22103", "41238", "34", "22103", "22108", "22"]) == [
        ("22103", "41238", 34.0), ("22103", "22108", 22.0)]
    assert parse_network_status([]) == []
    assert parse_network_status(None) == []
    assert parse_network_status(["1", "2"]) == []            # partial trio dropped
    assert parse_network_status(["1", "2", "x"]) == [("1", "2", None)]

    assert parse_uptime("23:51:08 up 2 days, 5:07, 0 users, load average: 1.05, 1.00, 1.02") \
        == ("2 days, 5:07", 1.05)

    # Scan words from the protocol's own "Example chn Commands" table.
    assert tc_scan_word(0, 'N') == 5120
    assert tc_scan_word(0, 'K') == 4864
    assert tc_scan_word(3, 'B') == 4099

    def scan(counts):
        """Pack signed 14-bit counts the way the DI-245 puts them on the wire."""
        raw = (counts + 0x2000) & 0x3FFF
        return bytes([(raw & 0x7F) << 1, (((raw >> 7) & 0x7F) << 1) | 1])

    # 0 counts sits at the type's b offset; the extremes are the documented
    # measurement range for that type (K: -180 to 1360 C).
    assert decode_tc_scan(scan(0), 'K') == [586]
    assert round(decode_tc_scan(scan(-8191), 'K')[0]) == -200
    assert round(decode_tc_scan(scan(8190), 'K')[0]) == 1372
    assert round(decode_tc_scan(scan(-5878), 'K')[0], 1) == 22.0   # room temperature
    # Agree bit-for-bit with DATAQ's own 245SimpleTest2.py, which assembles a
    # scan as (b1>>1) + ((b2&254)<<6), shifts left 2 and subtracts 32768.
    # Skips the two raw values reserved as fault sentinels.
    for raw in (1, 2314, 8192, 16382):
        b1, b2 = (raw & 0x7F) << 1, (((raw >> 7) & 0x7F) << 1) | 1
        dataq_counts = ((((b1 >> 1) + ((b2 & 254) << 6)) << 2) - 32768) // 4
        m, b = TC_TYPES['J'][1:]
        assert decode_tc_scan(bytes([b1, b2]), 'J') == [m * dataq_counts + b]

    assert decode_tc_scan(scan(8191), 'K') == ["CJC Error"]
    assert decode_tc_scan(scan(-8192), 'K') == ["TC Open"]
    # Echoed command bytes have their LSB set, so they cannot start a scan, and
    # the freshest complete scan wins over an older one.
    assert decode_tc_scan(b'S1' + scan(0) + scan(-5878), 'K') == \
        decode_tc_scan(scan(-5878), 'K')
    assert decode_tc_scan(b'S1', 'K') is None
    assert decode_tc_scan(b'', 'K') is None

    # Two channels: one scan is four bytes, sync bit clear only on the first.
    def scan2(a, b_):
        return bytes([scan(a)[0], scan(a)[1] | 1, scan(b_)[0] | 1, scan(b_)[1] | 1])

    two = decode_tc_scan(scan2(-5878, 0), 'K', 2)
    assert [round(v, 1) for v in two] == [22.0, 586.0]
    # A partial trailing scan is skipped for the last complete one behind it.
    assert decode_tc_scan(scan2(-5878, 0) + scan2(0, 0)[:2], 'K', 2) == two
    assert decode_tc_scan(scan(0), 'K', 2) is None
    # Channel order follows the scan list, so a 4-byte read is not two 1-channel
    # scans: decoding with the wrong count must not silently succeed.
    assert decode_tc_scan(scan2(-5878, 0)[1:], 'K', 2) is None

    assert as_c(47) == 47 and as_c("47.0°C") == 47.0
    assert as_c("Not Set") is None and as_c("TC Open") is None and as_c(None) is None
    assert hottest({"radio": 70, "TC CH1": "91.0°C"}) == ("TC CH1", 91.0)
    assert hottest({"radio": 70, "TC CH1": "TC Open"}) == ("radio", 70)

    assert tc_summary(None) == "Not Set"
    assert tc_summary({1: "24.0°C", 0: "22.3°C"}) == "CH1 22.3°C   CH2 24.0°C"
    assert parse_uptime("10:00:00 up 5 min, 1 user, load average: 0.10, 0.20, 0.30") \
        == ("5 min", 0.10)
    assert parse_uptime(None) == (None, None)
    assert parse_uptime("garbage") == (None, None)

    assert visible_len(paint("abc", RED)) == 3
    assert temp_colour(40, 85) == GREEN
    assert temp_colour(80, 85) == YELLOW
    assert temp_colour(90, 85) == RED
    assert temp_colour(40, None) == CYAN
    # Thermal cutout: trip at/above, release at/below, hysteresis in between.
    assert thermal_action(90, False, 85, 80) is True
    assert thermal_action(84, False, 85, 80) is None
    assert thermal_action(82, True, 85, 80) is None     # in the gap, stay idle
    assert thermal_action(80, True, 85, 80) is False
    assert thermal_action(90, True, 85, 80) is None     # already idle
    assert thermal_action(None, True, 85, 80) is None   # failed read: stay idle
    assert thermal_action(90, False, None, None) is None
    # Hottest sensor drives the cutout, whichever one it is.
    assert hottest({"radio": 70, "tc": 91, "ip": None}) == ("tc", 91)
    assert hottest({"radio": None, "tc": None}) == (None, None)
    assert thermal_action(hottest({"radio": 70, "tc": 91})[1], False, 85, 80) is True
    assert thermal_action(hottest({"radio": 82, "tc": 70})[1], True, 85, 80) is None
    assert thermal_action(hottest({"radio": 79, "tc": 70})[1], True, 85, 80) is False

    assert snr_colour(30) == GREEN and snr_colour(20) == YELLOW and snr_colour(5) == RED

    # Every rendered panel line must be exactly `width` visible columns.
    telem = {"temperature_c": 47, "voltage_mv": 12197.36, "tx_power_dbm": 30,
             "tx_power_mw": 1000, "battery_pct": 6500.0, "freq_mhz": 2490.0,
             "bw_mhz": 20.0, "mcs": 255, "max_speed_mph": 30, "noise_dbm": -95,
             "uptime": "23:51:08 up 2 days, 5:07, 0 users, load average: 1.05, 1.00, 1.02",
             "links": [("22103", "41238", 34.0)]}
    for w in (64, 80, 100):
        out = build_panel("2026-08-28 14:03:12", "172.20.4.31", telem, 85,
                          ["33.0°C", "Not Set"], w,
                          tc={0: "22.3°C", 1: "24.0°C"})
        for line in out.split('\n'):
            assert visible_len(line) == w, (w, visible_len(line), line)

    # Missing readings must not crash either renderer.
    empty = {k: None for k in telem}
    empty["links"] = []
    na = ["N/A", "N/A"]
    build_panel("2026-08-28 14:03:12", "1.2.3.4", empty, None, na, 80)
    assert "IPCOMM2: N/A" in build_log_entry("2026-08-28 14:03:12", empty, na)
    assert "Links:none" in build_log_entry("2026-08-28 14:03:12", empty, na)

    idle_panel = build_panel("2026-08-28 14:03:12", "172.20.4.31", telem, 85,
                             ["33.0°C", "Not Set"], 80, tx_idle=True)
    for line in idle_panel.split('\n'):
        assert visible_len(line) == 80, (visible_len(line), line)
    assert "FORCED IDLE" in idle_panel

    row = build_csv_row("2026-08-28 14:03:12", telem,
                        ["33.0°C", "Not Set"],
                        tc={0: "22.3°C", 3: "TC Open"})
    assert len(row) == len(CSV_FIELDS)
    assert row[CSV_FIELDS.index("tc1")] == "22.3°C"
    assert row[CSV_FIELDS.index("tc2")] == ""
    assert row[CSV_FIELDS.index("tc4")] == "TC Open"
    assert row[CSV_FIELDS.index("voltage_v")] == 12.197
    assert row[CSV_FIELDS.index("battery_pct")] == 65.0
    assert row[CSV_FIELDS.index("links")] == "22103>41238:34.0"
    assert row[CSV_FIELDS.index("tx_idle")] == 0
    assert build_csv_row("2026-08-28 14:03:12", telem, ["a", "b"],
                         tx_idle=True)[CSV_FIELDS.index("tx_idle")] == 1
    empty_row = build_csv_row("2026-08-28 14:03:12", empty, na)
    assert len(empty_row) == len(CSV_FIELDS)
    assert empty_row[CSV_FIELDS.index("temperature_c")] == ""

    print("selftest ok")


# =============================================================================
#  Main polling loop
# =============================================================================

def main():
    for path in (OUTPUT_FILE, CSV_FILE, DEBUG_LOG_FILE):
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)

    silvus = SilvusAPI(
        ip=SILVUS_IP,
        username=SILVUS_USER,
        password=SILVUS_PASS,
        fips_mode=SILVUS_FIPS_MODE,
    )

    # Static for the life of the run; the panel needs it to colour temperature.
    max_threshold = silvus.get_temp_max_threshold()

    trip_c = IDLE_TEMP_C if IDLE_TEMP_C is not None else max_threshold
    resume_c = IDLE_RESUME_C if IDLE_RESUME_C is not None else (
        None if trip_c is None else trip_c - 5)
    # Start from whatever the radio is already doing, so a restart mid-cutout
    # does not forget that transmit is off.
    tx_idle = bool(silvus.get_tx_disabled()) if THERMAL_IDLE else False
    if THERMAL_IDLE and trip_c is None:
        log.warning("Thermal idle: no threshold (set IDLE_TEMP_C in config.py); cutout inactive")

    interactive = sys.stdout.isatty()
    width = max(64, min(shutil.get_terminal_size((80, 24)).columns, 100))

    if not interactive:
        print(f"Polling network telemetry every {INTERVAL_SECONDS}s. Logging to {OUTPUT_FILE} and {CSV_FILE}")
        print(f"Silvus data via StreamCaster API at {silvus.api_url}")

    while True:
        timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        telemetry = silvus.get_all_telemetry()
        ipcomm_temps = [get_board_temp(u, label=f"IPCOMM{i + 1}")
                        for i, u in enumerate(IPCOMM_URLS)]
        tc_temps = get_tc_temps(DATAQ_PORT, DATAQ_CHANNELS, DATAQ_TC_TYPE,
                                DATAQ_OFFSET_C)

        if THERMAL_IDLE:
            hot_src, hot_c = hottest({
                "radio": telemetry["temperature_c"],
                **{f"IPCOMM{i + 1}": v for i, v in enumerate(ipcomm_temps)},
                **{f"TC CH{ch + 1}": v for ch, v in tc_temps.items()},
            })
            want = thermal_action(hot_c, tx_idle, trip_c, resume_c)
            if want is not None:
                if silvus.set_tx_disabled(want):
                    tx_idle = want
                    log.warning(f"Thermal idle: transmit {'DISABLED' if want else 'restored'} "
                                f"at {hot_c} C ({hot_src}) "
                                f"(trip {trip_c} C, resume {resume_c} C)")
                else:
                    log.error(f"Thermal idle: failed to set tx_fifo_disable={int(want)}")

        log_entry = build_log_entry(timestamp, telemetry, ipcomm_temps,
                                    tx_idle, tc_temps)
        with open(OUTPUT_FILE, "a") as f:
            f.write(log_entry)
        append_csv_row(CSV_FILE, build_csv_row(timestamp, telemetry, ipcomm_temps,
                                               tx_idle, tc_temps))

        if interactive:
            panel = build_panel(timestamp, SILVUS_IP, telemetry, max_threshold,
                                ipcomm_temps, width, tx_idle, tc_temps)
            # Home the cursor and clear, so the panel updates in place.
            sys.stdout.write('\033[H\033[J' + panel + '\n')
            sys.stdout.write(f"{DIM}every {INTERVAL_SECONDS}s → {OUTPUT_FILE}   csv → {CSV_FILE}"
                             f"   debug → {DEBUG_LOG_FILE}   ctrl-c to quit{RESET}\n")
            sys.stdout.flush()
        else:
            # Piped or redirected: no ANSI, just append the same line we logged.
            sys.stdout.write(log_entry)
            sys.stdout.flush()

        time.sleep(INTERVAL_SECONDS)


if __name__ == '__main__':
    if '--selftest' in sys.argv:
        selftest()
    elif '--dataq' in sys.argv:
        # One reading and quit, for checking the probe and the port without
        # waiting out a poll. Failures land in the debug log as usual.
        print(tc_summary(get_tc_temps(DATAQ_PORT, DATAQ_CHANNELS, DATAQ_TC_TYPE,
                                      DATAQ_OFFSET_C)))
    else:
        try:
            main()
        except KeyboardInterrupt:
            print()
