# Copy to config.py and edit. config.py is gitignored, so it survives pulls
# and never lands in a commit.
SILVUS_IP = '172.20.0.1'
IPCOMM1_URL = 'http://10.128.1.1'
IPCOMM2_URL = ''

SILVUS_FIPS_MODE = True
SILVUS_USER = 'admin'
SILVUS_PASS = 'password'

# Optional:
# INTERVAL_SECONDS = 60

# Thermal cutout (forces tx_fifo_disable=1 above IDLE_TEMP_C, releases at
# IDLE_RESUME_C). Defaults: radio's own overheat threshold, trip - 5.
# THERMAL_IDLE = True
# IDLE_TEMP_C = 80
# IDLE_RESUME_C = 70

# DATAQ DI-245 thermocouple. Empty port disables it. The port is
# /dev/dataq-di245 after running setup-di245.sh; see the README.
# DATAQ_PORT = '/dev/dataq-di245'
# DATAQ_CHANNEL = 0        # 0-3; the terminal block silkscreen says 1-4
# DATAQ_TC_TYPE = 'K'      # B E J K N R S T
# DATAQ_OFFSET_C = 0.0     # calibration trim, added to every reading
