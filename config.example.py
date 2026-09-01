# Copy to config.py and edit. config.py is gitignored, so it survives pulls
# and never lands in a commit.
SILVUS_IP = '172.20.0.1'
IPCOMM1_URL = 'http://10.128.1.1'
IPCOMM2_URL = ''

SILVUS_FIPS_MODE = True
SILVUS_USER = 'admin'
SILVUS_PASS = 'password'

# DATAQ DI-245 thermocouple. Set DATAQ_PORT = '' on a machine that has no
# DI-245 wired to it. /dev/dataq-di245 is the name setup-di245.sh gives the
# port; without that script it is /dev/ttyUSB0 and moves around.
DATAQ_PORT = '/dev/dataq-di245'
DATAQ_CHANNELS = [0, 1]  # one per probe; 0-3, the silkscreen says 1-4
DATAQ_TC_TYPE = 'K'      # B E J K N R S T; same for every channel
DATAQ_OFFSET_C = 0.0     # calibration trim, added to every reading

# Optional:
# INTERVAL_SECONDS = 60

# Thermal cutout (forces tx_fifo_disable=1 when ANY temperature we read --
# radio, IPCOMM1, IPCOMM2, thermocouple -- goes above IDLE_TEMP_C; releases
# once all of them are back below IDLE_RESUME_C).
# Defaults: radio's own overheat threshold, trip - 5.
# THERMAL_IDLE = True
# IDLE_TEMP_C = 80
# IDLE_RESUME_C = 70
