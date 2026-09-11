"""Real end-to-end smoke test over HTTP against a running v0.4 app.

Unlike the pytest suite (TestClient), this drives a live uvicorn process:
capabilities -> games -> create session -> play a full arithmetic game -> verify
persistence, stale-revision conflict, and that the built frontend is served.

Usage:
    uvicorn pocker_agent.app:app --port 8765   (in another shell)
    uv run python scripts/e2e_smoke.py http://127.0.0.1:8765
"""
from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.request

from pocker_agent.arithmetic import solve

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8765"
PASSED: list[str] = []


def request(path: str, method: str = "GET", body: dict | None = None) -> tuple[int, object]:
    data = None if body is None else json.dumps(body).encode()
    headers = {"Content-Type": "application/json"} if data else {}
    req = urllib.request.Request(BASE + path, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=15) as response:
            raw = response.read().decode()
            return response.status, (json.loads(raw) if raw.startswith(("{", "[")) else raw)
    except urllib.error.HTTPError as error:
        raw = error.read().decode()
        return error.code, (json.loads(raw) if raw.startswith(("{", "[")) else raw)


def check(label: str, condition: bool, detail: str = "") -> None:
    if not condition:
        raise SystemExit(f"FAIL {label} {detail}")
    PASSED.append(label)
    print(f"PASS {label}")


def wait_for_health() -> None:
    for _ in range(60):
        try:
            status, _ = request("/health")
            if status == 200:
                return
        except OSError:
            pass
        time.sleep(0.5)
    raise SystemExit("FAIL server did not become healthy")


def main() -> None:
    wait_for_health()
    check("health", request("/health")[0] == 200)

    status, capabilities = request("/api/capabilities")
    check("capabilities_status", status == 200, str(status))
    coverage = capabilities["coverage"]  # type: ignore[index]
    check("coverage_total", coverage["total"] >= 20, str(coverage["total"]))
    check("coverage_has_gaps", "turn_adapter" in coverage["missing_histogram"])

    status, games = request("/api/games")
    arithmetic = next(game for game in games["games"] if game["id"] == "arithmetic24")  # type: ignore[index]
    check("game_playtested", arithmetic["playtest"]["ok"] is True)

    status, state = request("/api/sessions", "POST", {"game_id": "arithmetic24", "seed": 7})
    check("session_created", status == 200 and state["revision"] == 0, str(status))  # type: ignore[index]
    session_id = state["session_id"]  # type: ignore[index]

    rounds = 0
    for _ in range(32):
        if state["finished"]:  # type: ignore[index]
            break
        answer = solve(tuple(state["numbers"]))  # type: ignore[index]
        payload = ({"revision": state["revision"], "expression": answer}  # type: ignore[index]
                   if answer else {"revision": state["revision"]})  # type: ignore[index]
        action = "submit_expression" if answer else "no_solution"
        status, response = request(f"/api/sessions/{session_id}/actions/{action}", "POST", payload)
        if status != 200:
            raise SystemExit(f"FAIL play action {action}: {status} {response}")
        state = response["state"]  # type: ignore[index]
        rounds += 1
    check("game_finished", state["finished"] is True)  # type: ignore[index]
    check("rounds_played", rounds == 3, str(rounds))
    check("score_is_full", state["players"][0]["score"] == 3, str(state["players"]))  # type: ignore[index]
    check("no_invented_winner", state["winners"] == [])  # type: ignore[index]

    status, persisted = request(f"/api/sessions/{session_id}")
    check("session_persisted", status == 200 and persisted == state)  # type: ignore[comparison-overlap]

    status, _conflict = request(f"/api/sessions/{session_id}/actions/give_up", "POST", {"revision": 0})
    check("stale_revision_conflict", status == 409, str(status))

    status, page = request("/")
    check("frontend_served", status == 200 and "root" in str(page), str(status))
    status, _ = request("/assets/cards/ace_of_spades.svg")
    check("card_assets_served", status == 200, str(status))

    status, tools = request("/api/agent/tools")
    check("agent_meta_tools_exposed",
          status == 200 and any(tool["name"] == "finalize" for tool in tools["meta_tools"]),  # type: ignore[index]
          str(status))
    status, denied = request("/api/agent/loop", "POST", {"goal": "做一个比大小"})
    check("agent_loop_fails_safely_without_model",
          status == 422 and "模型配置" in str(denied), str(status))

    status, games_all = request("/api/games")
    crazy = next((game for game in games_all["games"] if game["id"] == "crazy_eights"), None)  # type: ignore[index]
    check("crazy_eights_is_playtested",
          crazy is not None and crazy["playtest"]["ok"] is True, str(crazy))
    status, hidden = request("/api/sessions", "POST", {"game_id": "crazy_eights", "seed": 5})
    check("hidden_hands_are_private",
          status == 200 and hidden["players"][1]["hand"] == [] and hidden["players"][1]["hidden_count"] == 5,  # type: ignore[index]
          str(status))
    check("own_hand_is_visible", len(hidden["players"][0]["hand"]) == 5)  # type: ignore[index]

    print(f"\n{len(PASSED)} checks passed against {BASE}")


if __name__ == "__main__":
    main()
