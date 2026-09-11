"""A stale server is the one failure the browser cannot explain by itself.

The app module is imported once at startup, so a process that outlives an edit
keeps serving the old API while the page loads the new bundle. Requests then
fail in a way that looks like a frontend bug ("the design box cannot send").
These tests pin the diagnostics that name the real cause.
"""
from __future__ import annotations

import importlib.util
import socket
import threading
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _load(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


run_web = _load("run_web", "run_web.py")
doctor = _load("doctor", "doctor.py")


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def test_a_busy_port_says_whether_that_process_is_current(capsys):
    """The launcher used to fall back to another port in silence."""
    run_web.report_busy_port(8000, {"pid": 42, "code": "aaa"}, "aaa")
    current = capsys.readouterr().out
    assert "pid 42" in current and "current code" in current

    run_web.report_busy_port(8000, {"pid": 42, "code": "old"}, "new")
    stale = capsys.readouterr().out
    assert "older code" in stale
    assert "Stop-Process -Id 42" in stale          # the repair must be copy-pasteable


def test_health_identifies_this_app_and_nothing_else():
    """`health` must not mistake an unrelated service for the app."""
    assert run_web.health(free_port(), timeout=0.5) is None


@pytest.fixture
def live_server(tmp_path):
    import uvicorn

    from pocker_agent.app import create_app

    port = free_port()
    config = uvicorn.Config(create_app(tmp_path / "live.db"), host="127.0.0.1", port=port,
                            log_level="warning")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(80):
        if getattr(server, "started", False):
            break
        time.sleep(0.25)
    try:
        yield port
    finally:
        server.should_exit = True
        thread.join(timeout=10)


def test_doctor_url_passes_against_a_server_running_the_current_code(live_server, capsys):
    doctor.FAILURES.clear()
    doctor.check_running_server(f"http://127.0.0.1:{live_server}")
    report = capsys.readouterr().out
    assert doctor.FAILURES == [], report
    assert "[PASS] server code matches the working tree" in report
    assert "[PASS] POST /api/agent/loop rejects an empty message with its own words" in report


def test_doctor_url_flags_a_server_whose_code_moved_on(live_server, capsys, monkeypatch):
    """Simulate the exact incident: the process predates the working tree.

    The served side keeps the fingerprint it imported; only the on-disk side
    moves, which is precisely what an edit-after-start looks like from outside.
    """
    import pocker_agent.app as app_module

    monkeypatch.setattr(app_module, "code_fingerprint", lambda: "0" * 12)
    doctor.FAILURES.clear()
    doctor.check_running_server(f"http://127.0.0.1:{live_server}")
    report = capsys.readouterr().out
    assert "server code matches the working tree" in doctor.FAILURES, report
    assert "the process is stale" in report
    assert "stop it and start it again" in report
