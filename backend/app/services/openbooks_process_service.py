"""Start/stop the local OpenBooks server as a child process.

James isn't terminal-savvy, so the admin "Find a Book" page gets a Start/Stop
button instead of "go run this PowerShell script". This is the same command
``backend/tools/run-openbooks.ps1`` runs, just launched from the API.

The child is *not* detached — a clean backend shutdown terminates it. If the
backend is killed hard the child is orphaned but still detectable (the port is
listening) and ``stop()`` will kill whatever holds the port.
"""

from __future__ import annotations

import logging
import random
import re
import socket
import subprocess
import sys
import time
from pathlib import Path

from app.core.config import get_settings

logger = logging.getLogger(__name__)

_proc: subprocess.Popen | None = None

_PORT_WAIT_SECONDS = 12.0


class OpenBooksProcessError(RuntimeError):
    """Couldn't start or stop the OpenBooks server process."""


def _binary() -> Path:
    configured = get_settings().openbooks_binary
    if configured:
        return Path(configured)
    name = "openbooks.exe" if sys.platform == "win32" else "openbooks"
    return Path(__file__).resolve().parents[2] / "tools" / name


def _port() -> int:
    match = re.search(r"://[^/:]+:(\d+)", get_settings().openbooks_ws_url)
    return int(match.group(1)) if match else 5228


def _port_open(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sk:
        sk.settimeout(0.5)
        return sk.connect_ex(("127.0.0.1", port)) == 0


def _managed() -> bool:
    return _proc is not None and _proc.poll() is None


def status() -> dict:
    return {
        "installed": _binary().exists(),
        "running": _port_open(_port()),
        "managed": _managed(),
        "pid": _proc.pid if _managed() else None,
    }


def start() -> dict:
    """Launch the OpenBooks server if it isn't already listening. Blocking —
    call via asyncio.to_thread."""
    global _proc

    binary = _binary()
    if not binary.exists():
        raise OpenBooksProcessError(
            f"openbooks not found at {binary} — download openbooks.exe from "
            "github.com/evan-buss/openbooks/releases into backend/tools/"
        )

    port = _port()
    if _port_open(port):
        return status()  # already up (managed or a leftover)

    settings = get_settings()
    dl_dir = Path(settings.openbooks_download_dir)
    dl_dir.mkdir(parents=True, exist_ok=True)

    args = [
        str(binary),
        "server",
        "--port",
        str(port),
        "--persist",
        "--dir",
        str(dl_dir),
        "--no-browser-downloads",
        "--name",
        f"bb_{random.randint(1000, 99999)}",
    ]
    log_path = dl_dir / "openbooks-server.log"
    creationflags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0

    logger.info("openbooks: starting server: %s", " ".join(args))
    log_handle = open(log_path, "ab")  # noqa: SIM115 - handed to the child, closed on stop
    try:
        _proc = subprocess.Popen(
            args,
            stdout=log_handle,
            stderr=subprocess.STDOUT,
            cwd=str(binary.parent),
            creationflags=creationflags,
        )
    except OSError as exc:
        log_handle.close()
        raise OpenBooksProcessError(f"couldn't launch openbooks: {exc}") from exc

    deadline = time.monotonic() + _PORT_WAIT_SECONDS
    while time.monotonic() < deadline:
        if _port_open(port):
            return status()
        if _proc.poll() is not None:
            raise OpenBooksProcessError(
                f"openbooks exited immediately (code {_proc.returncode}) — see {log_path.name}"
            )
        time.sleep(0.25)

    raise OpenBooksProcessError(
        f"openbooks started but isn't listening on port {port} after "
        f"{_PORT_WAIT_SECONDS:.0f}s — see {log_path.name}"
    )


def _kill_by_port(port: int) -> None:
    if sys.platform != "win32":
        raise OpenBooksProcessError(
            "an OpenBooks server is running but this backend didn't start it — stop it manually"
        )
    try:
        out = subprocess.run(
            ["netstat", "-ano", "-p", "tcp"], capture_output=True, text=True, timeout=10
        ).stdout
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise OpenBooksProcessError(f"couldn't inspect port {port}: {exc}") from exc

    pids = {
        line.split()[-1]
        for line in out.splitlines()
        if f":{port} " in line and "LISTENING" in line and line.split()[-1].isdigit()
    }
    if not pids:
        return
    for pid in pids:
        subprocess.run(["taskkill", "/PID", pid, "/F"], capture_output=True, timeout=10)
    logger.info("openbooks: killed unmanaged server process(es) %s on port %d", pids, port)


def stop() -> dict:
    """Stop the OpenBooks server — the child we started, or an orphan holding
    the port. Blocking — call via asyncio.to_thread."""
    global _proc

    if _managed():
        assert _proc is not None
        _proc.terminate()
        try:
            _proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            _proc.kill()
        if _proc.stdout is not None:
            _proc.stdout.close()
        _proc = None
    elif _port_open(_port()):
        _kill_by_port(_port())

    return status()


def shutdown() -> None:
    """Lifespan hook: only ever touches the child we started."""
    global _proc
    if _managed():
        assert _proc is not None
        _proc.terminate()
        try:
            _proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            _proc.kill()
        _proc = None


def _reset_for_tests() -> None:
    global _proc
    _proc = None
