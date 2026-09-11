"""Live natural-language acceptance against independent, explicit rule expectations.

Uses the saved credential, never modifies configuration. Results contain no key.
"""
import argparse
import json
from pathlib import Path
from time import perf_counter

from pocker_agent.agent import AgentSession, RuleAgent
from pocker_agent.configuration import ConfigStore, ModelConfig
from pocker_agent.executors import create_engine
from pocker_agent.llm import OpenAICompatibleClient

ROOT = Path(__file__).resolve().parents[1]


def at(data, path):
    for key in path.split("."):
        data = data[key] if isinstance(data, dict) else data[int(key)]
    return data


def verify(case, result):
    if case["kind"] == "unsupported":
        assert result.kind == "unsupported", f"Must explicitly report missing mechanisms, got {result.kind}"
        assert result.rules is None
        return
    assert result.kind == "proposal", f"Expected executable proposal, got {result.kind}: {result.message} {result.errors}"
    body = result.rules.model_dump(mode="json")
    assert body.get("kind", "legacy") == case["kind"], "Wrong game family"
    for path, expected in case.get("expect", {}).items():
        assert at(body, path) == expected, f"{path}: expected {expected!r}, got {at(body,path)!r}"
    for path, expected in case.get("sets", {}).items():
        assert set(at(body, path)) == set(expected), f"{path}: values differ"
    if case["id"] == "high_card":
        assert [a.name for a in result.rules.actions] == ["play"]
        assert result.rules.actions[0].amount == 1
        assert body["deck"]["ranks"] == [*map(str,range(2,11)),"J","Q","K","A"]
    # Every proposed rule runs against the exact same executor used by Web play.
    for seed in (0, 7, 23):
        engine = create_engine(result.rules, seed)
        engine.run()
        assert engine.state.finished
        if case["kind"] == "arithmetic":
            assert engine.state.players[0].score == result.rules.max_rounds


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--case", action="append", dest="cases")
    parser.add_argument("--model")
    parser.add_argument("--timeout", type=float, default=45)
    parser.add_argument("--output", default="artifacts/common-games-live.json")
    args = parser.parse_args()
    cases = json.loads((ROOT / "benchmarks/common_games.json").read_text(encoding="utf-8"))
    if args.cases: cases = [c for c in cases if c["id"] in args.cases]
    config = ConfigStore().read()
    if args.model: config = ModelConfig(config.base_url,args.model,config.api_key)
    client = OpenAICompatibleClient.from_config(config)
    client.timeout_seconds = args.timeout
    complete = client.complete
    traces = []
    def recorded(messages, **kwargs):
        raw = complete(messages, **kwargs)
        traces.append(raw)
        return raw
    client.complete = recorded
    report = {"model":config.model, "base_url":config.base_url, "cases":[]}
    output = ROOT / args.output
    output.parent.mkdir(exist_ok=True, parents=True)
    for case in cases:
        traces.clear()
        print("START",case["id"],config.model,flush=True)
        record = {"id":case["id"],"name":case["name"],"prompt":case["prompt"],"expected":case["kind"]}
        start = perf_counter()
        try:
            result = RuleAgent(client).turn(AgentSession(), case["prompt"])
            record.update(kind=result.kind,message=result.message,errors=result.errors,
                          rules=result.rules.model_dump(mode="json") if result.rules else None)
            verify(case,result)
            record["status"] = "PASS" if case["kind"] != "unsupported" else "CORRECTLY_UNSUPPORTED"
        except (RuntimeError, ValueError, AssertionError, KeyError) as error:
            record.update(status="FAIL",failure=str(error))
        record["seconds"] = round(perf_counter()-start,2)
        record["model_responses"] = list(traces)
        report["cases"].append(record)
        output.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
        print(record["id"],record["status"],record["seconds"],record.get("failure",""),flush=True)
    return int(any(r["status"] == "FAIL" for r in report["cases"]))


if __name__ == "__main__":
    raise SystemExit(main())
