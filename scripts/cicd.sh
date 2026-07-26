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

    chmod +x "$PROJECT_DIR/scripts/deploy.sh"
    "$PROJECT_DIR/scripts/deploy.sh"
fi
