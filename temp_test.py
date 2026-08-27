import time
import datetime
import requests
import os
from bs4 import BeautifulSoup
from pysnmp.hlapi import *

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

def get_silvus_telemetry_via_web(ip, username, password):
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
        
        # Send post request to clear the FIPS barrier gateway
        login_response = session.post(login_url, data=login_payload, timeout=3.0)
        
        # 2. Grab the live status/diagnostics page where temperature metrics live
        status_url = f"http://{ip}/status.php" # Change to 'diagnostics.php' if metrics live there in your FW version
        response = session.get(status_url, timeout=3.0)
        
        if response.status_code != 200:
            return None, None
            
        soup = BeautifulSoup(response.text, 'html.parser')
        
        # 3. Locate the Temperature element inside the Silvus structure
        # (Silvus typically uses table structures or unique string tokens for diagnostics)
        temp_element = soup.find(string=lambda text: text and "internal temp" in text.lower())
        volt_element = soup.find(string=lambda text: text and "voltage" in text.lower())
        
        # Extract numbers from siblings if found
        temp_val = int(''.join(filter(str.isdigit, temp_element.find_next().text))) if temp_element else None
        volt_raw = int(''.join(filter(str.isdigit, volt_element.find_next().text))) if volt_element else None
        
        return temp_val, volt_raw
    except Exception:
        return None, None

def get_board_temp(url):
    if not url:
        return "Not Set"
        
    try:
        session = requests.Session()
        session.headers.update({
            'Cache-Control': 'no-cache, no-store, must-revalidate',
            'Pragma': 'no-cache',
            'Expires': '0'
        })
        
        response = session.get(url, timeout=3.0)
        if response.status_code != 200:
            return "Conn Error"
            
        soup = BeautifulSoup(response.text, 'html.parser')
        
        label_cell = soup.find('td', string='Board Temp.')
        
        if label_cell:
            value_cell = label_cell.find_next('td')
            if value_cell:
                raw_text = value_cell.text.strip().replace('deg. F', '').replace('F', '').replace('°', '').strip()
                return f"{float(raw_text):.1f}°F"
            else:
                return "Val Missing"
        else:
            return "Tag Error"
            
    except Exception:
        return "Fetch Error"

os.makedirs(os.path.dirname(OUTPUT_FILE), exist_ok=True)

print(f"Polling network telemetry loops. Output targeted to {OUTPUT_FILE}...")
print(f"{'Timestamp':<19} | {'Silvus Temp':<11} | {'Silvus Pwr':<10} | {'IPCOMM Int 1':<12} | {'IPCOMM Ext 2':<12}")
print("-" * 80)

while True:
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    # 1. Silvus Data (Celsius)
    silvus_temp_raw, silvus_volt_raw = get_silvus_telemetry_via_web(SILVUS_IP, SILVUS_USER, SILVUS_PASS)
    
    silvus_temp = f"{silvus_temp_raw}°C" if silvus_temp_raw is not None else "N/A"
    
    if silvus_volt_raw is not None:
        voltage = silvus_volt_raw / 1000.0
        silvus_amp_est = 0.2                
        watts = voltage * silvus_amp_est
        silvus_power = f"{watts:.1f}W"
    else:
        silvus_power = "N/A"

    # 2. IPCOMM Data (Fahrenheit)        
    ipcomm1_temp = get_board_temp(IPCOMM1_URL)
    ipcomm2_temp = get_board_temp(IPCOMM2_URL)
    
    log_entry = f"[{timestamp}] Silvus: {silvus_temp} ({silvus_power}) | IPCOMM Internal 1: {ipcomm1_temp} | IPCOMM External 2: {ipcomm2_temp}\n"
    
    print(f"{timestamp} | {silvus_temp:<11} | {silvus_power:<10} | {ipcomm1_temp:<12} | {ipcomm2_temp:<12}")
    
    with open(OUTPUT_FILE, "a") as f:
        f.write(log_entry)
        
    time.sleep(INTERVAL_SECONDS)
