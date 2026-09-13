"""M6 blind generation acceptance (ADR-0020).

Runs the frozen blind case set through the whole lifecycle:

    describe -> messages (real model) -> verify -> confirm -> publish -> play

Every case is judged by an *independent* evaluator (``m6_evidence``), not by
``finalized`` alone: the registered product must match the case's own
expectations (zones, visibility, terminal, action budget, hidden projection,
hash binding). The report separates ``runtime_playable`` (this harness plays it
over HTTP) from ``browser_playable`` (a real browser smoke), and it refuses to
score a run whose frozen surface changed or whose case set is invalid.

Modes:

    # real run against a configured model
    uv run --frozen python scripts/m6_generation_acceptance.py --base-url http://127.0.0.1:8012

    # flow-only: a deterministic model exercises the harness; never scored
    uv run --frozen python scripts/m6_generation_acceptance.py --scripted

Evidence goes to artifacts/g2/<git-rev>/<run-id>/ (report.json + summary.md).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import subprocess
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CASES_PATH = ROOT / "benchmarks" / "g2_blind_cases.json"
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tests"))

from m6_evidence import evaluate  # noqa: E402

#: The surfaces frozen for a blind run: mechanism, compiler, verifier, registry
#: and the design prompt/tools. A change invalidates the run.
FROZEN_FILES = [
    "src/pocker_agent/core/interpreter.py",
    "src/pocker_agent/core/session.py",
    "src/pocker_agent/core/plan.py",
    "src/pocker_agent/core/ir.py",
    "src/pocker_agent/core/zones.py",
    "src/pocker_agent/core/actions.py",
    "src/pocker_agent/core/capability.py",
    "src/pocker_agent/core/registry.py",
    "src/pocker_agent/core/artifacts.py",
    "src/pocker_agent/core/rules/compiler.py",
    "src/pocker_agent/core/rules/composed.py",
    "src/pocker_agent/core/rules/structure.py",
    "src/pocker_agent/core/rules/requirements.py",
    "src/pocker_agent/core/rules/expr.py",
    "src/pocker_agent/core/verify/service.py",
    "src/pocker_agent/core/verify/contract_check.py",
    "src/pocker_agent/agent/design_loop.py",
    "src/pocker_agent/agent/design_service.py",
    "src/pocker_agent/agent/meta_tools.py",
]
BLIND_TARGET = 6
BLIND_TOTAL = 8
NEGATIVE_MIN = 2
TERMINALS = {"highest_score", "score_reaches", "actor_action_budget", "max_rounds"}


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def sha256_file(path: Path) -> str | None:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()[:16]
    except OSError:
        return None


def freeze_snapshot(cases_path: Path) -> dict:
    """Hash the whole frozen surface, the prompt/tool schema and the case file."""
    files = {name: sha256_file(ROOT / name) for name in FROZEN_FILES}
    try:
        from pocker_agent.agent import DESIGN_TOOL_SCHEMAS
        from pocker_agent.agent.design_loop import DESIGN_SYSTEM_PROMPT
        prompt = sha256_text(DESIGN_SYSTEM_PROMPT)
        tools = sha256_text(json.dumps(DESIGN_TOOL_SCHEMAS, sort_keys=True, ensure_ascii=False))
    except Exception:                                   # a broken import is not silently ignored
        prompt = tools = None
    return {"files": files, "prompt": prompt, "tools": tools,
            "cases": sha256_file(cases_path),
            "missing": [name for name, value in files.items() if value is None]}


def freeze_diff(before: dict, after: dict) -> list[str]:
    changed = []
    if before.get("prompt") != after.get("prompt"):
        changed.append("prompt")
    if before.get("tools") != after.get("tools"):
        changed.append("tools")
    if before.get("cases") != after.get("cases"):
        changed.append("cases")
    for name in set(before.get("files", {})) | set(after.get("files", {})):
        if before["files"].get(name) != after["files"].get(name):
            changed.append(name)
    return sorted(set(changed))


def validate_cases(cases: list[dict]) -> list[str]:
    """A blind run needs its frozen shape, not an arbitrary file."""
    errors: list[str] = []
    ids = [case.get("id") for case in cases]
    if len(set(ids)) != len(ids):
        errors.append("case ids are not unique")
    composed = [case for case in cases if case.get("expect") == "composed"]
    negatives = [case for case in cases if case.get("expect") == "unsupported"]
    if len(composed) != BLIND_TOTAL:
        errors.append(f"expected {BLIND_TOTAL} composed cases, found {len(composed)}")
    if len(negatives) < NEGATIVE_MIN:
        errors.append(f"expected >= {NEGATIVE_MIN} unsupported cases, found {len(negatives)}")
    for case in cases:
        if case.get("expect") not in ("composed", "unsupported"):
            errors.append(f"{case.get('id')}: invalid expect")
        if case.get("expect") == "unsupported" and not case.get("missing"):
            errors.append(f"{case.get('id')}: unsupported case needs missing axes")
    for case in composed:
        evidence = case.get("evidence") or {}
        for key in ("players", "terminal", "min_actions"):
            if key not in evidence:
                errors.append(f"{case.get('id')}: evidence.{key} missing")
        terminal = evidence.get("terminal")
        if terminal not in TERMINALS:
            errors.append(f"{case.get('id')}: unknown terminal {terminal}")
        if terminal == "score_reaches" and "score_reaches" not in evidence:
            errors.append(f"{case.get('id')}: score_reaches threshold missing")
        if terminal == "actor_action_budget" and "max_actor_actions" not in evidence:
            errors.append(f"{case.get('id')}: max_actor_actions threshold missing")
    return errors


def load_cases(path: Path) -> list[dict]:
    return json.loads(path.read_text(encoding="utf-8"))


def decision(tool: str, **args) -> str:
    return json.dumps({"tool": tool, "args": args})


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
    if outcome == "transport_failure":
        return "transport"
    if outcome == "evidence_mismatch":
        return record.get("evidence_failure_class") or "validation"
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
                               json={"message": message, "expected_revision": revision})
        if response.status_code != 200:
            record.update({"outcome": "error", "detail": response.text[:200]})
            break
        last = response.json()
        record["turns"] += 1
        record["kinds"].append(last["kind"])
        record["attempts"] += int(last.get("attempts") or 0)
        record["message"] = last.get("message")
        for key, value in (last.get("used") or {}).items():
            record["used"][key] = record["used"].get(key, 0) + value if isinstance(value, (int, float)) else value
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

    detail_response = client.get(f"/api/games/{game_id}/versions/{artifact['version']}")
    detail = detail_response.json() if detail_response.status_code == 200 else {}

    opened = client.post("/api/sessions",
                         json={"game_id": game_id, "version": artifact["version"], "seed": 7})
    if opened.status_code != 200:
        record.update({"outcome": "session_failed", "detail": opened.text[:200]})
        return record
    initial_state = opened.json()
    state = initial_state
    actions = []
    while not state["finished"] and len(actions) < 4096:
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
        actions.append(acted.json())
        state = acted.json()["state"]
    record["actions"] = len(actions)
    record["runtime_playable"] = bool(state["finished"])

    evidence = evaluate(case, artifact=artifact, rules=detail.get("rules"),
                        plan=detail.get("plan"), verification=detail.get("verification"),
                        initial_state=initial_state, final_state=state,
                        actions=actions, events=[])
    record["evidence_ok"] = evidence["ok"]
    record["evidence_checks"] = evidence["checks"]
    record["evidence_failure_class"] = evidence["failure_class"]
    if not state["finished"]:
        record["outcome"] = "unfinished"
    elif not evidence["ok"]:
        record["outcome"] = "evidence_mismatch"
    else:
        record["outcome"] = "success"
    return record


def environment() -> dict:
    return {"python": platform.python_version(), "node": _node_version(),
            "platform": platform.platform(), "cwd": str(ROOT),
            "executable": sys.executable,
            "pocker_agent_path": _module_path("pocker_agent"),
            "core_path": _module_path("pocker_agent.core")}


def _module_path(name: str) -> str | None:
    try:
        module = __import__(name, fromlist=["__file__"])
        return str(Path(module.__file__).resolve())
    except Exception:
        return None


def _node_version() -> str:
    try:
        return subprocess.run(["node", "--version"], capture_output=True, text=True,
                              check=True).stdout.strip()
    except Exception:
        return "not found"


def git_revision() -> dict[str, str]:
    def run(*args: str) -> str:
        try:
            return subprocess.run(["git", *args], cwd=ROOT, capture_output=True,
                                  text=True, check=True).stdout.strip()
        except Exception:
            return "unknown"
    return {"head": run("rev-parse", "HEAD"), "dirty": run("status", "--porcelain")}


def server_health(base_url: str | None) -> dict:
    if not base_url:
        return {}
    try:
        import httpx
        with httpx.Client(base_url=base_url, timeout=20) as client:
            return client.get("/health").json()
    except Exception as error:
        return {"error": f"{type(error).__name__}: {error}"}


def run_browser_smoke(work: Path, base_url: str, artifacts: list[dict]) -> dict:
    """A real browser smoke per registered artifact; {} when unavailable."""
    node = shutil_which("node")
    script = ROOT / "frontend" / "acceptance" / "m6-artifact-smoke.mjs"
    if node is None or not script.is_file() or not artifacts:
        return {}
    payload = work / "browser-artifacts.json"
    payload.write_text(json.dumps(artifacts, ensure_ascii=False), encoding="utf-8")
    output = work / "browser-smoke.json"
    result = subprocess.run([node, str(script), base_url, str(payload), str(output)],
                            cwd=ROOT / "frontend")
    if result.returncode != 0 or not output.is_file():
        return {}
    return {f"{item['game_id']}@{item['version']}": item for item in
            json.loads(output.read_text(encoding="utf-8"))}


def shutil_which(name: str) -> str | None:
    import shutil
    return shutil.which(name)


def _histogram(records: list[dict]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for record in records:
        counts[record["failure_class"]] = counts.get(record["failure_class"], 0) + 1
    return counts


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", default=str(CASES_PATH))
    parser.add_argument("--base-url", default=None, help="a running server with a saved model")
    parser.add_argument("--scripted", action="store_true", help="flow-only, never scored")
    parser.add_argument("--max-turns", type=int, default=6)
    parser.add_argument("--browser", action="store_true", help="run a browser smoke per artifact")
    parser.add_argument("--output", default=None)
    args = parser.parse_args()
    if not args.base_url and not args.scripted:
        parser.error("pass --base-url (real run) or --scripted (flow only)")

    cases_path = Path(args.cases)
    cases = load_cases(cases_path)
    errors = validate_cases(cases)
    if errors:
        raise SystemExit("invalid case set: " + "; ".join(errors))
    started_at = datetime.now(UTC).isoformat()
    freeze_before = freeze_snapshot(cases_path)
    if freeze_before["missing"]:
        raise SystemExit(f"frozen files missing: {freeze_before['missing']}")

    mode = "scripted-flow-only" if args.scripted else "real-model"
    model_info: dict = {}
    records: list[dict] = []
    output = Path(args.output) if args.output else _output_dir()
    output.mkdir(parents=True, exist_ok=False)
    if args.scripted:
        import tempfile

        from fastapi.testclient import TestClient

        from pocker_agent.app import create_app
        for case in cases:
            print(f"[m6] {case['id']} …", flush=True)
            try:
                with tempfile.TemporaryDirectory() as folder:
                    app = create_app(
                        Path(folder) / "m6.db",
                        model_factory=lambda case=case: ScriptedModel(scripted_responses(case)))
                    record = run_case(TestClient(app), case, args.max_turns)
            except Exception as error:
                record = {"id": case["id"], "goal": case["goal"],
                          "expected": case["expect"], "turns": 0, "attempts": 0,
                          "kinds": [], "used": {}, "outcome": "transport_failure",
                          "detail": f"{type(error).__name__}: {error}"}
            records.append(record)
            print(f"[m6] {case['id']} -> {record['outcome']}", flush=True)
        model_info = {"provider": "scripted", "model": "ScriptedModel"}
    else:
        import httpx
        with httpx.Client(base_url=args.base_url, timeout=300) as client:
            model_info = client.get("/api/agent/config").json()
            print(f"[m6] model {model_info.get('model')} @ {model_info.get('base_url')}", flush=True)
            for case in cases:
                print(f"[m6] {case['id']} …", flush=True)
                try:
                    record = run_case(client, case, args.max_turns)
                except Exception as error:            # a single case must not sink the run
                    record = {"id": case["id"], "goal": case["goal"],
                              "expected": case["expect"], "turns": 0, "attempts": 0,
                              "kinds": [], "used": {}, "outcome": "transport_failure",
                              "detail": f"{type(error).__name__}: {error}"}
                records.append(record)
                print(f"[m6] {case['id']} -> {record['outcome']}", flush=True)

    for case, record in zip(cases, records):
        record["failure_class"] = classify(case, record)

    # ---------------------------------------------------- optional browser smoke
    artifacts = [{"game_id": f"blind-{record['id']}", "version": record["version"]}
                 for record in records if record.get("version") is not None]
    browser = run_browser_smoke(output, args.base_url or "", artifacts) \
        if (args.browser and args.base_url) else {}
    for record in records:
        key = f"blind-{record['id']}@{record.get('version')}"
        record["browser_playable"] = browser.get(key, {}).get("ok") if browser else None

    freeze_after = freeze_snapshot(cases_path)
    changed = freeze_diff(freeze_before, freeze_after)
    frozen_ok = not changed

    composed = [case for case in cases if case["expect"] == "composed"]
    negatives = [case for case in cases if case["expect"] == "unsupported"]
    successes = [r for r in records if r["failure_class"] == "success"]
    correct_negatives = [r for r in records if r["failure_class"] == "correctly_unsupported"]
    compiled = [r for r, c in zip(records, cases)
                if c["expect"] == "composed" and r.get("version") is not None]
    verified = [r for r, c in zip(records, cases)
                if c["expect"] == "composed" and r.get("verification_id")]
    runtime_playable = [r for r, c in zip(records, cases)
                        if c["expect"] == "composed" and r.get("runtime_playable")]
    browser_playable = [r for r, c in zip(records, cases)
                        if c["expect"] == "composed" and r.get("browser_playable") is True]

    targets = {"blind_success": len(successes) >= BLIND_TARGET,
               "negatives_rejected": len(correct_negatives) == len(negatives),
               "frozen_surface_unchanged": frozen_ok,
               "cases_valid": True}
    report = {
        "schema": "m6-generation-report/1",
        "mode": mode,
        "started_at": started_at,
        "finished_at": datetime.now(UTC).isoformat(),
        "command": " ".join(sys.argv),
        "revision": git_revision(),
        "environment": environment(),
        "data_dir": os.environ.get("POCKER_AGENT_DATA_DIR"),
        "base_url": args.base_url,
        "server": server_health(args.base_url),
        "model": {key: model_info.get(key) for key in ("base_url", "model", "configured")},
        "usage_note": "tokens are the design loop's estimate (len(output)//4); "
                      "the provider usage is not returned by the current client",
        "freeze": {"before": freeze_before, "after": freeze_after, "ok": frozen_ok,
                   "changed": changed},
        "cases_path": str(cases_path),
        "cases": records,
        "metrics": {
            "mechanism_present": f"{len(composed)}/{len(composed)} (pre-assessed expressible)",
            "ir_compilable": f"{len(compiled)}/{len(composed)}",
            "generated_and_verified": f"{len(verified)}/{len(composed)}",
            "runtime_playable": f"{len(runtime_playable)}/{len(composed)}",
            "browser_playable": (f"{len(browser_playable)}/{len(composed)}"
                                 if args.browser else "not run"),
            "evidence_verified": f"{len(successes)}/{len(composed)}",
        },
        "targets": targets,
        "targets_met": (all(targets.values()) if mode == "real-model" else None),
    }

    (output / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2),
                                        encoding="utf-8")
    _write_summary(output / "summary.md", report)

    print(f"mode {mode} | blind success {len(successes)}/{len(composed)} "
          f"| negatives {len(correct_negatives)}/{len(negatives)} "
          f"| frozen {'ok' if frozen_ok else 'CHANGED: ' + ', '.join(changed)}")
    print("failure classes:", _histogram(records))
    print("report:", output / "report.json")
    if mode == "real-model" and not report["targets_met"]:
        return 1
    return 0


def _output_dir() -> Path:
    revision = git_revision()["head"][:12]
    return ROOT / "artifacts" / "g2" / revision / f"run-{uuid.uuid4().hex[:12]}"


def _write_summary(path: Path, report: dict) -> None:
    lines = [f"# M6 generation acceptance ({report['mode']})", "",
             f"- revision: `{report['revision']['head']}` "
             f"(dirty: `{report['revision']['dirty'] or 'clean'}`)",
             f"- model: `{report['model']['model']}` @ `{report['model']['base_url']}`",
             f"- command: `{report['command']}`",
             f"- python: {report['environment']['python']} · node: {report['environment']['node']}",
             f"- data dir: `{report['data_dir']}` · base url: `{report['base_url']}`",
             f"- server code: `{report['server'].get('code')}`",
             f"- freeze ok: {report['freeze']['ok']} "
             f"(changed: {', '.join(report['freeze']['changed']) or 'none'})",
             f"- started: {report['started_at']} · finished: {report['finished_at']}", "",
             "## Metrics", ""]
    lines += [f"- {name}: {value}" for name, value in report["metrics"].items()]
    lines += ["", "## Targets", ""]
    if report["targets_met"] is None:
        lines.append("- flow-only run: thresholds not evaluated")
    else:
        lines += [f"- {name}: {'PASS' if ok else 'FAIL'}"
                  for name, ok in report["targets"].items()]
    lines += ["", "## Cases", "",
              "| id | expected | outcome | class | turns | actions | version | evidence |",
              "|---|---|---|---|---|---|---|---|"]
    for record in report["cases"]:
        lines.append(f"| {record['id']} | {record['expected']} | {record.get('outcome')} | "
                     f"{record['failure_class']} | {record.get('turns')} | "
                     f"{record.get('actions', '')} | {record.get('version', '')} | "
                     f"{'ok' if record.get('evidence_ok') else 'fail/none'} |")
    lines += ["", "## Frozen fingerprint", ""]
    fail = [name for name, value in report["freeze"]["before"]["files"].items() if value is None]
    lines.append(f"- files: {len(report['freeze']['before']['files'])} "
                 f"(missing: {', '.join(fail) or 'none'})")
    lines.append(f"- prompt: {report['freeze']['before']['prompt']}")
    lines.append(f"- tools: {report['freeze']['before']['tools']}")
    lines.append(f"- cases: {report['freeze']['before']['cases']}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
