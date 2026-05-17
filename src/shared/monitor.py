import sys
import time
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.live import Live
from rich.layout import Layout
from shared.redis_client import redis, STREAM_RAW
from shared.db import supabase
from datetime import datetime, timezone, timedelta

console = Console()

_stats_cache: dict = {}
_CACHE_TTL = 15  # seconds — refresh stats at most once every 15s


def _get_redis_stats():
    """Fetches consumer group lag and stuck message count from Redis."""
    try:
        info = redis.execute(command=["XINFO", "GROUPS", STREAM_RAW])
        if info:
            fields = info[0]
            d = {fields[i]: fields[i + 1] for i in range(0, len(fields), 2)}
            return d.get("lag", 0), d.get("pending", 0)
    except Exception:
        pass
    return "Error", "Error"


def _get_db_stats():
    """Fetches total, delivered, and ready article counts from Supabase."""
    try:
        total_res = supabase.table("articles").select("count", count="exact").execute()
        total = total_res.count or 0

        delivered_res = (
            supabase.table("articles")
            .select("count", count="exact")
            .eq("is_delivered", True)
            .execute()
        )
        delivered = delivered_res.count or 0

        since = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
        ready_res = (
            supabase.table("articles")
            .select("count", count="exact")
            .eq("is_delivered", False)
            .gte("created_at", since)
            .gte("score", 2.5)
            .execute()
        )
        ready = ready_res.count or 0
        return total, delivered, ready
    except Exception:
        return "Error", "Error", "Error"


def _get_telemetry_stats():
    """Fetches recent telemetry logs."""
    try:
        return (
            supabase.table("telemetry")
            .select("*")
            .order("timestamp", desc=True)
            .limit(5)
            .execute()
            .data
            or []
        )
    except Exception:
        return []


def get_stats():
    """Fetches pipeline stats with a 15-second TTL cache to avoid hammering the DB."""
    now = time.monotonic()
    if _stats_cache and now - _stats_cache.get("_ts", 0) < _CACHE_TTL:
        return _stats_cache["data"]

    lag, stuck = _get_redis_stats()
    total, delivered, ready = _get_db_stats()
    telemetry = _get_telemetry_stats()

    result = (lag, stuck, total, delivered, ready, telemetry)
    _stats_cache["data"] = result
    _stats_cache["_ts"] = now
    return result


def _create_stats_table(lag, stuck, total, delivered, ready) -> Table:
    """Helper to build the Rich stats table."""
    stats_table = Table(title="System Pipeline")
    stats_table.add_column("Metric", style="magenta")
    stats_table.add_column("Value", style="bold green")

    stats_table.add_row("Unread in Queue", str(lag))
    stats_table.add_row("Stuck (Unacknowledged)", str(stuck))
    stats_table.add_row("Total in Database", str(total))
    stats_table.add_row("Total Delivered", str(delivered))
    stats_table.add_row("Ready for Delivery (Top 24h)", str(ready))
    return stats_table


def _create_logs_table(telemetry) -> Table:
    """Helper to build the Rich telemetry logs table."""
    logs_table = Table(title="Recent Activity (Telemetry)")
    logs_table.add_column("Time", style="dim")
    logs_table.add_column("Service")
    logs_table.add_column("Metrics")

    for entry in telemetry:
        ts = datetime.fromisoformat(entry["timestamp"]).strftime("%H:%M:%S")
        svc = entry["service"].capitalize()
        metrics = ", ".join([f"{k}: {v}" for k, v in entry["metrics"].items()])
        color = "green" if entry.get("success", True) else "red"
        logs_table.add_row(ts, f"[{color}]{svc}[/]", metrics)
    return logs_table


def generate_layout(stats):
    lag, stuck, total, delivered, ready, telemetry = stats

    layout = Layout()
    layout.split_column(
        Layout(name="header", size=3),
        Layout(name="main"),
        Layout(name="footer", size=3),
    )

    layout["header"].update(Panel("TechPulse AI — System Monitor", style="bold cyan"))
    layout["main"].split_row(Layout(name="stats"), Layout(name="logs"))

    stats_table = _create_stats_table(lag, stuck, total, delivered, ready)
    layout["stats"].update(Panel(stats_table, border_style="blue"))

    logs_table = _create_logs_table(telemetry)
    layout["logs"].update(Panel(logs_table, border_style="blue"))

    layout["footer"].update(
        Panel(
            f"Last updated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}", style="dim"
        )
    )

    return layout


def run_monitor():
    with Live(generate_layout(get_stats()), refresh_per_second=0.5) as live:
        try:
            while True:
                live.update(generate_layout(get_stats()))
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    if "--live" in sys.argv:
        run_monitor()
    else:
        console.print(generate_layout(get_stats()))
