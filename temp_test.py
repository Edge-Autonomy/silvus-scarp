import time
import datetime
import requests
import urllib3
import os
import logging
from bs4 import BeautifulSoup

# Suppress InsecureRequestWarning for self-signed certs
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# --- LOGGING SETUP ---
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
log = logging.getLogger(__name__)

OUTPUT_FILE = '/home/slo-elec/Desktop/temp_test.txt'
INTERVAL_SECONDS = 60

SILVUS_IP = 'XXX'
IPCOMM1_URL = 'XXX'
IPCOMM2_URL = ''

# --- SILVUS FIPS / AUTH CONFIGURATION ---
# FIPS mode enforces: HTTPS only, login authentication required, SSH disabled.
# If your radio is in FIPS mode, set SILVUS_FIPS_MODE = True.
# This forces HTTPS and mandatory authentication.
SILVUS_FIPS_MODE = True

# Login credentials (required when FIPS is on, or when login_auth_disable is 0)
SILVUS_USER = 'XXX'
SILVUS_PASS = 'XXX'


# =============================================================================
#  Silvus StreamCaster API Client (JSON-RPC 2.0)
# =============================================================================
# Reference: StreamCaster Programming Manual v5.0.1.17
# API endpoint: POST http://<IP>/streamscape_api
# All methods return {"result": [...], "id": "...", "jsonrpc": "2.0"} on success.

class SilvusAPI:
    """Thin wrapper around the Silvus StreamCaster JSON-RPC 2.0 API.

    Supports both FIPS and non-FIPS radios:
      - FIPS mode: HTTPS only, login authentication mandatory (Section 13).
      - Non-FIPS: HTTP by default, auth optional.

    Session cookies are maintained automatically by requests.Session.
    The cookie refreshes with every API call (10-minute expiry per Section 9).
    If a session expires, the client re-authenticates automatically.
    """

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

    # --- Convenience methods for telemetry data ---

    def get_temperature(self):
        """Read current temperature in degrees Celsius (Section 3.21)."""
        result = self._call("read_current_temperature")
        if result and len(result) > 0:
            try:
                return int(result[0])
            except (ValueError, TypeError):
                log.warning(f"Silvus API: Could not parse temperature: {result[0]}")
        return None

    def get_input_voltage(self):
        """Read input voltage in millivolts (Section 3.119)."""
        result = self._call("input_voltage_monitoring")
        if result and len(result) > 0:
            try:
                return float(result[0])
            except (ValueError, TypeError):
                log.warning(f"Silvus API: Could not parse voltage: {result[0]}")
        return None

    def get_tx_power_dbm(self):
        """Read actual transmitted total output power in dBm (Section 3.58)."""
        result = self._call("read_power_dBm")
        if result and len(result) > 0:
            try:
                return int(result[0])
            except (ValueError, TypeError):
                log.warning(f"Silvus API: Could not parse power dBm: {result[0]}")
        return None

    def get_tx_power_mw(self):
        """Read actual transmitted total output power in mW (Section 3.59)."""
        result = self._call("read_power_mw")
        if result and len(result) > 0:
            try:
                return int(result[0])
            except (ValueError, TypeError):
                log.warning(f"Silvus API: Could not parse power mW: {result[0]}")
        return None

    def get_battery_percent(self):
        """Read battery percentage (Section 3.122). Divide by 100 for GUI value."""
        result = self._call("battery_percent")
        if result and len(result) > 0:
            try:
                return float(result[0])
            except (ValueError, TypeError):
                log.warning(f"Silvus API: Could not parse battery: {result[0]}")
        return None

    def get_node_id(self):
        """Read the radio's unique 20-bit node ID (Section 3.1)."""
        result = self._call("nodeid")
        if result and len(result) > 0:
            return result[0]
        return None

    def get_all_telemetry(self):
        """Fetch all available telemetry data in one call.

        Returns a dict with all readings (values may be None if unavailable).
        """
        return {
            "temperature_c": self.get_temperature(),
            "voltage_mv": self.get_input_voltage(),
            "tx_power_dbm": self.get_tx_power_dbm(),
            "tx_power_mw": self.get_tx_power_mw(),
            "battery_pct": self.get_battery_percent(),
        }


# =============================================================================
#  IPCOMM Board Temperature (web scrape - unchanged)
# =============================================================================

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
                raw_text = value_cell.text.strip().replace('deg. F', '').replace('F', '').replace('\u00b0', '').strip()
                log.debug(f"{label}: Raw temp text='{raw_text}'")
                return f"{float(raw_text):.1f}\u00b0F"
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
#  Main polling loop
# =============================================================================

os.makedirs(os.path.dirname(OUTPUT_FILE), exist_ok=True)

# Initialize the Silvus API client
silvus = SilvusAPI(
    ip=SILVUS_IP,
    username=SILVUS_USER,
    password=SILVUS_PASS,
    fips_mode=SILVUS_FIPS_MODE,
)

scheme = "HTTPS" if SILVUS_FIPS_MODE else "HTTP"
print(f"Polling network telemetry every {INTERVAL_SECONDS}s. Logging to {OUTPUT_FILE}")
print(f"Silvus data via StreamCaster API at {silvus.api_url}")
if SILVUS_FIPS_MODE:
    print(f"FIPS mode: HTTPS enforced, login authentication required")
print()
print(f"{'Timestamp':<19} | {'Temp':>6} | {'Voltage':>10} | {'TX dBm':>7} | {'TX mW':>7} | {'Batt':>6} | {'IPCOMM1':<12} | {'IPCOMM2':<12}")
print("-" * 105)

while True:
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # 1. Silvus telemetry via StreamCaster API
    telemetry = silvus.get_all_telemetry()

    temp_c = telemetry["temperature_c"]
    voltage_mv = telemetry["voltage_mv"]
    tx_dbm = telemetry["tx_power_dbm"]
    tx_mw = telemetry["tx_power_mw"]
    battery = telemetry["battery_pct"]

    # Format display strings
    temp_str = f"{temp_c}\u00b0C" if temp_c is not None else "N/A"

    if voltage_mv is not None:
        voltage_v = voltage_mv / 1000.0
        volt_str = f"{voltage_v:.2f}V"
    else:
        volt_str = "N/A"

    dbm_str = f"{tx_dbm}dBm" if tx_dbm is not None else "N/A"
    mw_str = f"{tx_mw}mW" if tx_mw is not None else "N/A"

    if battery is not None:
        batt_str = f"{battery / 100.0:.1f}%"
    else:
        batt_str = "N/A"

    # 2. IPCOMM Data (Fahrenheit) - still web scraped
    ipcomm1_temp = get_board_temp(IPCOMM1_URL, label="IPCOMM1")
    ipcomm2_temp = get_board_temp(IPCOMM2_URL, label="IPCOMM2")

    # Build log line
    log_entry = (
        f"[{timestamp}] "
        f"Silvus: {temp_str} {volt_str} TX:{dbm_str}/{mw_str} Batt:{batt_str} | "
        f"IPCOMM1: {ipcomm1_temp} | IPCOMM2: {ipcomm2_temp}\n"
    )

    # Console output (tabular)
    print(
        f"{timestamp} | {temp_str:>6} | {volt_str:>10} | {dbm_str:>7} | {mw_str:>7} | "
        f"{batt_str:>6} | {ipcomm1_temp:<12} | {ipcomm2_temp:<12}"
    )

    with open(OUTPUT_FILE, "a") as f:
        f.write(log_entry)

    time.sleep(INTERVAL_SECONDS)
