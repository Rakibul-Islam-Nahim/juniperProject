# Automated MicroVM Network Lab Platform

An on-demand, isolated network-lab platform that boots **Juniper routers and switches**
inside **Cloud Hypervisor microVMs** running **ContainerLab**, then gives each student a
private, console-attached lab they can tear down with a single API call.

```
┌────────────────────────────────────────────────────────────────────┐
│  Student Browser                                                   │
│     │  POST /api/v1/labs                                           │
│     │  GET  /api/v1/labs/{id}                                      │
│     │  WS   /api/v1/labs/{id}/terminal?device=r1                   │
│     │  DELETE /api/v1/labs/{id}                                    │
└──────────────┬─────────────────────────────────────────────────────┘
               │
               ▼
┌────────────────────────────────────────────────────────────────────┐
│  Backend Agent    :8000   (Docker, privileged, host network)       │
│  FastAPI · state machine · REST + WebSocket · calls IPAM via HTTP  │
└──────────────┬─────────────────────────────────────────────────────┘
               │   HTTP /allocate, /release
               ▼
┌────────────────────────────────────────────────────────────────────┐
│  IPAM Service     :8100   (Docker)                                 │
│  FastAPI · owns the ip_allocations table                           │
└──────────────┬─────────────────────────────────────────────────────┘
               │   asyncpg
               ▼
┌────────────────────────────────────────────────────────────────────┐
│  PostgreSQL 16    :5432   (Docker, persistent volume)              │
└────────────────────────────────────────────────────────────────────┘

                ┌──────────────────────────────┐
                │ MicroVM (per student lab)    │ ← cloud-hypervisor
                │  Lab Agent :9001             │
                │  ContainerLab                │
                │  vJunos / vMX / cRPD         │
                └──────────────────────────────┘
```

---

## Table of contents

