#!/bin/bash
set -e
cd ~/Public_Expose/juniperProject

echo "--- killing any leftover cloud-hypervisor processes ---"
sudo pkill -9 -f cloud-hypervisor 2>/dev/null || true
sleep 1

echo "--- removing leftover tap-* interfaces ---"
for tap in $(ip -br link show type tun 2>/dev/null | awk '{print $1}' | grep '^tap-'); do
  echo "  removing $tap"
  sudo ip link delete "$tap" 2>/dev/null || true
done

echo "--- clearing runtime artifacts ---"
sudo rm -f var/ch-sockets/*.sock var/ch-sockets/*.sock.lock var/runtime-disks/*.qcow2 var/ch-logs/*.log

echo "--- resetting non-terminal lab rows in the DB ---"
.venv/bin/python -c "
import sys; sys.path.insert(0, 'backend')
import asyncio
from sqlalchemy import delete
from app.db import SessionLocal
from app.models import Lab
async def main():
    async with SessionLocal() as db:
        await db.execute(delete(Lab))
        await db.commit()
        print('lab table cleared')
asyncio.run(main())
"

echo "--- final check ---"
pgrep -af cloud-hypervisor || echo "no cloud-hypervisor processes"
ip -br link show type tun | grep '^tap-' || echo "no leftover taps"
ls var/ch-sockets/ var/runtime-disks/ var/ch-logs/ 2>&1

echo "--- clean ---"
