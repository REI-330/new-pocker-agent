"""Build the frontend (if needed) and serve the app on one port.

Preferred launcher: it needs no PowerShell, so it is immune to execution-policy
and .ps1 encoding issues.

    uv run python scripts/run_web.py
    uv run python scripts/run_web.py --port 8899 --skip-build

If the requested port is busy the next free port is used and printed; when the
busy port is *already* this app, the launcher says whether that process is
current or stale, so nobody keeps using an old server by accident.
"""
from __future__ import annotations

import argparse
import json
import socket
import subprocess
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def health(port: int, timeout: float = 2.0) -> dict | None:
    """Identify what is already listening, when it answers like this app."""
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=timeout) as reply:
            payload = json.loads(reply.read().decode("utf-8"))
    except Exception:
        return None
    return payload if isinstance(payload, dict) and payload.get("status") == "ok" else None


def report_busy_port(port: int, running: dict, current: str) -> None:
    """A stale server on the requested port is the classic "the page cannot send" trap."""
    pid, code = running.get("pid"), running.get("code")
    print(f"note: http://127.0.0.1:{port} is already serving Pocker Agent "
          f"(pid {pid}, code {code})", flush=True)
    if code == current:
        print("      that process runs the current code; just open it, "
              "or stop it first if you want a fresh start", flush=True)
    else:
        print(f"      that process runs older code (this tree is {current}); it will "
              f"reject requests the page makes", flush=True)
        print(f"      stop it to pick up your edits:  Stop-Process -Id {pid}", flush=True)


def free_port(preferred: int) -> int:
    with socket.socket() as probe:
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            probe.bind(("127.0.0.1", preferred))
            return preferred
        except OSError:
            pass
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def npm_command() -> str:
    from shutil import which

    found = which("npm.cmd") or which("npm")
    if not found:
        raise SystemExit("npm was not found on PATH; install Node.js, or run with --skip-build "
                         "if frontend/dist already exists")
    return found


def build_frontend() -> None:
    npm = npm_command()
    if not (ROOT / "frontend" / "node_modules").is_dir():
        print("installing frontend dependencies (first run)...", flush=True)
        subprocess.check_call([npm, "ci", "--prefix", "frontend"], cwd=ROOT)
    print("building frontend...", flush=True)
    subprocess.check_call([npm, "run", "build", "--prefix", "frontend"], cwd=ROOT)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--skip-build", action="store_true",
                        help="serve the existing frontend/dist without rebuilding")
    args = parser.parse_args()

    if not args.skip_build:
        build_frontend()
    page = ROOT / "frontend" / "dist" / "index.html"
    if not page.is_file():
        print("warning: frontend/dist/index.html is missing; the API will run but the page "
              "will 404 (rebuild without --skip-build)", file=sys.stderr)

    port = free_port(args.port)
    if port != args.port:
        print(f"port {args.port} is already in use; using {port} instead", flush=True)
        running = health(args.port)
        if running is not None:
            from pocker_agent.app import CODE_FINGERPRINT

            report_busy_port(args.port, running, CODE_FINGERPRINT)
    print("", flush=True)
    print(f"Pocker Agent web app:  http://127.0.0.1:{port}", flush=True)
    print("  library   -> pick a game and press start", flush=True)
    print("  new game  -> needs a model saved in Model Settings", flush=True)
    print("  Ctrl+C to stop", flush=True)
    print("", flush=True)

    import uvicorn

    uvicorn.run("pocker_agent.app:app", host="127.0.0.1", port=port, log_level="info")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
