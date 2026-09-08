VENV ?= .venv
ABS_VENV := $(abspath $(VENV))
PY ?= $(ABS_VENV)/bin/python
PIP ?= $(ABS_VENV)/bin/pip

venv:
	@test -d $(VENV) || python3 -m venv $(VENV)
	@$(PIP) install --upgrade pip

.PHONY: help up up-all up-ipam up-backend down migrate backend backend-bg agent agent-bg ipam ipam-bg test install-backend install-agent install-ipam install all clean

help:
	@echo "Targets:"
	@echo "  install         Install backend + lab_agent + ipam_service dependencies"
	@echo "  install-ipam    Install ipam_service only"
	@echo "  up              Start Postgres only (docker compose)"
	@echo "  up-ipam         Start Postgres + ipam-service (docker compose)"
	@echo "  up-all          Start Postgres + ipam-service + backend (docker compose)"
	@echo "  up-backend      Start Postgres + ipam-service + backend"
	@echo "  down            Stop all docker-compose services"
	@echo "  migrate         Apply Alembic migrations"
	@echo "  backend         Run backend (foreground, native)"
	@echo "  backend-bg      Run backend in background (logs in var/backend.log)"
	@echo "  ipam            Run ipam-service (foreground, native)"
	@echo "  ipam-bg         Run ipam-service in background (logs in var/ipam.log)"
	@echo "  agent           Run Lab Agent (foreground)"
	@echo "  agent-bg        Run Lab Agent in background (logs in var/agent.log)"
	@echo "  test            Run backend pytest suite"
	@echo "  clean           Stop background processes, remove var/"

install: venv install-backend install-agent install-ipam

install-backend:
	cd backend && $(PIP) install -e ".[dev]"

install-agent:
	cd lab_agent && $(PIP) install -e ".[dev]"

install-ipam:
	cd ipam_service && $(PIP) install -e ".[dev]"

up:
	docker compose up -d postgres

up-ipam:
	docker compose up -d postgres ipam-service

up-all: up-ipam
	docker compose up -d backend

up-backend:
	docker compose up -d postgres ipam-service backend

down:
	docker compose down

migrate:
	cd backend && $(PY) -m alembic -c alembic.ini upgrade head

backend:
	cd backend && $(PY) -m uvicorn app.main:app --host $$BACKEND_HOST --port $$BACKEND_PORT --reload

backend-bg:
	@mkdir -p var
	(cd backend && nohup $(PY) -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --app-dir . > ../var/backend.log 2>&1 & echo $$! > ../var/backend.pid)
	@echo "backend pid: $$(cat var/backend.pid)"

agent:
	cd lab_agent && $(PY) -m uvicorn app.main:app --host 0.0.0.0 --port 9001 --reload

agent-bg:
	@mkdir -p var
	(cd lab_agent && nohup $(PY) -m uvicorn app.main:app --host 0.0.0.0 --port 9001 --app-dir . > ../var/agent.log 2>&1 & echo $$! > ../var/agent.pid)
	@echo "agent pid: $$(cat var/agent.pid)"

ipam:
	cd ipam_service && $(PY) -m uvicorn app.main:app --host 0.0.0.0 --port 8100 --reload

ipam-bg:
	@mkdir -p var
	(cd ipam_service && nohup $(PY) -m uvicorn app.main:app --host 0.0.0.0 --port 8100 --app-dir . > ../var/ipam.log 2>&1 & echo $$! > ../var/ipam.pid)
	@echo "ipam pid: $$(cat var/ipam.pid)"

test:
	cd backend && $(PY) -m pytest -x -q

clean:
	@if [ -f var/backend.pid ]; then kill $$(cat var/backend.pid) 2>/dev/null || true; rm -f var/backend.pid; fi
	@if [ -f var/agent.pid ]; then kill $$(cat var/agent.pid) 2>/dev/null || true; rm -f var/agent.pid; fi
	@if [ -f var/ipam.pid ]; then kill $$(cat var/ipam.pid) 2>/dev/null || true; rm -f var/ipam.pid; fi
	rm -rf var/