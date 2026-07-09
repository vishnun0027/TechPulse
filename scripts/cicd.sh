#!/bin/bash
set -e

# Define variables
PROJECT_DIR="/home/vishnu/worklab/techpulse"
cd "$PROJECT_DIR"
export PATH="$HOME/.local/bin:$PATH"

# Mode flags
RUN_CI=true
RUN_CD=true

if [ "$1" = "--ci-only" ]; then
    RUN_CD=false
elif [ "$1" = "--cd-only" ]; then
    RUN_CI=false
fi

# ── RUN CI (TESTS & LINT) ──────────────────────────────────────────────
if [ "$RUN_CI" = true ]; then
    echo "========================================="
    echo "🚀 Starting Continuous Integration (CI)"
    echo "========================================="

    # Export dummy environment variables for tests
    export DATABASE_URL="sqlite:///./test.db"
    export GROQ_API_KEY="ci-dummy-key"
    export SUPABASE_URL="https://ci-dummy.supabase.co"
    export SUPABASE_KEY="ci-dummy-key"
    export TELEGRAM_BOT_TOKEN="0000000000:ci-dummy-token"
    export TELEGRAM_ALLOWED_CHAT_ID="123456789"
    export EMBED_SERVER_URL="http://localhost:8080"

    echo "🧹 Linting with Ruff..."
    uvx ruff check src/ tests/ --ignore E501

    echo "🧪 Running Pytest Suite..."
    uv run pytest tests/unit/ -v --tb=short

    echo "✅ CI checks passed successfully!"
fi

# ── RUN CD (DEPLOYMENT) ────────────────────────────────────────────────
if [ "$RUN_CD" = true ]; then
    echo "========================================="
    echo "🚀 Starting Continuous Deployment (CD)"
    echo "========================================="

    echo "--- Pulling latest code ---"
    git reset --hard HEAD
    git pull origin main

    echo "--- Syncing dependencies ---"
    uv sync --frozen

    echo "--- Running database migrations ---"
    uv run python scripts/migrate.py

    echo "--- Installing systemd service files ---"
    mkdir -p "$HOME/.config/systemd/user/"
    cp "$PROJECT_DIR"/config/systemd/* "$HOME/.config/systemd/user/"

    echo "--- Reloading systemd and restarting timers ---"
    systemctl --user daemon-reload
    systemctl --user enable techpulse-collector.timer techpulse-pulse.timer techpulse-archive.timer techpulse-keepalive.timer techpulse-purge.timer techpulse-api.service
    systemctl --user restart techpulse-collector.timer techpulse-pulse.timer techpulse-archive.timer techpulse-keepalive.timer techpulse-purge.timer techpulse-api.service

    echo "--- Verifying timer statuses ---"
    sleep 2

    # Check collector timer
    systemctl --user is-active --quiet techpulse-collector.timer \
      && echo "✅ techpulse-collector.timer is active" \
      || (echo "❌ techpulse-collector.timer is NOT active" && exit 1)

    # Check pulse timer
    systemctl --user is-active --quiet techpulse-pulse.timer \
      && echo "✅ techpulse-pulse.timer is active" \
      || (echo "❌ techpulse-pulse.timer is NOT active" && exit 1)

    # Check archive timer
    systemctl --user is-active --quiet techpulse-archive.timer \
      && echo "✅ techpulse-archive.timer is active" \
      || (echo "❌ techpulse-archive.timer is NOT active" && exit 1)

    # Check keepalive timer
    systemctl --user is-active --quiet techpulse-keepalive.timer \
      && echo "✅ techpulse-keepalive.timer is active" \
      || (echo "❌ techpulse-keepalive.timer is NOT active" && exit 1)

    # Check purge timer
    systemctl --user is-active --quiet techpulse-purge.timer \
      && echo "✅ techpulse-purge.timer is active" \
      || (echo "❌ techpulse-purge.timer is NOT active" && exit 1)

    echo "🎉 CD Deployment completed successfully!"
fi
