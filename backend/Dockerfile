FROM python:3.11-slim

WORKDIR /app

# System packages needed for:
#   - `ip` command (iproute2) for TAP/bridge management
#   - `qemu-img` (qemu-utils) for qcow2 overlay conversion in local_ch.py
#   - `sudo` for non-root escalation when invoking cloud-hypervisor
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        iproute2 \
        qemu-utils \
        sudo \
    && rm -rf /var/lib/apt/lists/*

# Backend source — the build context is the repo root in docker-compose.
COPY backend/ ./

RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -e .

EXPOSE 8000

# The container needs to:
#   - create TAP interfaces (NET_ADMIN capability is granted via --privileged in compose)
#   - invoke the cloud-hypervisor binary on the host (mounted from host)
#   - speak HTTP to ipam-service (DNS via docker compose network)
# Run as root so we have the network privileges. In a production-grade setup
# you'd grant NET_ADMIN + a CAP_* set instead.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]