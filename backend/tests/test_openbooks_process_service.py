import subprocess

import pytest

from app.core.config import get_settings
from app.services import openbooks_process_service as ps
from app.services.openbooks_process_service import OpenBooksProcessError


class _FakePopen:
    def __init__(self, *, alive: bool = True) -> None:
        self.pid = 4242
        self._alive = alive
        self.returncode = None
        self.terminated = False
        self.killed = False
        self.stdout = None

    def poll(self):
        return None if self._alive else 0

    def terminate(self):
        self.terminated = True
        self._alive = False

    def wait(self, timeout=None):
        return 0

    def kill(self):
        self.killed = True
        self._alive = False


@pytest.fixture
def settings(monkeypatch, tmp_path):
    s = get_settings()
    monkeypatch.setattr(s, "openbooks_binary", str(tmp_path / "openbooks.exe"))
    monkeypatch.setattr(s, "openbooks_download_dir", str(tmp_path / "dl"))
    monkeypatch.setattr(s, "openbooks_ws_url", "ws://localhost:5228/ws")
    return s


def _install_binary(settings):
    from pathlib import Path

    Path(settings.openbooks_binary).write_bytes(b"MZ fake")


def test_status_reports_installed_and_not_running(settings, monkeypatch):
    monkeypatch.setattr(ps, "_port_open", lambda port: False)
    assert ps.status() == {"installed": False, "running": False, "managed": False, "pid": None}
    _install_binary(settings)
    assert ps.status()["installed"] is True


def test_start_without_binary_raises(settings, monkeypatch):
    monkeypatch.setattr(ps, "_port_open", lambda port: False)
    with pytest.raises(OpenBooksProcessError, match="not found"):
        ps.start()


def test_start_noops_when_port_already_open(settings, monkeypatch):
    _install_binary(settings)
    monkeypatch.setattr(ps, "_port_open", lambda port: True)

    def _boom(*a, **k):
        raise AssertionError("should not spawn when the port is already open")

    monkeypatch.setattr(subprocess, "Popen", _boom)
    state = ps.start()
    assert state["running"] is True
    assert state["managed"] is False


def test_start_spawns_and_waits_for_port(settings, monkeypatch):
    _install_binary(settings)
    calls = {"n": 0}

    def _port_open(port):
        calls["n"] += 1
        return calls["n"] > 2  # closed on the first checks, then up

    monkeypatch.setattr(ps, "_port_open", _port_open)
    fake = _FakePopen()
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: fake)

    state = ps.start()
    assert state == {"installed": True, "running": True, "managed": True, "pid": 4242}


def test_start_detects_immediate_exit(settings, monkeypatch):
    _install_binary(settings)
    monkeypatch.setattr(ps, "_port_open", lambda port: False)
    dead = _FakePopen(alive=False)
    dead.returncode = 1
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: dead)
    with pytest.raises(OpenBooksProcessError, match="exited immediately"):
        ps.start()


def test_stop_terminates_managed_process(settings, monkeypatch):
    fake = _FakePopen()
    monkeypatch.setattr(ps, "_proc", fake)
    monkeypatch.setattr(ps, "_port_open", lambda port: False)
    state = ps.stop()
    assert fake.terminated is True
    assert state["managed"] is False
    assert ps._proc is None


def test_stop_kills_orphan_on_port(settings, monkeypatch):
    monkeypatch.setattr(ps, "_proc", None)
    monkeypatch.setattr(ps, "_port_open", lambda port: True)
    killed = {}
    monkeypatch.setattr(ps, "_kill_by_port", lambda port: killed.setdefault("port", port))
    ps.stop()
    assert killed["port"] == 5228


def test_shutdown_only_touches_managed(settings, monkeypatch):
    monkeypatch.setattr(ps, "_proc", None)
    called = {"kill": False}
    monkeypatch.setattr(ps, "_kill_by_port", lambda port: called.__setitem__("kill", True))
    ps.shutdown()
    assert called["kill"] is False  # never kills an instance it didn't start
