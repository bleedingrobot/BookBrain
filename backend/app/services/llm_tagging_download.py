"""Runs the Drive file download for the LLM-tagging tick in a subprocess so
a stalled connection can be killed outright.

Incident 2026-09-17: a chunked response trickled bytes just fast enough to
keep resetting httplib2's idle-recv timeout (client.py's `_HTTP_TIMEOUT_SECONDS`)
without ever finishing. tick() already wrapped the download in
`asyncio.wait_for(..., timeout=...)`, but that only cancels the *await* — the
underlying `asyncio.to_thread` worker thread was already blocked inside a
synchronous socket read, and a Python thread can't be forced to stop, so
`wait_for` ends up blocking right alongside it until the read itself gives
up. Result: the tick never returned, and (max_instances=1) every later tick
was skipped behind it — for about five hours until the process was
restarted, mirroring the same class of hang already fixed once for this job
(see tick()'s docstring on the earlier Drive-download incident).

A subprocess doesn't have this problem: SIGKILL works no matter what syscall
it's stuck in. So the actual Drive call happens in a short-lived child
process; the parent just waits on a pipe with a real timeout and kills the
child if it doesn't answer in time."""

import multiprocessing as mp
from multiprocessing.connection import Connection

from google.oauth2.credentials import Credentials

from app.providers.drive.client import build_drive_service
from app.providers.drive.provider import DriveProvider

_ctx = mp.get_context("spawn")


def _worker(creds_kwargs: dict, file_id: str, conn: Connection) -> None:
    try:
        creds = Credentials(**creds_kwargs)
        provider = DriveProvider(build_drive_service(creds))
        data = provider.download_file(file_id)
        conn.send((True, data))
    except Exception as exc:  # noqa: BLE001 — forward any failure to the parent
        conn.send((False, str(exc)))
    finally:
        conn.close()


def download_file_with_hard_timeout(
    creds: Credentials, file_id: str, *, timeout_seconds: float
) -> bytes:
    """Blocking — call via `asyncio.to_thread`. Raises `TimeoutError` if the
    child hasn't answered within `timeout_seconds`, killing it either way."""
    creds_kwargs = {
        "token": creds.token,
        "refresh_token": creds.refresh_token,
        "token_uri": creds.token_uri,
        "client_id": creds.client_id,
        "client_secret": creds.client_secret,
        "scopes": creds.scopes,
    }
    parent_conn, child_conn = _ctx.Pipe(duplex=False)
    proc = _ctx.Process(target=_worker, args=(creds_kwargs, file_id, child_conn), daemon=True)
    proc.start()
    child_conn.close()
    try:
        if not parent_conn.poll(timeout_seconds):
            raise TimeoutError(f"Drive download stalled past {timeout_seconds}s")
        ok, payload = parent_conn.recv()
    finally:
        if proc.is_alive():
            proc.kill()
        proc.join(timeout=5)
        parent_conn.close()
    if not ok:
        raise RuntimeError(f"Drive download failed: {payload}")
    return payload
