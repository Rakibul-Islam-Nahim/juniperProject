# IPAM Service

Standalone microservice that owns the `ip_allocations` table. Split out of the
Backend Agent so the address-management plane can be scaled, deployed, and
versioned independently.

## Endpoints

| Verb | Path                | Purpose                                  |
|------|---------------------|------------------------------------------|
| GET  | `/healthz`          | Liveness                                 |
| GET  | `/readyz`           | DB readiness                             |
| POST | `/allocate`         | Allocate a `/prefix_len` block to a lab  |
| POST | `/release/{lab_id}` | Idempotent release (hard delete)         |
| GET  | `/status`           | Pool stats (total/free blocks)           |
| GET  | `/allocations`      | List active allocations (debug)          |

## Request / Response

```http
POST /allocate
Content-Type: application/json

{"lab_id": "lab-abc123"}
```

```json
{
  "subnet": "172.30.0.0/24",
  "gateway": "172.30.0.1",
  "vm_ip": "172.30.0.10"
}
```

## Run locally

```bash
pip install -e ".[dev]"
uvicorn app.main:app --host 0.0.0.0 --port 8100
```

Or via the root `docker-compose.yml`:

```bash
docker compose up -d ipam-service
```