1. [Introduction](#1-introduction)
2. [Repository layout](#2-repository-layout)
3. [Architecture](#3-architecture)
4. [Lab lifecycle — state machine](#4-lab-lifecycle--state-machine)
5. [Quickstart — full stack with Docker](#5-quickstart--full-stack-with-docker)
6. [Quickstart — native development (no Docker)](#6-quickstart--native-development-no-docker)
7. [REST API reference](#7-rest-api-reference)
   - 7.1 [Backend Agent](#71-backend-agent-port-8000)
   - 7.2 [IPAM Service](#72-ipam-service-port-8100)
8. [WebSocket API reference](#8-websocket-api-reference)
9. [Configuration reference](#9-configuration-reference)
10. [Operational commands](#10-operational-commands)
11. [Testing](#11-testing)
12. [Adding a new lab type](#12-adding-a-new-lab-type)
13. [Troubleshooting](#13-troubleshooting)
14. [License](#14-license)

---

## 1. Introduction

Networking students and engineers need realistic, isolated hands-on labs. Physical gear
doesn't scale, full-router simulators on the host don't isolate per-student state, and
naive Docker-based topologies miss the real Junos boot path.

This platform solves that by **running every student's lab inside its own microVM**
booted from a pre-baked *golden image* — a Cloud Hypervisor root disk containing Docker,
ContainerLab, vrnetlab, and the relevant Juniper image files. Each lab gets:

- A dedicated `/24` carved out of the platform's IPAM pool.
- A TAP interface plumbed onto the host bridge so the microVM has L2 connectivity.
- A private ContainerLab topology the student can SSH / console into.
- A hard kill-switch (`DELETE /labs/{id}`) that releases every resource, top-to-bottom.

The student only ever sees the **Backend Agent API**. They never need to know what
hypervisor runs underneath.

**Status:** MVP backend. The hypervisor is pluggable — `mock` for laptops, `local_ch`
for the production Cloud Hypervisor VPS. The IPAM service runs as a separate
microservice so address management can be deployed and scaled independently.

---

## 2. Repository layout

```
juniperProject/
├── README.md                          ← you are here
├── Makefile                           ← install / up / down / migrate / test / clean
├── docker-compose.yml                 ← postgres + ipam-service + backend
├── .env / .env.example                ← every supported env var
├── document.md                        ← historical / long-form notes
│
├── backend/                           ← Backend Agent (FastAPI)
│   ├── pyproject.toml                 ← package: lab-platform-backend
│   ├── Dockerfile                     ← container image
│   ├── .dockerignore
│   ├── README.md
│   ├── alembic.ini
│   ├── alembic/versions/0001_init.py  ← creates labs, ip_allocations, lab_events
│   ├── app/
│   │   ├── main.py                    ← FastAPI factory, lifespan, error handlers
│   │   ├── config.py                  ← Settings (env-driven)
│   │   ├── db.py                      ← async engine
│   │   ├── deps.py                    ← DB session dep
│   │   ├── models.py                  ← ORM: Lab, IPAllocation, LabEvent
│   │   ├── schemas.py                 ← Pydantic request/response
│   │   ├── state_machine.py           ← Lab lifecycle FSM
│   │   ├── logging.py                 ← structlog + lab_id contextvar
│   │   ├── registry.py                ← lab_type → golden_image
│   │   ├── api/                       ← REST + WebSocket routes
│   │   │   ├── health.py              ← /healthz, /readyz
│   │   │   ├── labs.py                ← POST/GET/DELETE /api/v1/labs
│   │   │   └── ws.py                  ← WS terminal gateway
│   │   ├── events/service.py          ← transition_status, record_event
│   │   ├── hypervisor/                ← VM lifecycle abstraction
│   │   │   ├── base.py                ← ABC + VMHandle
│   │   │   ├── mock.py                ← in-process mock
│   │   │   ├── local_ch.py            ← real Cloud Hypervisor launcher
│   │   │   └── factory.py             ← Settings → backend
│   │   ├── ipam_client/client.py      ← HTTP client → ipam-service
│   │   ├── lab_agent_client/client.py ← HTTP client → Lab Agent in VM
│   │   ├── networking/service.py      ← TAP/bridge management
│   │   ├── orchestrator/workflow.py   ← run_lab() / destroy_lab() coroutines
│   │   ├── resources/manager.py       ← quota enforcement
│   │   └── terminal/gateway.py        ← browser ⇄ Lab Agent WS bridge
│   └── tests/                         ← pytest suite
│
├── ipam_service/                      ← Standalone IPAM microservice (FastAPI)
│   ├── pyproject.toml                 ← package: lab-ipam-service
│   ├── Dockerfile
│   ├── .dockerignore
│   ├── README.md
│   └── app/
│       ├── main.py                    ← FastAPI /allocate, /release, /status
│       ├── config.py                  ← Settings (pool, prefix_len, db)
│       ├── db.py                      ← async engine
│       ├── models.py                  ← IPAllocation ORM
│       └── service.py                 ← IPAMService class
│
├── lab_agent/                         ← In-microVM service (unchanged, not in Docker)
│   ├── pyproject.toml
│   ├── README.md
│   ├── app/
│   │   ├── main.py                    ← lifespan autodeploys topology
│   │   ├── config.py                  ← topology_path, readiness settings
│   │   ├── containerlab.py            ← containerlab CLI wrapper
│   │   ├── state.py                   ← in-memory StateStore
│   │   ├── readiness.py               ← TCP probe loop
│   │   ├── routes.py                  ← /health, /status, /start, /stop, /console
│   │   └── logging.py
│   └── topologies/                    ← 5 YAML lab topologies (alpine stubs)
│       ├── bgp.clab.yml
│       ├── enterprise.clab.yml
│       ├── ospf.clab.yml
│       ├── switching.clab.yml
│       └── vlan.clab.yml
│
├── scripts/                           ← WebSocket smoke tests
│   ├── test_ws.py                     ← through backend gateway
│   └── test_ws_direct.py              ← direct to Lab Agent
│
├── github-content/                    ← PNG diagrams (architecture, workflow)
├── var/                               ← runtime artifacts (gitignored)
│   ├── backend.log
│   ├── ipam.log
│   ├── agent.log
│   ├── ch-sockets/                    ← cloud-hypervisor API sockets
│   ├── ch-logs/                       ← cloud-hypervisor stdout/stderr
│   └── runtime-disks/                 ← per-lab qcow2 overlays
└── .venv/                             ← Python virtualenv
```

---

## 3. Architecture

### Components

| Component | Path | Role |
|---|---|---|
| **Backend Agent** | `backend/app/` | FastAPI on :8000. Owns the `labs` + `lab_events` tables. Drives the state machine. Proxies to Lab Agent (HTTP + WebSocket) and to the IPAM service (HTTP). |
| **IPAM Service** | `ipam_service/app/` | FastAPI on :8100. **Exclusively owns** the `ip_allocations` table. The backend never touches that table directly. |
| **Lab Agent** | `lab_agent/app/` | FastAPI on :9001, runs *inside* each microVM. Owns ContainerLab, exposes `/health`, `/status`, `/start`, `/stop`, and `/console/{device?}` WebSocket. |
| **Postgres** | docker-compose | Single source of truth for lab metadata. Both services share it but write disjoint tables. |
| **Cloud Hypervisor** | host binary | Launches microVMs from golden images; backend talks to its HTTP API over a per-lab unix socket. |
| **Networking** | `backend/app/networking/` | TAP creation + bridge attachment via `ip` command. No-op in `mock` hypervisor mode. |

### Service boundaries

```
Backend ──HTTP──▶ IPAM (allocate / release)
   │
   ├──HTTP──▶ Lab Agent in VM (health / status / start / stop)
   ├──WS────▶ Lab Agent in VM (console bridge)
   ├──exec──▶ /usr/local/bin/cloud-hypervisor
   └──exec──▶ ip tuntap / ip link  (TAP creation)
```

The Backend is the only thing that touches `cloud-hypervisor` or the host networking
stack. That's why the backend container runs with `--privileged` and `network_mode: host`.

### Trust model

| Surface | Trust | Reason |
|---|---|---|
| Backend Agent | Trusted | Drives everything; talks to CH, IPAM, and per-lab Lab Agents |
| IPAM Service | Trusted | Tiny DB-backed address allocator |
| Lab Agent in VM | Semi-trusted | Runs the student's lab; not exposed to other students |
| MicroVM | Untrusted | One per student; isolated by TAP + bridge + per-VM `/24` |

---

## 4. Lab lifecycle — state machine

Every lab moves through a strict, auditable FSM. The backend records each transition
in `lab_events` (from_status, to_status, message, created_at).

```
   ┌──────────┐
   │REQUESTED │ ← POST /api/v1/labs inserts row, kicks off run_lab()
   └────┬─────┘
        ▼
   ┌──────────┐
   │CREATING  │ ← resource quota check
   └────┬─────┘
        ▼
   ┌───────────────────┐
   │NETWORK_ALLOCATED  │ ← HTTP POST /allocate to ipam-service
   └────┬──────────────┘
        ▼
   ┌──────────┐
   │VM_STARTING│ ← cloud-hypervisor --net tap=tap-<lab_id>
   └────┬─────┘
        ▼
   ┌──────────┐
   │ VM_READY │ ← poll Lab Agent /health at http://<vm_ip>:9001/health
   └────┬─────┘
        ▼
   ┌─────────────────────┐
   │CONTAINERLAB_STARTING│ ← Lab Agent auto-deploys topology on its own boot
   └────┬────────────────┘
        ▼
   ┌─────────────────┐
   │DEVICES_BOOTING  │ ← poll /status until all devices ready
   └────┬────────────┘
        ▼
   ┌──────────┐
   │ LAB_READY│ ← 100% — student can connect
   └────┬─────┘
        │  DELETE /api/v1/labs/{id}
        ▼
   ┌──────────┐
   │ STOPPING │
   └────┬─────┘
        ▼
   ┌────────────┐
   │VM_STOPPED  │ ← cloud-hypervisor /vm.shutdown
   └────┬───────┘
        ▼
   ┌────────────────────┐
   │RESOURCES_RELEASED  │ ← HTTP POST /release to ipam-service + TAP removal
   └────┬───────────────┘
        ▼
   ┌──────────┐
   │DESTROYED │ ← terminal
   └──────────┘

   (any state) ──on error──▶ ┌──────────┐
                              │  FAILED  │ ← terminal; auto-cleanup
                              └──────────┘
```

### Progress percentages (returned by `/api/v1/labs/{id}`)

| Status | Progress |
|---|---|
| REQUESTED | 5 |
| CREATING | 10 |
| NETWORK_ALLOCATED | 20 |
| VM_STARTING | 35 |
| VM_READY | 55 |
| CONTAINERLAB_STARTING | 70 |
| DEVICES_BOOTING | 85 |
| LAB_READY | 100 |
| STOPPING | 95 |
| VM_STOPPED | 50 |
| RESOURCES_RELEASED | 25 |
| DESTROYED | 100 |
| FAILED | 100 |

### Timing knobs

| Setting | Default | Meaning |
|---|---|---|
| `DEVICE_READINESS_TIMEOUT_SEC` | **1800** | How long the backend waits for all devices in the topology to become reachable. Juniper routers in vrnetlab take ~15 minutes to boot — do not lower this without testing. |
| `DEVICE_READINESS_POLL_SEC` | 5 | Polling interval for `/status`. |
| `LAB_AGENT_BASE_URL` | `http://172.30.0.10:9001` | Bootstrap URL for the **first** lab. Subsequent labs use `http://<their_own_vm_ip>:9001` (per-lab client built in `_wait_for_devices_ready`). |

---

## 5. Quickstart — full stack with Docker

This is the recommended deployment. Everything runs in containers except the
hypervisor binary and golden images, which live on the host.

### Prerequisites

- Docker 24+ and docker compose v2
- A Linux host with KVM (`/dev/kvm` present and accessible)
- `cloud-hypervisor` binary installed at `/usr/local/bin/cloud-hypervisor`
- A Linux kernel image at `/var/lib/cloud-hypervisor/vmlinux`
- One or more golden qcow2 images under `/var/lib/cloud-hypervisor/images/`
  matching the names in `backend/app/registry.py`
- (Optional) A host bridge `br0` configured with IP forwarding

### Step 1 — Configure

Copy `.env.example` to `.env` and adjust paths:

```bash
cp .env.example .env
# Edit .env if your CH / kernel / image paths differ
```

Required environment tweaks for production:

```bash
# .env
DATABASE_URL=postgresql+asyncpg://lab:lab@postgres:5432/labplatform
DATABASE_URL_SYNC=postgresql://lab:lab@postgres:5432/labplatform   # for alembic
IPAM_SERVICE_URL=http://ipam-service:8100
HYPERVISOR_BACKEND=local_ch                # NOT "mock" — that would skip real VMs
CH_BINARY=/usr/local/bin/cloud-hypervisor
CH_KERNEL=/var/lib/cloud-hypervisor/vmlinux
CH_IMAGE_DIR=/var/lib/cloud-hypervisor/images
CH_BRIDGE=br0
DEVICE_READINESS_TIMEOUT_SEC=1800           # DO NOT lower — Juniper needs ~15 min
```

### Step 2 — Start everything

```bash
make up-all          # postgres + ipam-service + backend
```

Equivalent to:

```bash
docker compose up -d postgres ipam-service
docker compose up -d backend
```

### Step 3 — Apply database migrations

```bash
make migrate
```

This runs Alembic against the containerised postgres and creates `labs`,
`ip_allocations`, `lab_events`.

### Step 4 — Verify

```bash
# Backend
curl -s http://localhost:8000/healthz
# → {"status":"ok"}

# IPAM
curl -s http://localhost:8100/healthz
# → {"status":"ok"}

# IPAM pool status
curl -s http://localhost:8100/status
# → {"pool":"172.30.0.0/16","prefix_len":24,"total_blocks":256,"in_use":0,"free":256}

# Backend readiness
curl -s http://localhost:8000/readyz
# → {"status":"ready","database":"ok","lab_agent":"ok"}  (or "degraded")
```

### Step 5 — Create your first lab

```bash
curl -s -X POST http://localhost:8000/api/v1/labs \
  -H 'Content-Type: application/json' \
  -d '{"lab_type":"router","cpu":4,"memory":"8G"}'
```

Response:

```json
{
  "lab_id": "lab-7f8a1b2c3d4e",
  "status": "REQUESTED",
  "progress": 5,
  "cpu": 4,
  "memory_mb": 8192,
  "subnet": null,
  "gateway": null,
  "vm_ip": null,
  ...
}
```

Poll `GET /api/v1/labs/{lab_id}` every few seconds until `status == "LAB_READY"`.

### Step 6 — Connect to the lab

```bash
# Open a console to device r1 (WebSocket through the backend gateway)
python scripts/test_ws.py lab-7f8a1b2c3d4e
```

### Step 7 — Tear down

```bash
curl -s -X DELETE http://localhost:8000/api/v1/labs/lab-7f8a1b2c3d4e
```

This runs `destroy_lab()`, which:
1. Asks `cloud-hypervisor` to shut down the VM cleanly.
2. Polls for graceful exit, escalates to SIGTERM/SIGKILL if needed.
3. Removes the TAP interface.
4. Calls `POST /release/{lab_id}` on the IPAM service.
5. Transitions the lab to `DESTROYED`.

### Step 8 — Stop everything

```bash
make down
```

Stops and removes all containers. Postgres data persists in the
`labplatform_pgdata` volume.

---

## 6. Quickstart — native development (no Docker)

Use this if you want to hack on the backend / IPAM without spinning up
containers. Postgres still runs in docker.

### Prerequisites

- Python 3.11+
- The same `cloud-hypervisor` setup as above (or use `HYPERVISOR_BACKEND=mock`
  for an in-process simulator)

### Steps

```bash
# 1. Bring up just postgres
make up

# 2. Install everything into .venv
make install

# 3. Apply migrations
make migrate

# 4. In separate terminals:
make ipam-bg          # ipam-service on :8100, logs → var/ipam.log
make backend-bg       # backend on :8000, logs → var/backend.log
make agent-bg         # lab_agent on :9001 (only useful if you have a real VM)

# 5. Tail logs
tail -f var/backend.log
tail -f var/ipam.log
```

Stop:

```bash
make clean            # kills ipam/backend/agent, removes var/
```

---

## 7. REST API reference

All requests/responses use JSON unless otherwise noted. Errors follow this
envelope:

```json
{
  "error": {
    "code": "MACHINE_READABLE_CODE",
    "message": "human readable explanation",
    "details": null
  }
}
```

### 7.1 Backend Agent (port 8000)

Base URL: `http://<host>:8000`

#### `GET /healthz`

Liveness — always returns 200 if the process is up.

```
200 OK
{"status": "ok"}
```

#### `GET /readyz`

Readiness — checks Postgres and Lab Agent reachability.

```
200 OK  {"status": "ready",    "database": "ok", "lab_agent": "ok"}
200 OK  {"status": "degraded", "database": "ok", "lab_agent": "down"}
200 OK  {"status": "degraded", "database": "down", "lab_agent": "ok"}
```

(`/readyz` returns 200 in both ready and degraded states; clients should read
the body.)

#### `POST /api/v1/labs`

Create a new lab. Kicks off the orchestrator workflow asynchronously.

Request body:

```json
{
  "lab_type": "router",        // required, one of the registered types
  "cpu": 4,                    // optional, default 4, range 1..64
  "memory": "8G",              // optional, default "8G", format <int>[MG]
  "user_id": "alice"           // optional, free-form identifier
}
```

Validation errors → `422`:

```json
{"error":{"code":"VALIDATION_ERROR","message":"request validation failed","details":[...]}}
```

Success → `201 Created`:

```json
{
  "lab_id": "lab-7f8a1b2c3d4e",
  "user_id": null,
  "lab_type": "router",
  "golden_image": "router.qcow2",
  "status": "REQUESTED",
  "progress": 5,
  "cpu": 4,
  "memory_mb": 8192,
  "subnet": null,
  "gateway": null,
  "vm_ip": null,
  "vm_pid": null,
  "tap_name": null,
  "error": null,
  "created_at": "2026-09-08T10:00:00Z",
  "started_at": null,
  "ready_at": null,
  "terminated_at": null
}
```

#### `GET /api/v1/labs`

List labs, newest first.

Query parameters:

| Name | Type | Default | Notes |
|---|---|---|---|
| `status` | string | — | Filter by status (e.g. `LAB_READY`, `DESTROYED`) |
| `limit` | int | 50 | Range 1..500 |

```
200 OK
[
  { "lab_id": "...", "status": "LAB_READY", ... },
  ...
]
```

#### `GET /api/v1/labs/{lab_id}`

Fetch one lab by ID. `lab_id` must match `[A-Za-z0-9_-]{1,64}`.

```
200 OK  { ... LabResponse ... }
400     {"error":{"code":"INVALID_ID","message":"invalid lab id"}}
404     {"error":{"code":"LAB_NOT_FOUND","message":"no lab <id>"}}
```

#### `DELETE /api/v1/labs/{lab_id}`

Tear down a lab. Idempotent — calling on a `DESTROYED` lab returns the
existing record. Otherwise kicks off `destroy_lab()` asynchronously and
returns immediately.

```
202 Accepted   { ... LabResponse with status=STOPPING ... }
400            {"error":{"code":"INVALID_ID",...}}
404            {"error":{"code":"LAB_NOT_FOUND",...}}
```

### 7.2 IPAM Service (port 8100)

Base URL: `http://<host>:8100`

The IPAM service is **internal** — the backend is its only normal client. The
endpoints are documented here for operators and for completeness.

#### `GET /healthz`

```
200 OK  {"status": "ok"}
```

#### `GET /readyz`

Probes the database.

```
200 OK  {"status": "ready"}
503     {"error":{"code":"DB_DOWN","message":"ipam db unreachable"}}
```

#### `POST /allocate`

Allocate a `/IPAM_PREFIX_LEN` block to a lab.

Request body:

```json
{"lab_id": "lab-7f8a1b2c3d4e"}
```

Success → `200 OK`:

```json
{
  "subnet": "172.30.0.0/24",
  "gateway": "172.30.0.1",
  "vm_ip": "172.30.0.10"
}
```

Pool exhausted → `503`:

```json
{"error":{"code":"IPAM_EXHAUSTED","message":"no free /24 blocks left in pool 172.30.0.0/16"}}
```

#### `POST /release/{lab_id}`

Idempotently release the lab's allocation. Used by the backend during destroy.

```
200 OK  {"ok": true}
```

#### `GET /status`

Pool stats.

```
200 OK
{
  "pool": "172.30.0.0/16",
  "prefix_len": 24,
  "total_blocks": 256,
  "in_use": 3,
  "free": 253
}
```

#### `GET /allocations`

Debug — list all currently active allocations.

```
200 OK
[
  {"lab_id":"lab-abc","subnet":"172.30.0.0/24","gateway":"172.30.0.1","vm_ip":"172.30.0.10","allocated_at":"..."},
  ...
]
```

---

## 8. WebSocket API reference

### `/api/v1/labs/{lab_id}/terminal?device={device_name}`

WebSocket terminal gateway. The backend accepts the student's WS, validates
that the lab is `LAB_READY`, then **bridges** the socket to the Lab Agent's
console WebSocket inside the VM.

**Closing codes the backend may send:**

| Code | Meaning |
|---|---|
| 4404 | Lab not found |
| 4409 | Lab not ready (status != `LAB_READY`) |
| 4400 | Lab agent hasn't started topology yet |
| 1011 | Internal error in the gateway |

**Smoke test** (provided in `scripts/`):

```bash
# Through the backend gateway (preferred)
python scripts/test_ws.py lab-7f8a1b2c3d4e

# Direct to the Lab Agent (useful when debugging the VM-side service)
python scripts/test_ws_direct.py
```

### Lab Agent direct WebSocket (in-VM)

```
ws://<vm_ip>:9001/console           # default device
ws://<vm_ip>:9001/console/{device}  # specific device
```

Note: the Lab Agent console stream is currently a stub — see
`lab_agent/app/routes.py`. A real vrnetlab-backed console implementation
belongs to the per-golden-image Lab Agent build.

---

## 9. Configuration reference

All settings are read by Pydantic from environment variables (or `.env`). See
`.env.example` for the canonical list.

### General

| Variable | Default | Notes |
|---|---|---|
| `APP_NAME` | `lab-platform-backend` | Logged at startup |
| `LOG_LEVEL` | `INFO` | `DEBUG` uses colored dev renderer; anything else uses JSON |

### Database

| Variable | Default |
|---|---|
| `DATABASE_URL` | `postgresql+asyncpg://lab:lab@localhost:5432/labplatform` |
| `DATABASE_URL_SYNC` | `postgresql://lab:lab@localhost:5432/labplatform` |

Inside Docker, the host is `postgres` (the compose service name). Outside, it's
`localhost`.

### IPAM

| Variable | Default | Notes |
|---|---|---|
| `IPAM_POOL` | `172.30.0.0/16` | Single CIDR. Sliced into `/IPAM_PREFIX_LEN` blocks. |
| `IPAM_PREFIX_LEN` | `24` | Must be longer than the pool prefix. |
| `IPAM_SERVICE_URL` | `http://ipam-service:8100` | Backend → IPAM. Override to `http://localhost:8100` when running backend natively. |

### Resource quotas

| Variable | Default |
|---|---|
| `MAX_LABS_PER_HOST` | `10` |
| `CPU_QUOTA` | `64` |
| `MEM_QUOTA_MB` | `131072` |

### Hypervisor

| Variable | Default |
|---|---|
| `HYPERVISOR_BACKEND` | `local_ch` (in compose); `mock` (in `.env.example` for dev) |
| `CH_BINARY` | `/usr/local/bin/cloud-hypervisor` |
| `CH_KERNEL` | `/var/lib/cloud-hypervisor/vmlinux` |
| `CH_IMAGE_DIR` | `/var/lib/cloud-hypervisor/images` |
| `CH_BRIDGE` | `br0` |
| `CH_TAP_PREFIX` | `tap` |
| `CH_DISK_DIR` | `./var/runtime-disks` (in compose: `/app/var/runtime-disks`) |
| `CH_API_SOCKET_DIR` | `./var/ch-sockets` |
| `CH_SEED_DIR` | (empty by default) |
| `CH_SEED_FILE` | `seed.iso` |

### Lab Agent

| Variable | Default | Notes |
|---|---|---|
| `LAB_AGENT_BASE_URL` | `http://172.30.0.10:9001` | Bootstrap URL for the **first** lab only. Subsequent labs use their own `vm_ip`. |
| `DEVICE_READINESS_TIMEOUT_SEC` | `1800` | **Do not lower** — Juniper routers need ~15 min to boot. |
| `DEVICE_READINESS_POLL_SEC` | `5` | |

### Backend server

| Variable | Default |
|---|---|
| `BACKEND_HOST` | `0.0.0.0` |
| `BACKEND_PORT` | `8000` |

---

## 10. Operational commands

The `Makefile` exposes every common operation. Run `make help` for the full
list.

### Stack lifecycle

| Command | What it does |
|---|---|
| `make up` | Start Postgres only (docker compose) |
| `make up-ipam` | Start Postgres + IPAM service |
| `make up-all` | Start Postgres + IPAM service + Backend |
| `make up-backend` | Alias for `up-all` |
| `make down` | Stop and remove all containers |
| `make migrate` | Apply Alembic migrations against the running postgres |

### Native process lifecycle (developer mode)

| Command | What it does |
|---|---|
| `make ipam` | Run IPAM service in foreground (reload) |
| `make ipam-bg` | Run IPAM service in background (logs → `var/ipam.log`) |
| `make backend` | Run backend in foreground (reload) |
| `make backend-bg` | Run backend in background (logs → `var/backend.log`) |
| `make agent` | Run Lab Agent in foreground (inside a VM, not on host) |
| `make agent-bg` | Run Lab Agent in background |
| `make clean` | Kill background processes, remove `var/` |

### Tests & install

| Command | What it does |
|---|---|
| `make install` | Install backend + lab_agent + ipam_service into `.venv` |
| `make install-backend` | Install backend only |
| `make install-agent` | Install lab_agent only |
| `make install-ipam` | Install ipam_service only |
| `make test` | Run backend pytest suite (18 tests) |

### Reset environment (dangerous)

`reset_environment.sh` is an emergency script — it kills any leftover
`cloud-hypervisor` processes, removes leftover `tap-*` interfaces, deletes
runtime artifacts, and wipes non-terminal lab rows:

```bash
./reset_environment.sh
```

This is destructive. Do not run while student labs are active.

---

## 11. Testing

### Backend

```bash
cd backend
../.venv/bin/python -m pytest tests/ -v
```

The suite has 18 tests:

| File | Tests |
|---|---|
| `tests/test_state_machine.py` | Transition legality, progress monotonicity, terminal states |
| `tests/test_ipam.py` | Allocator distinctness, release+reuse, soft release (against the in-tree `IPAMService` class — the service itself is exercised by `ipam_service/`) |
| `tests/test_api_labs.py` | REST endpoints: 201, 422, 404, 202, list, health |
| `tests/test_e2e_workflow.py` | Full lifecycle `REQUESTED → LAB_READY`, destroy, device-boot timeout |

Tests use **SQLite** (`aiosqlite`) and monkeypatch both the IPAM HTTP client
and the Lab Agent HTTP client, so no Postgres, no IPAM service, no Lab Agent,
and no real VM are required.

### IPAM service

```bash
cd ipam_service
../.venv/bin/python -m pytest tests/ -v   # (scaffold only — add tests as needed)
```

### Lab Agent

```bash
cd lab_agent
../.venv/bin/python -m pytest tests/ -v
```

### WebSocket smoke tests

```bash
# Through the backend gateway
python scripts/test_ws.py lab-7f8a1b2c3d4e

# Direct to the Lab Agent (set vm_ip in the script)
python scripts/test_ws_direct.py
```

---

## 12. Adding a new lab type

Each lab type maps 1:1 to a **golden image**. Adding a new type is a three-step
process — no code changes required in the IPAM service or the state machine.

### Step 1 — Bake the golden image

On a workstation with `containerlab` + `vrnetlab` + your target Juniper image:

1. Build a rootfs that, on first boot:
   - Brings up `eth0` with `ip=<vm_ip>::<gateway>:<mask>::eth0:off`
     (the values come from the backend via the seed ISO or kernel cmdline)
   - Installs Docker, ContainerLab, vrnetlab
   - Drops your `.clab.yml` at `/opt/lab_agent/topologies/<your_lab>.clab.yml`
   - Installs a `systemd` unit that runs `lab_agent` on `:9001`
2. Convert to qcow2:
   ```bash
   qemu-img convert -O qcow2 rootfs.raw your_lab.qcow2
   ```
3. Copy the qcow2 (and the matching kernel + seed ISO) to the CH host.

### Step 2 — Register the lab type

Edit `backend/app/registry.py`:

```python
REGISTRY: dict[str, LabTypeConfig] = {
    "router": LabTypeConfig(name="router", golden_image="router.qcow2"),
    "switch": LabTypeConfig(name="switch", golden_image="switch.qcow2"),
    # add yours:
    "my-new-lab": LabTypeConfig(name="my-new-lab", golden_image="my-new-lab.qcow2"),
}
```

### Step 3 — Use it

```bash
curl -X POST http://localhost:8000/api/v1/labs \
  -H 'Content-Type: application/json' \
  -d '{"lab_type":"my-new-lab","cpu":4,"memory":"8G"}'
```

That's it. The state machine, orchestrator, IPAM, and quota manager all key
off the string `lab_type` and don't need to be touched.

---

## 13. Troubleshooting

### `/healthz` returns OK but `/readyz` is "degraded"

The backend can reach Postgres but the **Lab Agent at `LAB_AGENT_BASE_URL` is
unreachable**. This is expected behaviour in two cases:
1. No lab is running yet (the URL points at a not-yet-allocated IP).
2. `HYPERVISOR_BACKEND=mock` is in effect — mock VMs don't expose a real
   Lab Agent port, so the readiness check returns "degraded" by design.

If `HYPERVISOR_BACKEND=local_ch` and you see "degraded" while a lab is
running, the Lab Agent inside the microVM has failed to start. Inspect:

```bash
tail -f var/ch-logs/<lab_id>.log
```

### `POST /api/v1/labs` returns 503 `IPAM_EXHAUSTED`

The IPAM pool is full. Either:
- Widen `IPAM_POOL` (e.g. `172.30.0.0/12` → 1024 `/24` blocks).
- Wait for existing labs to be destroyed.

### `POST /api/v1/labs` returns 500 with "ipam-service unreachable"

The backend container can't reach the IPAM container. Check:

```bash
docker compose ps                  # is ipam-service running?
docker compose logs ipam-service   # any startup errors?
curl http://localhost:8100/healthz # is the IPAM service itself up?
```

If you're running the backend natively and IPAM in docker, make sure
`IPAM_SERVICE_URL=http://localhost:8100` in your local `.env`.

### `cloud-hypervisor exited immediately with code N`

`CH_BINARY`, `CH_KERNEL`, or `CH_IMAGE_DIR` is wrong, or KVM is unavailable.
Check:

```bash
ls -la /dev/kvm
ls -la /usr/local/bin/cloud-hypervisor
ls -la /var/lib/cloud-hypervisor/vmlinux
ls /var/lib/cloud-hypervisor/images/
tail var/ch-logs/<lab_id>.log    # last 2KB of CH stderr
```

### `DEVICE_READINESS_TIMEOUT` keeps tripping

Your golden image's ContainerLab topology is taking longer than
`DEVICE_READINESS_TIMEOUT_SEC` seconds for all devices to come up. Juniper
vJunos routers commonly need 12–15 minutes. **Do not lower the timeout below
~1200s without testing.** Either:
- Raise the timeout (`.env` + `make up-all`).
- Optimise your golden image (faster vrnetlab boot, smaller topology).

### `tap-lab-...` interfaces accumulate after labs are destroyed

`reset_environment.sh` will sweep them:

```bash
./reset_environment.sh
```

If they keep accumulating, the TAP cleanup in `local_ch.stop()` or
`networking.remove_tap()` is failing — check `var/backend.log` for warnings.

### `database is locked` errors during pytest

SQLite under concurrent test load. Two options:
- Run tests serially: `pytest -x -q`.
- Switch the test fixture to use a real Postgres (already supported via
  `DATABASE_URL=postgresql+...`).

### Tests pass in isolation but fail together

The autouse fixtures clean tables between tests, but `aiosqlite` + a single
SQLite file can race. Either run tests serially or point `conftest.py` at
Postgres for CI:

```bash
DATABASE_URL=postgresql+asyncpg://lab:lab@localhost:5432/labplatform_test \
DATABASE_URL_SYNC=postgresql://lab:lab@localhost:5432/labplatform_test \
  pytest tests/
```

---

## 14. License

Proprietary — internal use only. (See `backend/pyproject.toml`.)
