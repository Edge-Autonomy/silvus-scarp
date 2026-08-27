import time
import datetime
import requests
import os
import logging
from bs4 import BeautifulSoup
from pysnmp.hlapi import *

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
SILVUS_USER = 'XXX'
SILVUS_PASS = 'XXX'
IPCOMM1_URL = 'XXX' 
IPCOMM2_URL = ''

# --- SILVUS SECURITY CONFIGURATION ---
USE_SNMP_V3 = False  # Set to True if your network enforces encrypted SNMPv3 profiles

# Enter your custom Silvus Read-Only SNMP Community Password here (Set via StreamScape GUI admin settings)
SNMP_V2_COMMUNITY = 'private' 

# If using SNMPv3, provide your radio's authentication credentials:
SNMP_V3_USER = 'XXX'
SNMP_V3_AUTH_PASS = 'XXX'
SNMP_V3_PRIV_PASS = 'XXXX'
# -------------------------------------

# Silvus Diagnostic OIDs
OID_TEMP = '1.3.6.1.4.1.41728.1.1.1.0'     # streamscapeTemperatureCurrent.0
OID_VOLT = '1.3.6.1.4.1.41728.1.1.2.0'     # streamscapeVoltageCurrent.0

def get_silvus_telemetry_via_snmp(ip):
    """Query Silvus radio telemetry using SNMP (preferred over web scraping)."""
    try:
        if USE_SNMP_V3:
            log.debug(f"SNMP: Querying {ip} via SNMPv3 (user={SNMP_V3_USER})")
            auth_data = UsmUserData(
                SNMP_V3_USER,
                authKey=SNMP_V3_AUTH_PASS,
                privKey=SNMP_V3_PRIV_PASS,
                authProtocol=usmHMACSHAAuthProtocol,
                privProtocol=usmAesCfb128Protocol,
            )
        else:
            log.debug(f"SNMP: Querying {ip} via SNMPv2c (community={SNMP_V2_COMMUNITY})")
            auth_data = CommunityData(SNMP_V2_COMMUNITY, mpModel=1)

        transport = UdpTransportTarget((ip, 161), timeout=3, retries=1)

        temp_val = None
        volt_val = None

        # Fetch temperature
        error_indication, error_status, error_index, var_binds = next(
            getCmd(SnmpEngine(), auth_data, transport, ContextData(),
                   ObjectType(ObjectIdentity(OID_TEMP)))
        )
        if error_indication:
            log.warning(f"SNMP temp error_indication: {error_indication}")
        elif error_status:
            log.warning(f"SNMP temp error_status: {error_status.prettyPrint()} at {error_index}")
        else:
            for oid, val in var_binds:
                log.debug(f"SNMP temp response: {oid.prettyPrint()} = {val.prettyPrint()}")
                temp_val = int(val)

        # Fetch voltage
        error_indication, error_status, error_index, var_binds = next(
            getCmd(SnmpEngine(), auth_data, transport, ContextData(),
                   ObjectType(ObjectIdentity(OID_VOLT)))
        )
        if error_indication:
            log.warning(f"SNMP volt error_indication: {error_indication}")
        elif error_status:
            log.warning(f"SNMP volt error_status: {error_status.prettyPrint()} at {error_index}")
        else:
            for oid, val in var_binds:
                log.debug(f"SNMP volt response: {oid.prettyPrint()} = {val.prettyPrint()}")
                volt_val = int(val)

        return temp_val, volt_val

    except Exception as e:
        log.error(f"SNMP query to {ip} failed: {type(e).__name__}: {e}")
        return None, None

