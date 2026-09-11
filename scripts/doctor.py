"""Diagnose why the web app will not start, then optionally self-test it.

    uv run python scripts/doctor.py            # checks only
    uv run python scripts/doctor.py --serve    # checks, then start + self-test

Every check prints PASS/FAIL with the concrete value, so the output alone is
enough to locate a problem.
"""
from __future__ import annotations

import argparse
import importlib
import json
import re
import socket
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FAILURES: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print(f"[{'PASS' if ok else 'FAIL'}] {label}{(' :: ' + detail) if detail else ''}")
    if not ok:
        FAILURES.append(label)


def port_state(port: int) -> tuple[bool, str]:
    with socket.socket() as probe:
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            probe.bind(("127.0.0.1", port))
            return True, "free"
        except OSError as error:
            return False, f"in use ({error.errno})"


def http(path: str, port: int, timeout: float = 5.0):
    url = f"http://127.0.0.1:{port}{path}"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            return response.status, response.read().decode()
    except urllib.error.HTTPError as error:
        return error.code, error.read().decode()
    except Exception as error:
        return None, str(error)


def diagnose(port: int) -> None:
    print(f"cwd            : {Path.cwd()}")
    print(f"repo root      : {ROOT}")
    check("cwd is the repo root", Path.cwd().resolve() == ROOT.resolve(),
          "run the launcher from the repository root")
    print(f"python         : {sys.executable}")
    print(f"python version : {sys.version.split()[0]}")

    for module in ("fastapi", "uvicorn", "pydantic", "openai", "keyring"):
        try:
            imported = importlib.import_module(module)
            print(f"  {module:<9} : {getattr(imported, '__version__', 'ok')}")
        except Exception as error:
            check(f"import {module}", False, str(error))

    try:
        package = importlib.import_module("pocker_agent")
        origin = getattr(package, "__file__", "?")
        check("import pocker_agent", True, origin)
        from pocker_agent.app import create_app  # noqa: F401
        check("import pocker_agent.app", True)
        import pocker_agent.core as core  # noqa: F401
        check("import pocker_agent.core", True)
    except Exception as error:
        check("import pocker_agent.app", False, f"{type(error).__name__}: {error}")

    legacy = importlib.util.find_spec("pocker_agent.api") if importlib.util else None
    check("legacy pocker_agent.api is gone (expected)", legacy is None,
          "old uvicorn commands pointing at pocker_agent.api cannot work")

    dist = ROOT / "frontend" / "dist" / "index.html"
    check("frontend/dist/index.html exists", dist.is_file(),
          "run: npm run build --prefix frontend   (or drop --skip-build)")

    free, detail = port_state(port)
    check(f"port {port} is free", free, detail)

    if not free:
        print(f"       inspect with: netstat -ano | findstr :{port}")
        print("       or just let the launcher pick another port")


def serve_and_self_test(port: int) -> int:
    free, detail = port_state(port)
    if not free:
        print(f"port {port} is busy ({detail}); pick another with --port")
        return 1
    import uvicorn

    config = uvicorn.Config("pocker_agent.app:app", host="127.0.0.1", port=port, log_level="warning")
    server = uvicorn.Server(config)
    import threading

    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(60):
        if getattr(server, "started", False):
            break
        time.sleep(0.25)
    try:
        status, body = http("/health", port)
        check("GET /health", status == 200, f"{status} {body[:60]}")
        status, body = http("/", port)
        check("GET / serves the app", status == 200 and 'id="root"' in body, str(status))
        # The page is useless if its own bundle 404s (the /assets mount is the
        # card SVG directory, so the build must emit its bundle elsewhere).
        referenced = re.findall(r'(?:src|href)="(/(?:static|assets)[^"]*)"', body)
        check("page references its bundle", bool(referenced), str(referenced))
        for url in referenced:
            asset_status, _ = http(url, port)
            check(f"GET {url}", asset_status == 200, str(asset_status))
        card_status, _ = http("/assets/cards/ace_of_spades.svg", port)
        check("GET card asset", card_status == 200, str(card_status))
        status, body = http("/api/games", port)
        ok = status == 200 and "games" in body
        count = len(json.loads(body)["games"]) if ok else 0
        check("GET /api/games", ok, f"{count} games")
        status, body = http("/api/capabilities", port)
        check("GET /api/capabilities", status == 200, str(status))
    finally:
        server.should_exit = True
        thread.join(timeout=10)

    print("")
    if FAILURES:
        print(f"{len(FAILURES)} check(s) failed: {FAILURES}")
        return 1
    print(f"all checks passed; the app serves correctly on http://127.0.0.1:{port}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8098)
    parser.add_argument("--serve", action="store_true",
                        help="start the app in-process and self-test the HTTP endpoints")
    args = parser.parse_args()
    diagnose(args.port)
    if args.serve:
        print("")
        return serve_and_self_test(args.port)
    print("")
    print("run with --serve to start it in-process and self-test, or use scripts/run_web.py")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    raise SystemExit(main())
