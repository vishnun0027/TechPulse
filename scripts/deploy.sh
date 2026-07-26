#!/bin/bash
# Uniform Deployment Script for TechPulse
set -e

PROJECT_NAME="TechPulse"
PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

echo "========================================="
echo "🚀 Deploying $PROJECT_NAME in $PROJECT_DIR"
echo "========================================="

# 1. Update PATH to include user local binaries
export PATH="$HOME/.local/bin:$PATH"

# 2. Pull latest code
if [ -d "$PROJECT_DIR/.git" ]; then
    echo "--- Pulling latest code ---"
    cd "$PROJECT_DIR"
    git reset --hard HEAD
    git pull origin main
fi

# 3. Sync dependencies
echo "--- Syncing dependencies with uv ---"
cd "$PROJECT_DIR"
uv sync --frozen

# 4. Run database migrations
echo "--- Running database migrations ---"
uv run python scripts/migrate.py

# 5. Prepare systemd directory
echo "--- Installing systemd user services ---"
mkdir -p "$HOME/.config/systemd/user/"

# 6. Template and copy systemd files
for f in "$PROJECT_DIR"/config/systemd/*; do
    if [ -f "$f" ]; then
        basename_f=$(basename "$f")
        echo "Templating $basename_f..."
        # Replace hardcoded paths and user/group names dynamically
        sed -e "s|/home/vishnu/worklab/techpulse|$PROJECT_DIR|g" \
            -e "/^User=/d" \
            -e "/^Group=/d" \
            "$f" > "$HOME/.config/systemd/user/$basename_f"
    fi
done

# 7. Enable lingering and export runtime dir for systemctl --user commands
loginctl enable-linger "$(whoami)" || true
export XDG_RUNTIME_DIR="/run/user/$(id -u)"

# 8. Reload systemd daemon
echo "--- Reloading systemd user daemon ---"
systemctl --user daemon-reload

# 9. Enable services and timers
echo "--- Enabling services and timers ---"
systemctl --user enable techpulse-collector.timer techpulse-pulse.timer techpulse-archive.timer techpulse-keepalive.timer techpulse-purge.timer techpulse-api.service

# 10. Restart services and timers
echo "--- Restarting services ---"
systemctl --user restart techpulse-collector.timer techpulse-pulse.timer techpulse-archive.timer techpulse-keepalive.timer techpulse-purge.timer techpulse-api.service

# 11. Verify deployment (health check)
echo "--- Verifying deployment (health check) ---"
HEALTH_PASSED=false
for i in {1..8}; do
    echo "Health check attempt $i/8..."
    if curl --fail --silent --show-error http://localhost:8089/health; then
        echo "✅ API health check passed"
        HEALTH_PASSED=true
        break
    fi
    sleep 5
done

if [ "$HEALTH_PASSED" = false ]; then
    echo "❌ API health check FAILED"
    echo "=== Journalctl API logs ==="
    journalctl --user -u techpulse-api.service -n 100 --no-pager || true
    echo "=== Service Status ==="
    systemctl --user status techpulse-api.service || true
    exit 1
fi

echo "🎉 $PROJECT_NAME deployment completed successfully!"