def get_silvus_telemetry_via_web(ip, username, password):
    """Fallback: scrape Silvus StreamScape web UI for telemetry."""
    try:
        # Create a persistent session to maintain login authentication cookies
        session = requests.Session()
        session.headers.update({
            'Cache-Control': 'no-cache, no-store, must-revalidate',
            'Pragma': 'no-cache'
        })
        
        # 1. Authenticate and log into the StreamScape Web server
        login_url = f"http://{ip}/login.php"  # Target the radio's login endpoint
        login_payload = {'username': username, 'password': password}
        
        log.debug(f"Web: Logging in to {login_url}")
        login_response = session.post(login_url, data=login_payload, timeout=5.0)
        log.debug(f"Web: Login response status={login_response.status_code}, len={len(login_response.text)}")
        
        # Check if login actually succeeded (look for redirect or session cookie)
        if login_response.status_code not in (200, 302):
            log.warning(f"Web: Login failed with status {login_response.status_code}")
            return None, None
        
        # 2. Grab the live status/diagnostics page where temperature metrics live
        status_url = f"http://{ip}/status.php"  # Change to 'diagnostics.php' if metrics live there in your FW version
        log.debug(f"Web: Fetching status page {status_url}")
        response = session.get(status_url, timeout=5.0)
        
        if response.status_code != 200:
            log.warning(f"Web: Status page returned {response.status_code}")
            return None, None
        
        log.debug(f"Web: Status page length={len(response.text)}")
        soup = BeautifulSoup(response.text, 'html.parser')
        
        # 3. Locate the Temperature element inside the Silvus structure
        temp_element = soup.find(string=lambda text: text and "internal temp" in text.lower())
        volt_element = soup.find(string=lambda text: text and "voltage" in text.lower())
        
        if not temp_element:
            log.warning("Web: Could not find 'internal temp' text in status page HTML")
        if not volt_element:
            log.warning("Web: Could not find 'voltage' text in status page HTML")
        
        # Extract numbers from siblings if found
        temp_val = int(''.join(filter(str.isdigit, temp_element.find_next().text))) if temp_element else None
        volt_raw = int(''.join(filter(str.isdigit, volt_element.find_next().text))) if volt_element else None
        
        log.debug(f"Web: Parsed temp={temp_val}, volt={volt_raw}")
        return temp_val, volt_raw
        
    except requests.exceptions.ConnectionError as e:
        log.error(f"Web: Cannot connect to Silvus at {ip}: {e}")
        return None, None
    except requests.exceptions.Timeout:
        log.error(f"Web: Timeout connecting to Silvus at {ip}")
        return None, None
    except Exception as e:
        log.error(f"Web: Silvus scrape failed: {type(e).__name__}: {e}")
        return None, None

def get_silvus_telemetry(ip, username, password):
    """Try SNMP first, fall back to web scraping."""
    log.info(f"Querying Silvus radio at {ip}...")
    temp, volt = get_silvus_telemetry_via_snmp(ip)
    if temp is not None or volt is not None:
        log.info(f"SNMP succeeded: temp={temp}, volt={volt}")
        return temp, volt
    log.info("SNMP returned no data, falling back to web scraping...")
    temp, volt = get_silvus_telemetry_via_web(ip, username, password)
    log.info(f"Web scrape result: temp={temp}, volt={volt}")
    return temp, volt

def get_board_temp(url, label="IPCOMM"):
    if not url:
        return "Not Set"
        
    try:
        log.debug(f"{label}: Fetching {url}")
        session = requests.Session()
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
        
        # Try exact match first, then case-insensitive partial match
        label_cell = soup.find('td', string='Board Temp.')
        if not label_cell:
            label_cell = soup.find('td', string=lambda t: t and 'board temp' in t.lower())
        
        if label_cell:
            value_cell = label_cell.find_next('td')
            if value_cell:
                raw_text = value_cell.text.strip().replace('deg. F', '').replace('F', '').replace('°', '').strip()
                log.debug(f"{label}: Raw temp text='{raw_text}'")
                return f"{float(raw_text):.1f}°F"
            else:
                log.warning(f"{label}: Found 'Board Temp.' label but no value cell next to it")
                return "Val Missing"
        else:
            # Log all td contents to help diagnose the page structure
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

os.makedirs(os.path.dirname(OUTPUT_FILE), exist_ok=True)

print(f"Polling network telemetry loops. Output targeted to {OUTPUT_FILE}...")
print(f"{'Timestamp':<19} | {'Silvus Temp':<11} | {'Silvus Pwr':<10} | {'IPCOMM Int 1':<12} | {'IPCOMM Ext 2':<12}")
print("-" * 80)

while True:
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    # 1. Silvus Data (Celsius) - SNMP first, web scrape fallback
    silvus_temp_raw, silvus_volt_raw = get_silvus_telemetry(SILVUS_IP, SILVUS_USER, SILVUS_PASS)
    
    silvus_temp = f"{silvus_temp_raw}°C" if silvus_temp_raw is not None else "N/A"
    
    if silvus_volt_raw is not None:
        voltage = silvus_volt_raw / 1000.0
        silvus_amp_est = 0.2                
        watts = voltage * silvus_amp_est
        silvus_power = f"{watts:.1f}W"
    else:
        silvus_power = "N/A"

    # 2. IPCOMM Data (Fahrenheit)        
    ipcomm1_temp = get_board_temp(IPCOMM1_URL, label="IPCOMM1")
    ipcomm2_temp = get_board_temp(IPCOMM2_URL, label="IPCOMM2")
    
    log_entry = f"[{timestamp}] Silvus: {silvus_temp} ({silvus_power}) | IPCOMM Internal 1: {ipcomm1_temp} | IPCOMM External 2: {ipcomm2_temp}\n"
    
    print(f"{timestamp} | {silvus_temp:<11} | {silvus_power:<10} | {ipcomm1_temp:<12} | {ipcomm2_temp:<12}")
    
    with open(OUTPUT_FILE, "a") as f:
        f.write(log_entry)
        
    time.sleep(INTERVAL_SECONDS)
