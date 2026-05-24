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

# ── OPERATOR CLI (techpulse-ops) ──────────────────────────────────────────────
ops:
	PYTHONPATH=$(PYTHONPATH) uv run python -m cli.ops $(ARGS)

ops-collect:
	PYTHONPATH=$(PYTHONPATH) uv run python -m cli.ops run collect

ops-summarize:
	PYTHONPATH=$(PYTHONPATH) uv run python -m cli.ops run summarize

ops-deliver:
	PYTHONPATH=$(PYTHONPATH) uv run python -m cli.ops run deliver

ops-all:
	PYTHONPATH=$(PYTHONPATH) uv run python -m cli.ops run all

ops-tenants:
	PYTHONPATH=$(PYTHONPATH) uv run python -m cli.ops tenants list

# ── USER CLI (techpulse) ──────────────────────────────────────────────────────
user:
	PYTHONPATH=$(PYTHONPATH) uv run python -m cli.user $(ARGS)

user-login:
	PYTHONPATH=$(PYTHONPATH) uv run python -m cli.user login

user-status:
	PYTHONPATH=$(PYTHONPATH) uv run python -m cli.user status

user-sources:
	PYTHONPATH=$(PYTHONPATH) uv run python -m cli.user sources list

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

## Populate default configuration from migration script
migrate-config:
	PYTHONPATH=$(PYTHONPATH) uv run python scratch/migrate_config.py
