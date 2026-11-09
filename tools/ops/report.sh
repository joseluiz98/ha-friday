#!/bin/sh
# Gemini cost report of the F.R.I.D.A.Y. (runs on the VM friday). Usage: report.sh day ontem | month este | range A B | projection
exec ssh -o BatchMode=yes "$(cat /config/.friday/vm)" sudo python3 /opt/friday/cost/cost_report.py "$@"
