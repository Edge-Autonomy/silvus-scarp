#!/usr/bin/env bash
# Prepare an Ubuntu machine for the DATAQ DI-245. Run once per machine:
#
#   sudo ./setup-di245.sh
#
# The DI-245 is an FTDI device, but its VID/PID pair is not in the ftdi_sio
# driver's table, so nothing claims it and no /dev/ttyUSB* appears. The udev
# rule below registers the pair at plug time and gives the resulting port a
# stable name, which is what makes DATAQ_PORT survive a reboot or a replug
# into a different socket.
set -euo pipefail

VID=0683
PID=2450
RULES=/etc/udev/rules.d/99-dataq-di245.rules

[ "$(id -u)" -eq 0 ] || { echo "Run with sudo." >&2; exit 1; }
[ -w /etc/udev/rules.d ] || {
    echo "/etc/udev/rules.d is not writable - this script is for Ubuntu and" >&2
    echo "other distributions with a mutable /etc. On NixOS, put the same two" >&2
    echo "rules in services.udev.extraRules instead." >&2
    exit 1
}

# The invoking user, not root, is the one who needs port access.
TARGET_USER=${SUDO_USER:-}

cat > "$RULES" <<EOF
# DATAQ DI-245 ($VID:$PID). ftdi_sio has no entry for this pair, so register
# it as the device appears; without this the DI-245 enumerates but no serial
# port is created.
ACTION=="add", SUBSYSTEM=="usb", ATTR{idVendor}=="$VID", ATTR{idProduct}=="$PID", \\
  RUN+="/bin/sh -c 'modprobe ftdi_sio; echo $VID $PID > /sys/bus/usb-serial/drivers/ftdi_sio/new_id 2>/dev/null || true'"

# Stable name for the port. /dev/ttyUSB0 is whatever plugged in first, so the
# script should point at the symlink instead.
ACTION=="add", SUBSYSTEM=="tty", ATTRS{idVendor}=="$VID", ATTRS{idProduct}=="$PID", \\
  SYMLINK+="dataq-di245", GROUP="dialout", MODE="0660"
EOF
echo "wrote $RULES"

# Load it now as well, so this boot works without the rule having to fire.
modprobe ftdi_sio
echo "$VID $PID" > /sys/bus/usb-serial/drivers/ftdi_sio/new_id 2>/dev/null || true

udevadm control --reload-rules
udevadm trigger --subsystem-match=usb --action=add

if [ -n "$TARGET_USER" ] && ! id -nG "$TARGET_USER" | tr ' ' '\n' | grep -qx dialout; then
    usermod -aG dialout "$TARGET_USER"
    echo "added $TARGET_USER to dialout - log out and back in for it to apply"
fi

echo
if [ -e /dev/dataq-di245 ]; then
    echo "ready: /dev/dataq-di245 -> $(readlink -f /dev/dataq-di245)"
    echo "set DATAQ_PORT = '/dev/dataq-di245' in config.py"
else
    echo "no DI-245 detected yet. Plug it in (or replug it) and check:"
    echo "  ls -l /dev/dataq-di245"
fi
