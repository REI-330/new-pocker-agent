"""M6 blind generation acceptance (ADR-0020).

Runs the frozen blind case set through the whole lifecycle:

    describe -> messages (real model) -> verify -> confirm -> publish -> play

Each case gets its own design session, and the report records the model, prompt
fingerprint, budget usage, artifact/IR hashes, the verification id and a
mutually-exclusive failure class. The four metrics (mechanism exists / IR
compiles / verified / playable) stay separate.

Two modes:

    # real run against a configured model (the only mode that scores generation)
    uv run --frozen python scripts/m6_generation_acceptance.py --base-url http://127.0.0.1:8012

    # flow-only: a deterministic model proves the harness works; never scored
    uv run --frozen python scripts/m6_generation_acceptance.py --scripted

Evidence goes to artifacts/g2/<git-rev>/<run-id>/ (report.json + summary.md).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import platform
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CASES_PATH = ROOT / "benchmarks" / "g2_blind_cases.json"
sys.path.insert(0, str(ROOT / "tests"))

#: The surfaces frozen for a blind run; a change invalidates the run.
FROZEN_FILES = [
    "src/pocker_agent/core/interpreter.py",
    "src/pocker_agent/core/session.py",
    "src/pocker_agent/core/plan.py",
    "src/pocker_agent/core/ir.py",
    "src/pocker_agent/core/zones.py",
    "src/pocker_agent/core/actions.py",
    "src/pocker_agent/core/rules/compiler.py",
    "src/pocker_agent/core/rules/composed.py",
    "src/pocker_agent/core/rules/structure.py",
    "src/pocker_agent/agent/design_loop.py",
    "src/pocker_agent/agent/design_service.py",
    "src/pocker_agent/agent/meta_tools.py",
]
BLIND_TARGET = 6
BLIND_TOTAL = 8


def decision(tool: str, **args) -> str:
    return json.dumps({"tool": tool, "args": args})


def frozen_fingerprint() -> dict[str, str]:
    """Content hash of every frozen file, so a run names its exact frozen面."""
    return {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest()[:16]
            for name in FROZEN_FILES if (ROOT / name).is_file()}


def load_cases(path: Path) -> list[dict]:
    return json.loads(path.read_text(encoding="utf-8"))


def git_revision() -> dict[str, str]:
    def run(*args: str) -> str:
        try:
            return subprocess.run(["git", *args], cwd=ROOT, capture_output=True,
                                  text=True, check=True).stdout.strip()
        except Exception:
            return "unknown"
    return {"head": run("rev-parse", "HEAD"), "dirty": run("status", "--porcelain")}


class ScriptedModel:
    """A deterministic stand-in; only proves the harness (ADR-0020 §3)."""

    def __init__(self, responses: list[str]) -> None:
        self.responses = list(responses)
        self.calls = 0

    def complete(self, messages, **kwargs):
        if self.calls >= len(self.responses):
            raise RuntimeError("scripted responses exhausted")
        response = self.responses[self.calls]
        self.calls += 1
        return response


def scripted_responses(case: dict) -> list[str]:
    if case["expect"] == "unsupported":
        return [decision("unsupported", message=f"缺少 {case.get('missing', [])}",
                         missing=case.get("missing", []))]
    from test_g2_m2 import scenario_a_ir
    return [decision("propose_ir", ir=scenario_a_ir()), decision("compose_plan"),
            decision("verify_game"), decision("finalize")]


def classify(case: dict, record: dict) -> str:
    outcome = record["outcome"]
    if case["expect"] == "unsupported":
        return "correctly_unsupported" if outcome == "unsupported" else "interpretation"
    if outcome == "success":
        return "success"
    if outcome == "unsupported":
        return "missing_feature"
    if outcome == "error":
        # Repair exhaustion and a transport failure are model-side output/budget
        # problems, not composition failures, and must be counted as such.
        message = str(record.get("message") or "")
        if "repair_budget_exhausted" in message or "model_failed" in message:
            return "generation_budget"
        return "composition"
    return {"question": "interpretation",
            "budget_exhausted": "generation_budget",
            "verify_failed": "validation", "confirm_failed": "validation",
            "publish_failed": "validation", "unfinished": "UI", "play_rejected": "UI",
            "session_failed": "UI"}.get(outcome, "composition")


def _inputs_for(descriptor: dict) -> dict:
    return {item["id"]: item["options"][: item["min_count"]]
            for item in descriptor["inputs"] if item["min_count"] > 0}


def run_case(client, case: dict, max_turns: int) -> dict:
    record: dict = {"id": case["id"], "goal": case["goal"], "expected": case["expect"],
                    "turns": 0, "attempts": 0, "kinds": [], "used": {}}
    game_id = f"blind-{case['id']}"
    created = client.post("/api/designs", json={"game_id": game_id, "description": case["goal"]})
    if created.status_code != 200:
        record.update({"outcome": "error", "detail": created.text[:200]})
        return record
    session_id = created.json()["session_id"]
    revision = created.json()["revision"]
    last = None
    nudged = False
    for _ in range(max_turns):
        if last is None:
            message = case["goal"]
        elif last["kind"] == "question" and not nudged:
            # One automatic clarification: the blind case already contains the
            # full requirement, so tell the model to stop asking and finalize.
            nudged = True
            message = ("上述描述已包含全部规则，信息足够；请不要继续提问，"
                       "直接按描述提交最终规则并完成正式验证。")
        else:
            break
        response = client.post(f"/api/designs/{session_id}/messages",
                               json={"message": message,
                                     "expected_revision": revision})
        if response.status_code != 200:
            record.update({"outcome": "error", "detail": response.text[:200]})
            break
        last = response.json()
        record["turns"] += 1
        record["kinds"].append(last["kind"])
        record["attempts"] += int(last.get("attempts") or 0)
        record["used"] = last.get("used") or {}
        record["message"] = last.get("message")
        revision = last["revision"]
        if last["kind"] in ("finalized", "unsupported", "error", "budget_exhausted"):
            break
    if last is None:
        record["outcome"] = "generation_budget"
        return record
    if last["kind"] != "finalized":
        record["outcome"] = last["kind"]
        return record

    session = last["session"]
    if not (session.get("context", {}).get("verification") or {}).get("ok"):
        verified = client.post(f"/api/designs/{session_id}/verify",
                               json={"expected_revision": session["revision"]})
        if verified.status_code != 200:
            record.update({"outcome": "verify_failed", "detail": verified.text[:200]})
            return record
        session = verified.json()["session"]
    confirmed = client.post(f"/api/designs/{session_id}/confirm",
                            json={"ir_hash": session["ir_hash"],
                                  "expected_revision": session["revision"]})
    if confirmed.status_code != 200:
        record.update({"outcome": "confirm_failed", "detail": confirmed.text[:200]})
        return record
    published = client.post(f"/api/designs/{session_id}/publish",
                            json={"expected_revision": confirmed.json()["session"]["revision"]})
    if published.status_code != 200:
        record.update({"outcome": "publish_failed", "detail": published.text[:200]})
        return record
    artifact = published.json()["artifact"]
    record.update({"version": artifact["version"], "ir_hash": artifact["ir_hash"],
                   "verification_id": artifact["verification_id"],
                   "plan_hash": artifact["plan_hash"],
                   "generation_source": artifact["generation_source"]})

    opened = client.post("/api/sessions",
                         json={"game_id": game_id, "version": artifact["version"], "seed": 7})
    if opened.status_code != 200:
        record.update({"outcome": "session_failed", "detail": opened.text[:200]})
        return record
    state = opened.json()
    actions = 0
    while not state["finished"] and actions < 4096:
        descriptors = state.get("actions") or []
        if not descriptors:
            break
        descriptor = next((item for item in descriptors if item["inputs"]), descriptors[0])
        acted = client.post(f"/api/sessions/{state['session_id']}/actions",
                            json={"action_id": descriptor["id"],
                                  "input_values": _inputs_for(descriptor),
                                  "revision": state["revision"]})
        if acted.status_code != 200:
            record.update({"outcome": "play_rejected", "detail": acted.text[:200]})
            return record
        state = acted.json()["state"]
        actions += 1
    record["actions"] = actions
    expected_actions = (case.get("evidence") or {}).get("min_actions", 1)
    record["expected_min_actions"] = expected_actions
    if state["finished"] and actions >= expected_actions:
        record["outcome"] = "success"
    elif state["finished"]:
        record.update({"outcome": "unfinished",
                       "detail": f"finished below independent expectation ({actions} < {expected_actions})"})
    else:
        record["outcome"] = "unfinished"
    return record


def environment() -> dict:
    return {"python": platform.python_version(), "node": _node_version(),
            "platform": platform.platform(), "cwd": str(ROOT)}


def _node_version() -> str:
    try:
        return subprocess.run(["node", "--version"], capture_output=True, text=True,
                              check=True).stdout.strip()
    except Exception:
        return "not found"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", default=str(CASES_PATH))
    parser.add_argument("--base-url", default=None, help="a running server with a saved model")
    parser.add_argument("--scripted", action="store_true", help="flow-only, never scored")
    parser.add_argument("--max-turns", type=int, default=6)
    parser.add_argument("--output", default=None)
    args = parser.parse_args()
    if not args.base_url and not args.scripted:
        parser.error("pass --base-url (real run) or --scripted (flow only)")

    cases = load_cases(Path(args.cases))
    mode = "scripted-flow-only" if args.scripted else "real-model"
    model_info: dict = {}
    records: list[dict] = []

    if args.scripted:
        import tempfile

        from fastapi.testclient import TestClient

        from pocker_agent.app import create_app
        for case in cases:
            print(f"[m6] {case['id']} …", flush=True)
            with tempfile.TemporaryDirectory() as folder:
                app = create_app(Path(folder) / "m6.db",
                                 model_factory=lambda case=case: ScriptedModel(scripted_responses(case)))
                records.append(run_case(TestClient(app), case, args.max_turns))
            print(f"[m6] {case['id']} -> {records[-1]['outcome']}", flush=True)
        model_info = {"provider": "scripted", "model": "ScriptedModel"}
    else:
        import httpx
        with httpx.Client(base_url=args.base_url, timeout=300) as client:
            model_info = client.get("/api/agent/config").json()
            print(f"[m6] model {model_info.get('model')} @ {model_info.get('base_url')}", flush=True)
            for case in cases:
                print(f"[m6] {case['id']} …", flush=True)
                records.append(run_case(client, case, args.max_turns))
                print(f"[m6] {case['id']} -> {records[-1]['outcome']}", flush=True)

    for case, record in zip(cases, records):
        record["failure_class"] = classify(case, record)

    composed = [case for case in cases if case["expect"] == "composed"]
    negatives = [case for case in cases if case["expect"] == "unsupported"]
    successes = [r for r in records if r["failure_class"] == "success"]
    correct_negatives = [r for r in records if r["failure_class"] == "correctly_unsupported"]
    compiled = [r for r in records if r.get("version") is not None or r["failure_class"] == "success"]
    verified = [r for r in records if r.get("verification_id")]
    playable = successes

    targets = {"blind_success": len(successes) >= BLIND_TARGET,
               "negatives_rejected": len(correct_negatives) == len(negatives),
               "cases_present": len(composed) >= BLIND_TOTAL}
    report = {
        "mode": mode,
        "started_at": datetime.now(UTC).isoformat(),
        "revision": git_revision(),
        "environment": environment(),
        "model": {key: model_info.get(key) for key in ("base_url", "model", "configured")},
        "frozen_fingerprint": frozen_fingerprint(),
        "cases": records,
        "metrics": {
            "mechanism_present": f"{len(composed)}/{len(composed)} (pre-assessed expressible)",
            "ir_compilable": f"{len(compiled)}/{len(composed)}",
            "generated_and_verified": f"{len(verified)}/{len(composed)}",
            "playable": f"{len(playable)}/{len(composed)}",
        },
        "targets": targets,
        "targets_met": (all(targets.values()) if mode == "real-model" else None),
    }

    output = Path(args.output) if args.output else (
        ROOT / "artifacts" / "g2" / report["revision"]["head"][:12]
        / f"run-{int(time.time())}")
    output.mkdir(parents=True, exist_ok=True)
    (output / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2),
                                        encoding="utf-8")
    _write_summary(output / "summary.md", report)

    print(f"mode {mode} | blind success {len(successes)}/{len(composed)} "
          f"| negatives {len(correct_negatives)}/{len(negatives)}")
    print("failure classes:", _histogram(records))
    print("report:", output / "report.json")
    if mode == "real-model" and not report["targets_met"]:
        return 1
    return 0


def _histogram(records: list[dict]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for record in records:
        counts[record["failure_class"]] = counts.get(record["failure_class"], 0) + 1
    return counts


def _write_summary(path: Path, report: dict) -> None:
    lines = [f"# M6 generation acceptance ({report['mode']})", "",
             f"- revision: `{report['revision']['head']}` (dirty: `{report['revision']['dirty'] or 'clean'}`)",
             f"- model: `{report['model']['model']}` @ `{report['model']['base_url']}`",
             f"- python: {report['environment']['python']} · node: {report['environment']['node']}",
             f"- started: {report['started_at']}", "",
             "## Metrics", ""]
    lines += [f"- {name}: {value}" for name, value in report["metrics"].items()]
    lines += ["", "## Targets", ""]
    if report["targets_met"] is None:
        lines.append("- flow-only run: thresholds not evaluated")
    else:
        lines += [f"- {name}: {'PASS' if ok else 'FAIL'}" for name, ok in report["targets"].items()]
    lines += ["", "## Cases", "",
              "| id | expected | outcome | class | turns | actions | version |",
              "|---|---|---|---|---|---|---|"]
    for record in report["cases"]:
        lines.append(f"| {record['id']} | {record['expected']} | {record.get('outcome')} | "
                     f"{record['failure_class']} | {record.get('turns')} | "
                     f"{record.get('actions', '')} | {record.get('version', '')} |")
    lines += ["", "## Frozen fingerprint", ""]
    lines += [f"- `{name}`: {value}" for name, value in report["frozen_fingerprint"].items()]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
