# Lab Agent

In-microVM service that owns Docker / ContainerLab / Juniper health and exposes a console
WebSocket. See the top-level [`../README.md`](../README.md) for the architecture.

## Endpoints

| Verb | Path                       | Purpose                                  |
|------|----------------------------|------------------------------------------|
| GET  | `/health`                  | Liveness                                 |
| GET  | `/status`                  | Current containerlab + per-device state  |
| POST | `/start`                   | `containerlab deploy -t <topology>`      |
| POST | `/stop`                    | `containerlab destroy -t <topology>`     |
| WS   | `/console/{device?}`       | Stream device console bytes              |

In development (no real containerlab / vrnetlab available), `containerlab.py` falls back to
a stub: it launches simple Linux containers via Docker Compose as a stand-in for vJunos so
the full backend↔agent loop is testable.
