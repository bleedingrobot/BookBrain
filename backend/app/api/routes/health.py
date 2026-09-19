from fastapi import APIRouter

router = APIRouter(tags=["health"])


@router.get("/health")
async def health() -> dict[str, str]:
    """Liveness only: this returns a literal and touches no database, no Drive
    credential, no scheduler and no provider.

    That is deliberate and worth keeping. Serving a literal is exactly what a
    blocked event loop cannot do, which is what made the 2026-09-18 hang
    detectable at all, and it stays fast enough for the dashboard's `curl -m 2`
    poll every 30s — a slow health check reads as a dead server.

    What it does NOT prove: that the scheduler is running, that Drive
    credentials are valid, that the database isn't locked (32 occurrences in
    the week to 2026-09-18) or that any acquisition provider still works. The
    dashboards therefore render it as "responding" rather than "ok", and the
    alerts block in dashboard.sh covers the things this cannot see. Don't grow
    this into a health aggregator — add checks to that block instead."""
    return {"status": "ok"}
