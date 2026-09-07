# Automated MicroVM Network Lab Platform

An on-demand, isolated network-lab platform that boots **Juniper routers and switches** inside
**Cloud Hypervisor microVMs** running **ContainerLab**, then gives each student a private,
console-attached lab they can tear down with a single API call.

This repository ships the **Backend Agent** (orchestrator, REST + WebSocket API, IPAM) and a
reference **Lab Agent** that runs *inside* the microVM. The hypervisor itself is pluggable:
today a built-in mock backend runs the full lifecycle on a developer workstation; tomorrow the
same backend talks to a real Cloud Hypervisor host on a separate VPS.

---

## Table of contents

1. [Introduction](#1-introduction)
2. [Overview](#2-overview)
3. [Architecture](#3-architecture)
4. [Repository layout](#4-repository-layout)
5. [Lab state machine](#5-lab-state-machine)
6. [End-to-end guide: provisioning Cloud Hypervisor + ContainerLab + Juniper](#6-end-to-end-guide-provisioning-cloud-hypervisor--containerlab--juniper)
7. [Quickstart: backend on your workstation](#7-quickstart-backend-on-your-workstation)
8. [API reference](#8-api-reference)
9. [Configuration](#9-configuration)
10. [Logging](#10-logging)
11. [Testing — input/output validation (backend only)](#11-testing--inputoutput-validation-backend-only)
12. [Adding a new lab type](#12-adding-a-new-lab-type)
13. [License](#13-license)

---

## 1. Introduction

Networking students and engineers need realistic, isolated hands-on labs. Physical gear
doesn't scale, full-router simulators on the host don't isolate per-student state, and naive
Docker-based topologies miss the real Junos boot path.

This platform solves that by **running every student's lab inside its own microVM** booted from
a pre-baked *golden image* (a Cloud Hypervisor root disk containing Docker, ContainerLab,
vrnetlab, and the relevant Juniper image files). Each lab gets:

- A dedicated `/24` carved out of the platform's IPAM pool.
- A TAP interface plumbed onto a host bridge so the microVM has L2 connectivity.
- A private ContainerLab topology the student can SSH / console into.
- A hard kill-switch (`DELETE /labs/{id}`) that releases every resource, top-to-bottom.

The student only ever sees the **Backend Agent API**. They never need to know what hypervisor
runs underneath.

## 2. Overview

| What this repo is | What this repo isn't |
|---|---|
| A FastAPI Backend Agent that owns lab lifecycles | A UI / frontend |
| A reference Lab Agent that runs *inside* the microVM | The Cloud Hypervisor host binary itself |
| An IPAM + state machine + orchestrator + WS console gateway | A replacement for ContainerLab or Junos |
| Mock-hypervisor mode for full local development | A production multi-tenant SaaS (yet) |

**Current status:** MVP backend. The hypervisor backend is abstracted behind
`HypervisorBackend`; the in-repo `MockHypervisorBackend` simulates the VM lifecycle on a
background asyncio task so the entire state machine can be exercised on a laptop. When the
Cloud Hypervisor VPS comes online, only `LocalCloudHypervisorBackend` needs to be implemented —
no orchestrator, API, or Lab Agent changes are required.

## 3. Architecture
![Architecture_Diagram](github-content/Architecture_Diagram.png)

**Component responsibilities:**

| Component | Path | Role |
|---|---|---|
| **Backend Agent** | `backend/app/` | FastAPI app. Owns the database, drives the state machine, proxies to Lab Agent. |
| **IPAM** | `backend/app/ipam/service.py` | Carves `IPAM_POOL` into `/24` blocks; allocates/releases atomically with `SELECT … FOR UPDATE`. |
| **ResourceManager** | `backend/app/resources/manager.py` | Enforces `MAX_LABS_PER_HOST`, `CPU_QUOTA`, `MEM_QUOTA_MB`. |
| **Orchestrator** | `backend/app/orchestrator/workflow.py` | The async coroutine `run_lab()` that walks `REQUESTED → … → LAB_READY` (and `destroy_lab()` for cleanup). |
| **HypervisorBackend** | `backend/app/hypervisor/{base,mock,local_ch}.py` | Protocol + impls. `mock` today; `local_ch` ships once the VPS is wired. |
| **Networking** | `backend/app/networking/service.py` | TAP creation / attachment / removal on the host bridge. No-op in mock mode. |
| **Lab Agent client** | `backend/app/lab_agent_client/client.py` | `httpx.AsyncClient` wrapper that calls `/health`, `/status`, `/start`, `/stop` and opens the console WebSocket. |
| **Terminal gateway** | `backend/app/terminal/gateway.py` | Bridges a student browser WebSocket ⇄ the in-VM Lab Agent console WebSocket. |
| **Lab Agent** | `lab_agent/app/` | Tiny FastAPI service that runs inside the microVM. Owns ContainerLab and exposes the console stream. |
| **ContainerLab** | (inside the VM) | Deploys the topology (`vrnetlab` provides vJunos / vMX / cRPD containers). |
| **Cloud Hypervisor** | (the VPS) | Boots microVMs from golden images; exposes an HTTP API socket. Stubbed in this repo for now. |

## 4. Repository layout

```
juniperProject/
├── README.md                          ← you are here
├── Makefile                           ← venv, install, up, down, migrate, backend-bg, agent-bg, test, clean
├── docker-compose.yml                 ← Postgres 16 + volume + healthcheck
├── .env.example                       ← every supported env var
├── scripts/
│   ├── test_ws.py                     ← end-to-end WebSocket smoke test (through the gateway)
│   └── test_ws_direct.py              ← end-to-end WebSocket smoke test (direct to the lab agent)
├── backend/
│   ├── pyproject.toml                 ← package: lab-platform-backend
│   ├── README.md
│   ├── alembic.ini
│   ├── alembic/versions/0001_init.py  ← creates labs, ip_allocations, lab_events
│   ├── app/
│   │   ├── main.py                    ← FastAPI factory + global exception handlers
│   │   ├── config.py                  ← pydantic-settings (env-driven)
│   │   ├── logging.py                 ← structlog + lab_id ContextVar
│   │   ├── db.py                      ← async SQLAlchemy engine/session
│   │   ├── models.py                  ← Lab, IPAllocation, LabEvent ORM
│   │   ├── schemas.py                 ← Pydantic request/response models
│   │   ├── state_machine.py           ← LabStatus enum + transition rules + progress %
│   │   ├── deps.py                    ← FastAPI dependencies (db, settings)
│   │   ├── registry.py                ← lab_type → (golden_image, topology, devices) map
│   │   ├── ipam/service.py            ← IPAMService
│   │   ├── orchestrator/workflow.py   ← run_lab() + destroy_lab()
│   │   ├── hypervisor/{base,mock,local_ch,factory}.py
│   │   ├── networking/service.py      ← TAP plumbing
│   │   ├── lab_agent_client/client.py
│   │   ├── terminal/gateway.py        ← WebSocket ⇄ WebSocket bridge
│   │   ├── resources/manager.py
│   │   ├── events/service.py          ← LabEvent audit trail
│   │   └── api/{labs,health,ws}.py
│   └── tests/
│       ├── conftest.py                ← SQLite per session, autouse cleanup, app/engine swap
│       ├── test_state_machine.py      ← 5 tests
│       ├── test_ipam.py               ← 3 tests
│       ├── test_api_labs.py           ← 7 tests
│       └── test_e2e_workflow.py       ← 3 tests
└── lab_agent/
    ├── pyproject.toml                 ← package: lab-agent
    └── app/
        ├── main.py                    ← FastAPI app factory + lifespan (readiness loop)
        ├── config.py
        ├── routes.py                  ← /health, /status, /start, /stop, /console[/...]
        ├── containerlab.py            ← deploy() / destroy() wrapper (stub mode)
        ├── readiness.py               ← polls per-device readiness every 0.5 s
        ├── state.py                   ← in-memory topology + per-device state
        └── topologies/                ← stub .clab.yml for ospf, bgp, vlan, switching, enterprise
```

## 5. Lab state machine

The Backend Agent is the single source of truth for lab state. Every transition is enforced
by `backend/app/state_machine.py` (`assert_transition()`) and is recorded as a `LabEvent` row
plus a structured log line.

![Lab workflow](github-content/Lab_workflow.png)

**Rules:**

- Any active state may transition to `STOPPING` (so `DELETE` always succeeds even if the lab
  is mid-creation).
- Any failure during creation transitions to `FAILED` with a `FailureReason`
  (`QUOTA_EXCEEDED`, `VM_START_FAILED`, `CONTAINERLAB_FAILED`, `DEVICE_BOOT_FAILED`,
  `HEALTH_CHECK_TIMEOUT`, `NETWORK_CONFIGURATION_FAILED`, `LAB_AGENT_UNREACHABLE`,
  `CLEANUP_FAILED`).
- `DESTROYED` and `FAILED` are terminal (a `FAILED` lab may still be cleaned up via `DELETE`).
- `progress_pct()` returns 0–100 for the API's `progress` field.

## 6. End-to-end guide: provisioning Cloud Hypervisor + ContainerLab + Juniper

This is the "real" workflow the backend automates once `LocalCloudHypervisorBackend` is
implemented. You can run it today on a Linux host with KVM access.

### 6.1. Prerequisites

A Linux VPS (Ubuntu 22.04+ or similar) with:

```bash
# KVM + Cloud Hypervisor
sudo apt update
sudo apt install -y qemu-kvm libvirt-daemon-system cloud-image-utils
# cloud-hypervisor binary — grab the latest release:
curl -L https://github.com/cloud-hypervisor/cloud-hypervisor/releases/latest/download/cloud-hypervisor -o /usr/local/bin/cloud-hypervisor
chmod +x /usr/local/bin/cloud-hypervisor
cloud-hypervisor --version

# A Linux bridge (CH attaches microVM TAPs to it)
sudo ip link add br0 type bridge
sudo ip addr add 172.30.0.1/16 dev br0
sudo ip link set br0 up

# Enable IP forwarding so microVMs can reach the lab network
sudo sysctl -w net.ipv4.ip_forward=1
```

### 6.2. Build the golden image

A *golden image* is a root disk that already contains Docker, ContainerLab, vrnetlab, the
Juniper image files, and the Lab Agent. The backend boots a per-lab copy-on-write overlay off
this image so the golden itself is never mutated.

```bash
# Create a 20 GB empty disk
qemu-img create -f qcow2 /var/lib/cloud-hypervisor/images/ospf.qcow2 20G

# Install Ubuntu cloud image into it (seed with cloud-init user-data that installs everything)
wget https://cloud-images.ubuntu.com/jammy/current/jammy-server-cloudimg-amd64.img \
     -O /tmp/ubuntu-base.img
qemu-img convert -f qcow2 -O qcow2 /tmp/ubuntu-base.img /var/lib/cloud-hypervisor/images/ospf.qcow2

# Boot once with virt-customize (or run an installer) to:
#   · install docker, containerlab, vrnetlab (pip install)
#   · drop your Juniper image tarballs into /opt/vrnetlab/<lab-type>/
#   · copy the Lab Agent code into /opt/lab-agent and run it on boot (systemd unit)
#   · drop a stub topology at /opt/lab-agent/topologies/ospf.clab.yml
virt-customize -a /var/lib/cloud-hypervisor/images/ospf.qcow2 \
    --run-command 'curl -fsSL https://get.docker.com | sh' \
    --run-command 'pip install containerlab vrnetlab' \
    --run-command 'systemctl enable --now lab-agent.service' \
    --upload lab_agent/app:/opt/lab-agent/app \
    --upload lab_agent/topologies/ospf.clab.yml:/opt/lab-agent/topologies/ospf.clab.yml
```

The resulting `ospf.qcow2` is your *golden image*. Repeat for `bgp.qcow2`, `vlan.qcow2`, etc.
— one per `lab_type` in `backend/app/registry.py`.

### 6.3. Configure networking

Each lab gets its own TAP attached to `br0`:

```bash
# Hand-rolled example (the backend does this in networking/service.py):
sudo ip tuntap add dev tap-lab-<id> mode tap
sudo ip link set tap-lab-<id> master br0
sudo ip link set tap-lab-<id> up

# Tear down (DELETE /labs):
sudo ip link set tap-lab-<id> nomaster
sudo ip link delete tap-lab-<id>
```

The microVM's eth0 ends up on `br0`, with `vm_ip = <subnet>.10` and `gateway = <subnet>.1`.
ContainerLab then attaches its own internal bridges to `eth0`-side veths.

### 6.4. Boot the microVM with cloud-hypervisor

This is roughly what `LocalCloudHypervisorBackend.start()` will produce (read the stub in
`backend/app/hypervisor/local_ch.py` for the exact signature):

```bash
LAB_ID=lab-0123abcd
TAP=tap-${LAB_ID}
IMAGE=/var/lib/cloud-hypervisor/images/ospf.qcow2
KERNEL=/var/lib/cloud-hypervisor/vmlinux
API_SOCK=/var/lib/cloud-hypervisor/api-sockets/${LAB_ID}.sock
DISK=/var/lib/cloud-hypervisor/runtime-disks/${LAB_ID}.qcow2  # overlay

# Copy-on-write overlay so the golden image is never touched
qemu-img create -f qcow2 -b ${IMAGE} -F qcow2 ${DISK}

cloud-hypervisor \
  --api-socket path=${API_SOCK} \
  --kernel ${KERNEL} \
  --disk path=${DISK} \
  --cpus boot=4 \
  --memory size=4G \
  --net tap=${TAP},mac=52:54:00:12:34:56 \
  --serial tty \
  --console off \
  --rng src=/dev/urandom \
  --watchdog disabled

# (CH is a long-running process — talk to it via its REST API on ${API_SOCK} for VM info,
#  power-button, shutdown, etc.)
```

Once booted, the guest's user-data runs `lab-agent.service` which starts the Lab Agent on
port 9001 inside the VM.

### 6.5. Inside the microVM: run ContainerLab + vrnetlab + Juniper

The Lab Agent inside the VM exposes:

```
GET  /health                    # liveness
GET  /status                    # current state, per-device readiness
POST /start   {lab_type, topology_path}
POST /stop
WS   /console/{device}          # streams the Juniper console bytes
```

A real Juniper topology (`ospf.clab.yml`) looks like:

```yaml
name: ospf
topology:
  nodes:
    r1:
      kind: juniper_vjunos-router
      image: vrnetlab/vrnetlab-vjunos-router:22.4R1
    r2:
      kind: juniper_vjunos-router
      image: vrnetlab/vrnetlab-vjunos-router:22.4R1
    r3:
      kind: juniper_vjunos-router
      image: vrnetlab/vrnetlab-vjunos-router:22.4R1
  links:
    - endpoints: ["r1:eth1", "r2:eth1"]
    - endpoints: ["r2:eth2", "r3:eth1"]
    - endpoints: ["r3:eth2", "r1:eth2"]
```

The Backend Agent POSTs `/start` with `{lab_type: "ospf", topology_path: "/opt/lab-agent/topologies/ospf.clab.yml"}`.
The Lab Agent runs:

```bash
containerlab deploy -t /opt/lab-agent/topologies/ospf.clab.yml
```

vrnetlab then provisions three vJunos-router containers (`clab-ospf-r1`, `clab-ospf-r2`,
`clab-ospf-r3`), each running a real Junos VM reachable on `22` (SSH) and `830` (NETCONF).
The readiness loop polls per-device until all three are up, then the backend transitions the
lab to `LAB_READY`.

### 6.6. Attach a console

From the Backend Agent host:

```bash
# SSH to the Lab Agent, then docker exec into a container:
ssh -p 9022 labagent@<vm_ip>
docker exec -it clab-ospf-r1 cli   # vJunos: enters the Junos CLI
# Or, from the student browser, the WebSocket gateway at
#   ws://backend:8000/api/v1/labs/<lab_id>/terminal?device=r1
#   pipes the same console bytes through to the in-VM Lab Agent.
```

### 6.7. Wire it into the backend

When the VPS is online, point the backend at it and flip the backend flag:

```bash
# .env (or environment)
HYPERVISOR_BACKEND=local_ch
CH_BINARY=/usr/local/bin/cloud-hypervisor
CH_KERNEL=/var/lib/cloud-hypervisor/vmlinux
CH_IMAGE_DIR=/var/lib/cloud-hypervisor/images
CH_BRIDGE=br0
CH_TAP_PREFIX=tap
CH_DISK_DIR=/var/lib/cloud-hypervisor/runtime-disks
CH_API_SOCKET_DIR=/var/lib/cloud-hypervisor/api-sockets
LAB_AGENT_BASE_URL=http://<vm_ip_or_bridge_addr>:9001
```

Implement `backend/app/hypervisor/local_ch.py` against the real `cloud-hypervisor` API
(socket at `$CH_API_SOCKET_DIR/<lab_id>.sock`) using the `ch-remote` REST surface:
`PUT /vm.info`, `PUT /vm.power-button`, `PUT /vm.shutdown`, etc. No other backend code needs
to change.

## 7. Quickstart: backend on your workstation

This runs the entire backend with the **mock** hypervisor. No KVM, no Cloud Hypervisor, no
real VMs — just the Backend Agent, Postgres, and the reference Lab Agent (both as FastAPI
processes on the same machine).

```bash
# 1. Install Python deps into .venv (PEP 668 safe)
make install

# 2. Start Postgres
cp .env.example .env
make up
# (wait for "healthy")

# 3. Apply migrations
make migrate

# 4. Start backend + lab agent in the background
make backend-bg     # logs → var/backend.log
make agent-bg       # logs → var/agent.log

# 5. Smoke test
curl -s http://localhost:8000/healthz
# → {"status":"ok"}

curl -s -XPOST http://localhost:8000/api/v1/labs \
  -H 'content-type: application/json' \
  -d '{"lab_type":"ospf","cpu":4,"memory":"4G"}'
# → {"lab_id":"lab-<12-hex>","status":"REQUESTED", ...}

# Poll status — it walks REQUESTED → … → LAB_READY in ~7 s
curl -s http://localhost:8000/api/v1/labs/lab-<id>

# Tear down
curl -s -XDELETE http://localhost:8000/api/v1/labs/lab-<id>
# → status walks STOPPING → VM_STOPPED → RESOURCES_RELEASED → DESTROYED

# Tidy up
make clean
```

## 8. API reference

Base URL: `http://<host>:8000`. All bodies are JSON. All errors use the envelope:

```json
{ "error": { "code": "MACHINE_READABLE_CODE", "message": "human readable", "details": null } }
```

| Verb | Path | Purpose |
|---|---|---|
| `POST` | `/api/v1/labs` | Create a lab. Async — returns `201` immediately with `status=REQUESTED`; the orchestrator continues in the background. |
| `GET` | `/api/v1/labs` | List labs (optional `?status=LAB_READY&limit=50`). Newest first. |
| `GET` | `/api/v1/labs/{lab_id}` | Single lab status + progress + network info. |
| `DELETE` | `/api/v1/labs/{lab_id}` | Tear down a lab. Async — returns `202` with the *current* state; lifecycle continues. |
| `WS` | `/api/v1/labs/{lab_id}/terminal?device=r1` | Bidirectional console stream. Only opens if status is `LAB_READY`; otherwise `4409 close`. |
| `GET` | `/healthz` | Liveness (process up). |
| `GET` | `/readyz` | Readiness: Postgres reachable AND Lab Agent reachable. |

### `POST /api/v1/labs`

Request body:

```json
{
  "lab_type": "ospf",            // one of: ospf, bgp, vlan, switching, enterprise
  "cpu": 4,                      // 1..64, default 4
  "memory": "4G",                // <int>[MG], e.g. "4G", "8192M"
  "user_id": "student-42"        // optional
}
```

Successful response (`201 Created`):

```json
{
  "lab_id": "lab-ba0680ce0368",
  "user_id": "student-42",
  "lab_type": "ospf",
  "golden_image": "ospf.qcow2",
  "status": "REQUESTED",
  "progress": 5,
  "cpu": 4,
  "memory_mb": 4096,
  "subnet": null, "gateway": null, "vm_ip": null, "vm_pid": null, "tap_name": null,
  "error": null,
  "created_at": "2026-08-26T12:40:05.493685Z",
  "started_at": null, "ready_at": null, "terminated_at": null
}
```

Errors:

| Code | HTTP | When |
|---|---|---|
| `VALIDATION_ERROR` | 400 | bad `lab_type`, `cpu` out of range, malformed `memory`, etc. |
| `INVALID_LAB_TYPE` | 400 | `lab_type` not in the registry. |
| `QUOTA_EXCEEDED` | 409 | `MAX_LABS_PER_HOST` / `CPU_QUOTA` / `MEM_QUOTA_MB` would be exceeded. |

### `GET /api/v1/labs/{lab_id}`

Successful response (`200 OK`):

```json
{
  "lab_id": "lab-ba0680ce0368",
  "user_id": null,
  "lab_type": "ospf",
  "golden_image": "ospf.qcow2",
  "status": "LAB_READY",
  "progress": 100,
  "cpu": 4, "memory_mb": 4096,
  "subnet": "172.30.4.0/24",
  "gateway": "172.30.4.1",
  "vm_ip": "172.30.4.10",
  "vm_pid": 15441001,
  "tap_name": "tap-lab-ba0680ce",
  "error": null,
  "created_at": "2026-08-26T12:40:05.493685Z",
  "started_at": "2026-08-26T12:40:05.947321Z",
  "ready_at": "2026-08-26T12:40:12.282715Z",
  "terminated_at": null
}
```

Errors: `INVALID_ID` (`400`), `LAB_NOT_FOUND` (`404`).

### `GET /api/v1/labs`

`200 OK` with an array of `LabResponse` (newest first; capped by `?limit=`).

### `DELETE /api/v1/labs/{lab_id}`

`202 Accepted` with the lab's *current* state (orchestrator continues). Calling on an already
`DESTROYED` lab returns `200` with that lab's record.

### `WS /api/v1/labs/{lab_id}/terminal?device=r1`

- `101 Switching Protocols` if the lab is `LAB_READY`. Bytes flow bidirectionally.
- `4404 close` if the lab doesn't exist.
- `4409 close` if the lab exists but is not `LAB_READY`.
- `1011 close` on internal error.

The `device` query parameter is optional — defaults to the first device of the topology.

## 9. Configuration

All configuration is environment-driven. See `.env.example` for the authoritative list.

| Variable | Default | Meaning |
|---|---|---|
| `APP_NAME` | `lab-platform-backend` | Cosmetic, used in logs. |
| `LOG_LEVEL` | `INFO` | structlog level (`DEBUG`, `INFO`, `WARNING`, `ERROR`). |
| `DATABASE_URL` | `postgresql+asyncpg://lab:lab@localhost:5432/labplatform` | Async SQLAlchemy URL. |
| `DATABASE_URL_SYNC` | `postgresql://lab:lab@localhost:5432/labplatform` | Sync URL used by Alembic. |
| `IPAM_POOL` | `172.30.0.0/16` | Pool carved into per-lab subnets. |
| `IPAM_PREFIX_LEN` | `24` | Per-lab subnet size. |
| `MAX_LABS_PER_HOST` | `10` | Max concurrent active labs. |
| `CPU_QUOTA` | `64` | Aggregate vCPU cap across active labs. |
| `MEM_QUOTA_MB` | `131072` | Aggregate memory cap (MiB) across active labs. |
| `HYPERVISOR_BACKEND` | `mock` | `mock` (in-process simulator) or `local_ch` (real VPS — implement `local_ch.py`). |
| `CH_BRIDGE` | `br0` | Linux bridge the microVM's TAP attaches to. |
| `CH_TAP_PREFIX` | `tap` | TAP interface name prefix (e.g. `tap-lab-abc123`). |
| `CH_DISK_DIR` | `./var/runtime-disks` | Where per-lab copy-on-write overlay disks live. |
| `CH_API_SOCKET_DIR` | `./var/ch-sockets` | Where CH per-VM API sockets live. |
| `CH_BINARY` | `/usr/local/bin/cloud-hypervisor` | (local_ch only) CH binary path. |
| `CH_KERNEL` | `/var/lib/cloud-hypervisor/vmlinux` | (local_ch only) Direct-kernel boot path. |
| `CH_IMAGE_DIR` | `/var/lib/cloud-hypervisor/images` | (local_ch only) Golden-image directory. |
| `LAB_AGENT_BASE_URL` | `http://127.0.0.1:9001` | URL of the in-VM Lab Agent. |
| `DEVICE_READINESS_TIMEOUT_SEC` | `600` | How long the orchestrator waits for ContainerLab devices. |
| `DEVICE_READINESS_POLL_SEC` | `5` | Readiness poll interval. |
| `BACKEND_HOST` | `0.0.0.0` | Bind address for uvicorn. |
| `BACKEND_PORT` | `8000` | Bind port for uvicorn. |

## 10. Logging

`structlog` + a per-request `lab_id` `ContextVar`. Every log line emitted during a lab's
lifecycle carries `lab_id` automatically.

In dev (`LOG_LEVEL=DEBUG`), the console renderer is used:

```
2026-08-26 12:40:05 [info] orchestrator.transition  lab_id=lab-ba0680ce0368  from=REQUESTED  to=CREATING
2026-08-26 12:40:06 [info] ipam.allocate             lab_id=lab-ba0680ce0368  subnet=172.30.4.0/24  vm_ip=172.30.4.10
2026-08-26 12:40:06 [info] networking.create_tap     lab_id=lab-ba0680ce0368  tap=tap-lab-ba0680ce
2026-08-26 12:40:07 [info] vm.start (mock)           lab_id=lab-ba0680ce0368  pid=15441001
2026-08-26 12:40:08 [info] vm.ready (mock)           lab_id=lab-ba0680ce0368  pid=15441001
2026-08-26 12:40:09 [info] containerlab.start        lab_id=lab-ba0680ce0368  lab_type=ospf
2026-08-26 12:40:12 [info] devices.ready             lab_id=lab-ba0680ce0368  devices=['r1','r2','r3']
```

In production, set `LOG_LEVEL=INFO` (or higher) to get JSON output suitable for ingestion
into ELK / Loki / Cloud Logging.

## 11. Testing — input/output validation (backend only)

**No Cloud Hypervisor required.** Every command below runs against the Backend Agent with the
in-process `MockHypervisorBackend` and the reference Lab Agent on `:9001`. Make sure both are
up (`make backend-bg && make agent-bg`) before running.

### 11.1. Health

```bash
$ curl -s http://localhost:8000/healthz
{"status":"ok"}
```

### 11.2. Readiness (DB + Lab Agent reachable)

```bash
$ curl -s http://localhost:8000/readyz
{"status":"ready","database":"ok","lab_agent":"ok"}
```

### 11.3. Empty lab list

```bash
$ curl -s http://localhost:8000/api/v1/labs
[]
```

### 11.4. Happy path: create → poll → LAB_READY

```bash
$ curl -s -XPOST http://localhost:8000/api/v1/labs \
    -H 'content-type: application/json' \
    -d '{"lab_type":"ospf","cpu":4,"memory":"4G"}'
{"lab_id":"lab-ba0680ce0368","user_id":null,"lab_type":"ospf","golden_image":"ospf.qcow2","status":"REQUESTED","progress":5,"cpu":4,"memory_mb":4096,"subnet":null,"gateway":null,"vm_ip":null,"vm_pid":null,"tap_name":null,"error":null,"created_at":"2026-08-26T12:40:05.493685Z","started_at":null,"ready_at":null,"terminated_at":null}

# Poll immediately, then again ~3 s later, then ~7 s later:
$ curl -s http://localhost:8000/api/v1/labs/lab-ba0680ce0368
{"lab_id":"lab-ba0680ce0368",... "status":"CREATING","progress":10, ...}

$ curl -s http://localhost:8000/api/v1/labs/lab-ba0680ce0368
{"lab_id":"lab-ba0680ce0368",... "status":"DEVICES_BOOTING","progress":85,
 "subnet":"172.30.4.0/24","gateway":"172.30.4.1","vm_ip":"172.30.4.10",
 "vm_pid":15441001,"tap_name":"tap-lab-ba0680ce", ...}

$ curl -s http://localhost:8000/api/v1/labs/lab-ba0680ce0368
{"lab_id":"lab-ba0680ce0368",... "status":"LAB_READY","progress":100,
 "ready_at":"2026-08-26T12:40:12.282715Z", ...}
```

### 11.5. Unknown `lab_type` → 400

```bash
$ curl -s -XPOST http://localhost:8000/api/v1/labs \
    -H 'content-type: application/json' \
    -d '{"lab_type":"mpls","cpu":4,"memory":"4G"}'
{"error":{"code":"VALIDATION_ERROR","message":"request validation failed",
  "details":[{"type":"value_error","loc":["body","lab_type"],
   "msg":"Value error, lab_type must be one of ['bgp', 'enterprise', 'ospf', 'switching', 'vlan']",
   "input":"mpls",
   "ctx":{"error":"lab_type must be one of ['bgp', 'enterprise', 'ospf', 'switching', 'vlan']"}}]}}
```

### 11.6. Invalid `memory` (number instead of string) → 400

```bash
$ curl -s -XPOST http://localhost:8000/api/v1/labs \
    -H 'content-type: application/json' \
    -d '{"lab_type":"ospf","cpu":4,"memory":4096}'
{"error":{"code":"VALIDATION_ERROR","message":"request validation failed",
  "details":[{"type":"string_type","loc":["body","memory"],
   "msg":"Input should be a valid string","input":4096}]}}
```

### 11.7. Destroyed record (full lifecycle timestamps)

```bash
$ curl -s http://localhost:8000/api/v1/labs/lab-ba0680ce0368
{"lab_id":"lab-ba0680ce0368","user_id":null,"lab_type":"ospf","golden_image":"ospf.qcow2",
 "status":"DESTROYED","progress":100,"cpu":4,"memory_mb":4096,
 "subnet":"172.30.4.0/24","gateway":"172.30.4.1","vm_ip":"172.30.4.10",
 "vm_pid":15441001,"tap_name":"tap-lab-ba0680ce","error":null,
 "created_at":"2026-08-26T12:40:05.493685Z",
 "started_at":"2026-08-26T12:40:05.947321Z",
 "ready_at":"2026-08-26T12:40:12.282715Z",
 "terminated_at":"2026-08-26T12:40:42.765730Z"}
```

### 11.8. Unknown lab → 404

```bash
$ curl -s http://localhost:8000/api/v1/labs/lab-does-not-exist
{"error":{"code":"LAB_NOT_FOUND","message":"no lab lab-does-not-exist"}}
```

### 11.9. Destroy (async)

```bash
$ curl -s -XDELETE http://localhost:8000/api/v1/labs/lab-ba0680ce0368
{"lab_id":"lab-ba0680ce0368",... "status":"LAB_READY","progress":100, ...}
# (note: the orchestrator runs in the background; poll again in a couple seconds)
$ curl -s http://localhost:8000/api/v1/labs/lab-ba0680ce0368
{"lab_id":"lab-ba0680ce0368",... "status":"DESTROYED","progress":100,
 "terminated_at":"2026-08-26T12:40:42.765730Z", ...}
```

### 11.10. WebSocket terminal gateway

```bash
$ .venv/bin/python scripts/test_ws.py lab-ba0680ce0368
RECV: '\r\n*** JUNOS stub console for r1 ***\r\nlab-type: ospf\r\nlogin: '
RECV: '\r\nr1# show interfaces terse\r\n'
```

The first `RECV` is the banner pushed by the Lab Agent as soon as the gateway opens the
upstream WebSocket. The second is the echoed command — the gateway pumped your `send` back
through the Lab Agent, which echoed it as a fake Junos prompt.

### 11.11. Pytest

```bash
$ make test
# or, directly:
$ cd backend && ../.venv/bin/python -m pytest -x -q
backend/tests/test_state_machine.py::test_terminal_statuses PASSED
backend/tests/test_state_machine.py::test_happy_path_transitions PASSED
backend/tests/test_state_machine.py::test_illegal_transition PASSED
backend/tests/test_state_machine.py::test_failed_can_be_cleaned_up PASSED
backend/tests/test_state_machine.py::test_progress_monotonic PASSED
backend/tests/test_ipam.py::test_allocate_returns_distinct_subnets PASSED
backend/tests/test_ipam.py::test_allocate_then_release_then_reuse PASSED
backend/tests/test_ipam.py::test_soft_release_marks_row PASSED
backend/tests/test_api_labs.py::test_create_lab_returns_201_and_lab_id PASSED
backend/tests/test_api_labs.py::test_create_lab_invalid_type_422 PASSED
backend/tests/test_api_labs.py::test_create_lab_bad_memory_422 PASSED
backend/tests/test_api_labs.py::test_get_lab_404 PASSED
backend/tests/test_api_labs.py::test_list_labs_includes_recently_created PASSED
backend/tests/test_api_labs.py::test_health_endpoints PASSED
backend/tests/test_api_labs.py::test_delete_lab_transitions_to_stopping PASSED
backend/tests/test_e2e_workflow.py::test_full_lifecycle_to_lab_ready PASSED
backend/tests/test_e2e_workflow.py::test_destroy_cleans_up PASSED
backend/tests/test_e2e_workflow.py::test_device_boot_timeout_marks_failed PASSED
18 passed in ~10s
```

## 12. Adding a new lab type

1. Add a topology file: `lab_agent/topologies/<lab_type>.clab.yml` (use the existing
   `ospf.clab.yml` as a template — swap the nodes and links for whatever your new lab needs;
   `kind` may be `linux`, `juniper_vjunos-router`, `juniper_vjunos-switch`, etc.).
2. Add the entry to `backend/app/registry.py`:

   ```python
   "mpls": LabTypeConfig(
       name="mpls",
       golden_image="mpls.qcow2",
       topology_file="mpls.clab.yml",
       default_devices=("r1", "r2", "r3"),
   ),
   ```

3. Add it to the allow-list in `backend/app/schemas.py` (`_LAB_TYPES`) so Pydantic accepts it.
4. (Real path) Build a `mpls.qcow2` golden image following [§6.2](#62-build-the-golden-image).

That's it — `POST /api/v1/labs { "lab_type": "mpls" }` will now work.

## 13. License

Internal project. See your organization's standard internal-software notice.