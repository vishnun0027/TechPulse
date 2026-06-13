PYTHONPATH=src

.PHONY: collect summarize deliver pipeline monitor dashboard test reset

## Run individual services
collect:
	PYTHONPATH=$(PYTHONPATH) uv run python -m services.collector.main

summarize:
	PYTHONPATH=$(PYTHONPATH) uv run python -m services.summarizer.main

deliver:
	PYTHONPATH=$(PYTHONPATH) uv run python -m services.delivery.main

## Run the full pipeline
pipeline: collect summarize deliver

# ── UNIFIED CLI (pulse) ───────────────────────────────────────────────────────
pulse:
	PYTHONPATH=$(PYTHONPATH) uv run python -m cli.user $(ARGS)

pulse-login:
	PYTHONPATH=$(PYTHONPATH) uv run python -m cli.user login

pulse-status:
	PYTHONPATH=$(PYTHONPATH) uv run python -m cli.user status

pulse-collect:
	PYTHONPATH=$(PYTHONPATH) uv run python -m cli.user run collect

pulse-summarize:
	PYTHONPATH=$(PYTHONPATH) uv run python -m cli.user run summarize

pulse-deliver:
	PYTHONPATH=$(PYTHONPATH) uv run python -m cli.user run deliver

pulse-all:
	PYTHONPATH=$(PYTHONPATH) uv run python -m cli.user run all

pulse-tenants:
	PYTHONPATH=$(PYTHONPATH) uv run python -m cli.user tenants list

## API Server
api:
	PYTHONPATH=$(PYTHONPATH) uv run uvicorn api.main:app --host 0.0.0.0 --port 8000 --reload

## Monitoring
monitor:
	PYTHONPATH=$(PYTHONPATH) uv run python -m shared.monitor --live


## Testing
test:
	PYTHONPATH=$(PYTHONPATH) uv run pytest

## CI/CD Pipeline
cicd:
	@./scripts/cicd.sh

## Maintenance
reset:
	PYTHONPATH=$(PYTHONPATH) uv run python -m shared.maintenance reset --confirm
