"""Repeatable M5 browser acceptance over a real server and a real browser.

    uv run --frozen python scripts/m5_acceptance.py --browsers chromium
    uv run --frozen python scripts/m5_acceptance.py --browsers chromium,firefox,webkit

The script owns the whole run: it publishes the composed fixtures into a fresh
data dir, starts the built app with uvicorn, seeds verified design sessions,
drives Playwright (frontend/acceptance/m5-browser.mjs) through the full phase,
restarts the server, runs the recovery phase, then aggregates every check. It
exits non-zero if any check fails, so CI can gate on it.

Requires: the frontend built once (``npm run build``), the ``playwright``
devDependency installed, and the browser binaries (``npx playwright install``).
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from m5_acceptance_fixtures import publish_all, seed_designs  # noqa: E402

REQUIRED_GAMES = ("alpha-pairs", "gamma-market", "eta-two", "theta-draw")
ALL_BROWSERS = ("chromium", "firefox", "webkit")


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def health(port: int, timeout: float = 2.0) -> bool:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=timeout) as reply:
            return json.loads(reply.read().decode())["status"] == "ok"
    except Exception:
        return False


def wait_health(port: int, timeout: float = 45.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if health(port):
            return
        time.sleep(0.4)
    raise SystemExit(f"server on port {port} did not become healthy")


def start_server(port: int, data_dir: Path, log_path: Path) -> subprocess.Popen:
    env = {**os.environ, "POCKER_AGENT_DATA_DIR": str(data_dir)}
    log = log_path.open("w", encoding="utf-8")
    process = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "pocker_agent.app:app",
         "--host", "127.0.0.1", "--port", str(port)],
        cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT)
    return process


def stop_server(process: subprocess.Popen) -> None:
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=10)


def run_phase(port: int, designs_path: Path, phase: str, browser: str, work: Path) -> None:
    out_dir = work / f"shots-{browser}"
    out_dir.mkdir(parents=True, exist_ok=True)
    profile = work / f"profile-{browser}"
    midgame = work / f"midgame-{browser}.json"
    base = f"http://127.0.0.1:{port}"
    command = ["node", "acceptance/m5-browser.mjs", base, str(designs_path), phase,
               browser, str(profile), str(out_dir), str(midgame)]
    result = subprocess.run(command, cwd=ROOT / "frontend")
    if result.returncode not in (0, 1):            # 1 = checks failed, handled below
        raise SystemExit(f"browser harness crashed (exit {result.returncode})")


def collect(work: Path, browser: str, phase: str) -> list[dict]:
    path = work / f"shots-{browser}" / f"m5-browser-{browser}-{phase}-results.json"
    if not path.is_file():
        return [{"name": f"{browser}/{phase}: results written", "ok": False,
                 "detail": "missing result file"}]
    return json.loads(path.read_text(encoding="utf-8"))["results"]


def run_tool(command: list[str], cwd: Path) -> None:
    """Run an external tool, resolving Windows ``.cmd`` shims through cmd.exe."""
    executable = shutil.which(command[0])
    if executable is None:
        raise SystemExit(f"{command[0]} was not found on PATH")
    resolved = [executable, *command[1:]]
    if os.name == "nt" and executable.lower().endswith((".cmd", ".bat")):
        resolved = [os.environ.get("COMSPEC", "cmd.exe"), "/c", *resolved]
    subprocess.run(resolved, cwd=cwd, check=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--browsers", default="chromium",
                        help=f"comma-separated subset of {', '.join(ALL_BROWSERS)}")
    parser.add_argument("--work-dir", default=None, help="where runtime/results go")
    parser.add_argument("--skip-build", action="store_true",
                        help="use the existing frontend/dist")
    args = parser.parse_args()

    browsers = [item.strip() for item in args.browsers.split(",") if item.strip()]
    unknown = [item for item in browsers if item not in ALL_BROWSERS]
    if unknown:
        raise SystemExit(f"unknown browsers: {unknown}")
    if shutil.which("node") is None:
        raise SystemExit("node is required for the browser acceptance")
    if not (ROOT / "frontend" / "node_modules" / "playwright").is_dir():
        raise SystemExit("playwright is not installed; run `cd frontend && npm install`")

    dist = ROOT / "frontend" / "dist" / "index.html"
    if not args.skip_build:
        if shutil.which("npm") is None:
            raise SystemExit("npm is required to build the frontend")
        run_tool(["npm", "run", "build"], cwd=ROOT / "frontend")
    elif not dist.is_file():
        raise SystemExit("frontend/dist is missing; run `cd frontend && npm run build`")

    work = (Path(args.work_dir).resolve() if args.work_dir
            else ROOT / "artifacts" / "m5-acceptance" / f"run-{int(time.time())}")
    work.mkdir(parents=True, exist_ok=True)

    all_results: list[dict] = []
    for browser in browsers:
        # A fresh data dir per browser: the design flows register their games, so
        # a shared DB would make the next browser's first publish idempotent.
        data_dir = work / f"runtime-{browser}"
        data_dir.mkdir(parents=True, exist_ok=True)
        published = publish_all(data_dir / "pocker.db")
        broken = [game for game in REQUIRED_GAMES if not published.get(game, {}).get("ok")]
        if broken:
            raise SystemExit(f"fixture publish failed: {broken} -> {published}")
        print(f"published fixtures [{browser}]:",
              {game: data["version"] for game, data in published.items() if data.get("ok")})
        port = free_port()
        server = start_server(port, data_dir, work / f"server-{browser}.log")
        try:
            wait_health(port)
            base = f"http://127.0.0.1:{port}"
            designs = seed_designs(base)
            designs_path = work / f"designs-{browser}.json"
            designs_path.write_text(json.dumps(designs, ensure_ascii=False, indent=2),
                                    encoding="utf-8")
            print(f"\n=== {browser}: full phase (port {port}) ===")
            run_phase(port, designs_path, "full", browser, work)
            all_results += collect(work, browser, "full")

            print(f"=== {browser}: restart backend ===")
            stop_server(server)
            server = start_server(port, data_dir, work / f"server-{browser}-restart.log")
            wait_health(port)
            print(f"=== {browser}: recover phase ===")
            run_phase(port, designs_path, "recover", browser, work)
            all_results += collect(work, browser, "recover")
        finally:
            stop_server(server)

    passed = [item for item in all_results if item["ok"]]
    failed = [item for item in all_results if not item["ok"]]
    summary = {"work_dir": str(work), "browsers": browsers,
               "checks": len(all_results), "passed": len(passed), "failed": len(failed),
               "failures": failed}
    (work / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2),
                                      encoding="utf-8")
    print(f"\n{len(passed)}/{len(all_results)} browser checks passed "
          f"({', '.join(browsers)}); results in {work}")
    if failed:
        print("FAILURES:", ", ".join(item["name"] for item in failed))
        raise SystemExit(1)


if __name__ == "__main__":
    main()
